from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_career_prefilter import DATA, OVERVIEW, PERSONAL, QA, VIENNA, posting

from lifeos.agents.llm import LlmSettings, Usage
from lifeos.core.vault import Vault
from lifeos.modules.applications.profile import MASTER_FILE, PERSONAL_FILE
from lifeos.modules.career.matcher import (
    AGENT,
    QUEUE_FILE,
    Assessment,
    Matcher,
    ProfileRating,
    where_text,
)
from lifeos.modules.career.prefilter import FitIndex
from lifeos.modules.career.profiles import OVERVIEW_FILE, PROFILES_DIR
from lifeos.modules.jobsearch.router import build_router
from lifeos.modules.jobsearch.store import JobStore

AGENT_FILE = f"""---
name: {AGENT}
title: Job-Matcher
runner: job
daily_limit: 2
manual_limit: 5
max_searches: 2
propose_after: 2
---

## Rules

1. Be honest.
"""


def verdict(rec: str = "apply", fit: int = 80, missing: list[str] | None = None) -> Assessment:
    return Assessment(
        recommendation=rec, fit=fit, best_profile="data", summary="Solid data role.",
        profiles=[ProfileRating(slug="data", fit=fit, reason="SQL and Python")],
        strengths=["SQL"], gaps=["Spark"], red_flags=[], questions=["Team size?"],
        company="ACME builds things.", missing_skills=missing or [],
    )  # fmt: skip


class FakeLlm:
    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = outputs
        self.prompts: list[str] = []
        self.systems: list[str] = []

    def research(self, system, prompt, max_searches, effort):
        self.prompts.append(prompt)
        return "ACME, 200 people.\n\nSources: https://example.org", Usage(0.01, 1)

    def parse(self, system, prompt, schema, effort, images=None):
        self.prompts.append(prompt)
        self.systems.append(system)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out, Usage(0.02, 0)


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
    s.add([
        posting("a1", "Data Engineer", ["SQL", "Python", "Spark"]),
        posting("a2", "Senior Data Engineer", ["SQL", "Python"]),
        posting("a3", "Analytics Engineer", ["SQL"]),
        posting("b2", "QA Engineer", ["Playwright"], locs=[VIENNA]),  # far: does not pass
    ])  # fmt: skip
    return s


def matcher(vault: Vault, store: JobStore) -> Matcher:
    clock = lambda: datetime(2026, 11, 23, 9, tzinfo=UTC)  # noqa: E731
    return Matcher(vault, store, FitIndex(vault, store), "Europe/Vienna", clock=clock)


def test_candidates_pass_the_prefilter_best_first(vault: Vault, store: JobStore) -> None:
    m = matcher(vault, store)
    todo = m.candidates()
    assert todo[0] == "devjobs:a1" and "devjobs:b2" not in todo


def test_run_respects_queue_and_daily_limit(vault: Vault, store: JobStore) -> None:
    m = matcher(vault, store)
    m.enqueue("devjobs:b2")  # asked for by hand although it does not pass
    llm = FakeLlm([verdict("no", 20), verdict(), verdict("maybe", 60)])
    events = m.run(lambda agent: llm, LlmSettings(), per_tick=5)
    # the manual request first, then two automatic ones (daily_limit 2), then it stops
    assert events == ["assessed devjobs:b2 (manual)", "assessed devjobs:a1 (auto)",
                      "assessed devjobs:a2 (auto)"]  # fmt: skip
    assert m.queue() == []
    assert m.status()["autoToday"] == 2 and m.status()["manualToday"] == 1
    assert m.run(lambda agent: FakeLlm([]), LlmSettings()) == []  # limit reached today
    record = m.load(["devjobs:a1"])["devjobs:a1"]
    assert record["assessment"]["recommendation"] == "apply" and record["research"]
    assert "QA Engineer" in llm.prompts[0]  # research sees the (queued) posting
    assert "Location: Home A" in llm.prompts[3]  # the assess prompt names the distance
    assert "Test Automation Engineer" in llm.systems[0]  # active profiles go to the model


def test_a_failed_assessment_is_recorded_and_not_retried(vault: Vault, store: JobStore) -> None:
    m = matcher(vault, store)
    events = m.run(lambda agent: FakeLlm([RuntimeError("boom"), verdict()]), LlmSettings())
    assert events[0] == "devjobs:a1 failed: boom" and events[1] == "assessed devjobs:a2 (auto)"
    assert m.load(["devjobs:a1"])["devjobs:a1"]["error"] == "boom"
    assert "devjobs:a1" not in m.candidates()


def test_assessments_get_stale_when_profiles_change(vault: Vault, store: JobStore) -> None:
    m = matcher(vault, store)
    m.run(lambda agent: FakeLlm([verdict()]), LlmSettings(), per_tick=1)
    assert m.summaries(["devjobs:a1"])["devjobs:a1"]["stale"] is False
    profile = vault.resolve(f"{PROFILES_DIR}/data/Profile.md")
    profile.write_text(DATA + "\n| dbt | Neben | x |\n", encoding="utf-8")
    assert m.summaries(["devjobs:a1"])["devjobs:a1"]["stale"] is True


def test_missing_skills_become_one_proposal(vault: Vault, store: JobStore) -> None:
    m = matcher(vault, store)
    llm = FakeLlm([verdict(missing=["Snowflake", "Python"]), verdict(missing=["snowflake"])])
    m.run(lambda agent: llm, LlmSettings(), per_tick=2)
    missing = m.missing_skills(2)
    assert [s for s, _ in missing] == ["Snowflake"]  # Python is in a profile already
    assert sorted(missing[0][1]) == ["devjobs:a1", "devjobs:a2"]
    m.mark_proposed(["Snowflake"])
    assert m.missing_skills(2) == []


def test_where_text_names_homes_and_remote(vault: Vault, store: JobStore) -> None:
    fits = FitIndex(vault, store).all(["devjobs:a1", "devjobs:b2"])
    assert where_text(fits["devjobs:a1"]).startswith("Home A:")
    assert "outside every radius" in where_text(fits["devjobs:b2"])
    assert where_text(None) == "unknown"


def test_api_sorts_by_recommendation_and_queues(vault: Vault, store: JobStore) -> None:
    m = matcher(vault, store)
    m.run(lambda agent: FakeLlm([verdict("no", 10), verdict("maybe", 70)]), LlmSettings())
    app = FastAPI()
    app.include_router(build_router(store, "UTC", m.fit, m))
    client = TestClient(app)
    rows = client.get("/api/jobsearch/postings", params={"sort": "match"}).json()["items"]
    keys = [r["key"] for r in rows]
    # maybe (a2) first, then not assessed by fit, the "no" (a1) last
    assert keys[0] == "devjobs:a2" and keys[-1] == "devjobs:a1"
    assert rows[0]["assessment"]["recommendation"] == "maybe"
    assessed = client.get("/api/jobsearch/postings", params={"assessed": True}).json()
    assert assessed["total"] == 2
    assert client.get("/api/jobsearch/postings", params={"recommended": True}).json()["total"] == 0
    r = client.post("/api/jobsearch/postings/devjobs/a3/assess")
    assert r.json() == {"queued": True, "position": 1}
    assert vault.resolve(QUEUE_FILE).is_file()
    detail = client.get("/api/jobsearch/postings/devjobs/a2").json()
    assert detail["assessment"]["detail"]["questions"] == ["Team size?"]
    assert client.get("/api/jobsearch/postings/devjobs/a3").json()["queued"] is True
    assert client.post("/api/jobsearch/postings/devjobs/zz/assess").status_code == 404
    status = client.get("/api/jobsearch/matcher").json()
    assert status["dailyLimit"] == 2 and status["queued"] == 1
