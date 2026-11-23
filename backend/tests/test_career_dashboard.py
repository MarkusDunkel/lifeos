from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_career_commute import FakeOrs
from test_career_commute import client as ors_client
from test_career_matcher import AGENT_FILE, FakeLlm, verdict
from test_career_prefilter import DATA, OVERVIEW, PERSONAL, QA, VIENNA, posting

from lifeos.agents.llm import LlmSettings
from lifeos.core.vault import Vault
from lifeos.modules.applications.profile import MASTER_FILE, PERSONAL_FILE
from lifeos.modules.career.commute import Commutes
from lifeos.modules.career.dashboard import REMOTE, build_dashboard
from lifeos.modules.career.matcher import AGENT, Matcher
from lifeos.modules.career.prefilter import FitIndex
from lifeos.modules.career.profiles import OVERVIEW_FILE, PROFILES_DIR
from lifeos.modules.jobsearch.model import Location
from lifeos.modules.jobsearch.router import build_router
from lifeos.modules.jobsearch.store import JobStore

NOW = datetime(2026, 11, 23, 9, tzinfo=UTC)
LINZ = Location(city="Linz", lat=48.31, lon=14.29)


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    root = tmp_path / "vault"
    for rel, text in {
        f"{PROFILES_DIR}/data/Profile.md": DATA,
        f"{PROFILES_DIR}/qa/Profile.md": QA,
        OVERVIEW_FILE: OVERVIEW,
        PERSONAL_FILE: PERSONAL,
        MASTER_FILE: "positions: []\n",
        f"agents/{AGENT}.md": AGENT_FILE,
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return Vault.open(root)


@pytest.fixture
def store(vault: Vault) -> JobStore:
    s = JobStore(vault)
    new = posting("a1", "Data Engineer", ["SQL", "Python", "Kubernetes"])
    new.date_posted = "2026-11-20"
    older = posting("a2", "Senior Data Engineer", ["SQL", "Python"])
    older.date_posted = "2026-11-12"
    s.add([
        new, older,
        posting("a3", "Data Engineer", ["SQL", "Python"], locs=[VIENNA]),
        posting("a4", "Data Engineer", ["SQL", "Python"], locs=[LINZ]),
        posting("b1", "QA Engineer", ["Playwright", "Python"], locs=[VIENNA]),
        posting("c1", "Office Manager", []),
    ])  # fmt: skip
    return s


def parts(vault: Vault, store: JobStore) -> tuple[FitIndex, Matcher]:
    fit = FitIndex(vault, store)
    return fit, Matcher(vault, store, fit, "UTC", clock=lambda: NOW)


def test_dashboard_without_assessments(vault: Vault, store: JobStore) -> None:
    fit, matcher = parts(vault, store)
    d = build_dashboard(store, fit, matcher, None, now=NOW)
    f = d["funnel"]
    assert (f["active"], f["reach"], f["assessed"]) == (6, 3, 0)
    assert f["passed"] == 2 and f["open"] == 2 and f["daysLeft"] == 1  # daily limit 2
    assert d["quality"] is None
    data = next(x for x in d["demand"] if x["slug"] == "data")
    assert data["good"] == 4 and data["near"] == 2 and data["weight"] == 0.7
    # by the published date; without one (a3, a4) the import date (today) counts
    assert data["newWeek"] == 3 and data["newWeekBefore"] == 1
    assert d["demand"][0]["slug"] == "data"  # most demanded first
    assert d["places"]["columns"][:3] == ["Home A", "Home B", REMOTE]
    assert set(d["places"]["columns"][3:]) == {"Wien", "Linz"}
    row = next(r for r in d["places"]["rows"] if r["slug"] == "data")
    assert row["counts"]["Home A"] == 2 and row["counts"]["Wien"] == 1
    assert row["counts"]["Linz"] == 1
    wanted = {w["skill"]: w for w in d["gaps"]["wanted"]}
    assert wanted["Python"]["level"] == 2 and wanted["Python"]["count"] == 2
    assert wanted["Kubernetes"]["level"] is None and d["gaps"]["basis"] == 2


def test_dashboard_with_assessments_and_commute(vault: Vault, store: JobStore) -> None:
    fit, matcher = parts(vault, store)
    llm = FakeLlm([verdict("apply", 70, ["Snowflake"]), verdict("no", 30, ["snowflake"])])
    matcher.run(lambda agent: llm, LlmSettings())
    commutes = Commutes(vault)
    commutes.fill(ors_client(FakeOrs()), fit, list(store.index()))
    d = build_dashboard(store, fit, matcher, commutes, now=NOW)
    assert d["funnel"]["verdicts"] == {"apply": 1, "maybe": 0, "no": 1}
    assert d["funnel"]["open"] == 0 and d["funnel"]["daysLeft"] == 0
    q = d["quality"]
    assert q["hitRate"] == 0.5 and q["sameProfile"] == 1.0
    assert q["aiMean"] == 50 and q["offset"] == q["prefilterMean"] - 50
    assert q["byProfile"][0]["counts"] == {"apply": 1, "maybe": 0, "no": 1}
    assert q["stale"] == 0 and q["failed"] == 0 and len(q["points"]) == 2
    assert d["gaps"]["missing"] == [{"skill": "Snowflake", "count": 2}]
    assert d["places"]["commuteMedian"]["Home A"] == pytest.approx(7.0)


def test_dashboard_route_caches(vault: Vault, store: JobStore) -> None:
    fit, matcher = parts(vault, store)
    app = FastAPI()
    app.include_router(build_router(store, "UTC", fit, matcher, Commutes(vault)))
    api = TestClient(app)
    first = api.get("/api/jobsearch/dashboard").json()
    assert first["funnel"]["active"] == 6
    store.add([posting("z9", "Platform Engineer", ["SQL"])])
    assert api.get("/api/jobsearch/dashboard").json()["funnel"]["active"] == 6  # cached
    fresh = api.get("/api/jobsearch/dashboard", params={"refresh": True}).json()
    assert fresh["funnel"]["active"] == 7
