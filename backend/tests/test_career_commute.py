from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_career_prefilter import DATA, OVERVIEW, QA, VIENNA, posting

from lifeos.core.vault import Vault
from lifeos.modules.applications.profile import PERSONAL_FILE
from lifeos.modules.career.commute import CACHE_FILE, Commutes, OrsClient, coord, factor
from lifeos.modules.career.prefilter import FitIndex, Home
from lifeos.modules.career.profiles import OVERVIEW_FILE, PROFILES_DIR
from lifeos.modules.jobsearch.model import Location
from lifeos.modules.jobsearch.router import build_router
from lifeos.modules.jobsearch.store import JobStore

PERSONAL = """name: Someone
homes:
  - {label: Home A, lat: 47.0, lon: 15.5, radius_km: 30, comfort_minutes: 20, max_minutes: 60}
"""
NEAR = Location(city="Graz", lat=47.07, lon=15.44)
FARTHER = Location(city="Weiz", lat=47.2189, lon=15.6247)


def test_factor_is_soft() -> None:
    home = Home("A", 0, 0, comfort_minutes=20, max_minutes=60)
    assert factor(None, home) == 1.0 and factor(15, home) == 1.0
    assert factor(40, home) == pytest.approx(0.75)
    assert factor(60, home) == 0.5 and factor(120, home) == 0.5


class FakeOrs:
    """Answers the matrix API: car 1 minute per km of latitude offset ×100, bike twice."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        profile = request.url.path.rsplit("/", 1)[-1]
        self.calls.append((profile, body))
        assert request.headers["Authorization"] == "KEY"
        src = body["locations"][0]
        per = 1 if profile == "driving-car" else 2
        row = [abs(lat - src[1]) * 100 * 60 * per for _, lat in body["locations"][1:]]
        return httpx.Response(200, json={"durations": [row]})


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    root = tmp_path / "vault"
    for rel, text in {
        f"{PROFILES_DIR}/data/Profile.md": DATA,
        f"{PROFILES_DIR}/qa/Profile.md": QA,
        OVERVIEW_FILE: OVERVIEW,
        PERSONAL_FILE: PERSONAL,
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return Vault.open(root)


@pytest.fixture
def store(vault: Vault) -> JobStore:
    s = JobStore(vault)
    s.add([
        posting("a1", "Data Engineer", ["SQL", "Python"], locs=[NEAR]),
        posting("a2", "Data Engineer", ["SQL", "Python"], locs=[FARTHER]),
        posting("a3", "Analytics Engineer", ["SQL"], locs=[NEAR]),  # same place as a1
        posting("b1", "QA Engineer", ["Playwright"], locs=[VIENNA]),  # outside the radius
    ])  # fmt: skip
    return s


def client(fake: FakeOrs) -> OrsClient:
    return OrsClient("KEY", httpx.Client(transport=httpx.MockTransport(fake)), sleep=lambda s: None)


def test_fill_fetches_each_location_once_per_mode(vault: Vault, store: JobStore) -> None:
    fake = FakeOrs()
    commutes = Commutes(vault)
    done = commutes.fill(client(fake), FitIndex(vault, store), list(store.index()))
    assert done == {"fetched": 2, "failed": 0, "left": 0, "requests": 2}
    assert [p for p, _ in fake.calls] == ["driving-car", "cycling-regular"]
    assert len(fake.calls[0][1]["destinations"]) == 2  # Graz and Weiz, not Vienna
    cache = commutes.read()["Home A"]
    assert cache[coord(47.07, 15.44)] == {"car": 7.0, "bike": 14.0}
    again = commutes.fill(client(fake), FitIndex(vault, store), list(store.index()))
    assert again["fetched"] == 0 and len(fake.calls) == 2  # cached


def test_fill_stops_at_the_request_budget(vault: Vault, store: JobStore) -> None:
    done = Commutes(vault).fill(client(FakeOrs()), FitIndex(vault, store), list(store.index()),
                                max_requests=1)  # fmt: skip
    assert done["fetched"] == 0 and done["left"] == 2


def test_failed_requests_are_reported(vault: Vault, store: JobStore) -> None:
    broken = OrsClient("KEY", httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(403, json={"error": "quota"}))), sleep=lambda s: None)  # fmt: skip
    done = Commutes(vault).fill(broken, FitIndex(vault, store), list(store.index()))
    assert done["failed"] == 2 and not vault.resolve(CACHE_FILE).exists()


def test_annotate_and_sorting_weigh_the_commute(vault: Vault, store: JobStore) -> None:
    fit = FitIndex(vault, store)
    commutes = Commutes(vault)
    commutes.fill(client(FakeOrs()), fit, list(store.index()))
    fits = fit.all(list(store.index()))
    near = commutes.annotate(fits["devjobs:a1"])
    assert near["minutes"] == 7.0 and near["factor"] == 1.0 and not near["tooFar"]
    far = commutes.annotate(fits["devjobs:a2"])  # car 21.9 min: just over comfort (20)
    assert far["minutes"] == pytest.approx(21.9) and far["factor"] < 1.0
    unknown = commutes.annotate(fits["devjobs:b1"])
    assert unknown == {"homes": {}, "minutes": None, "factor": 1.0, "tooFar": False}

    app = FastAPI()
    app.include_router(build_router(store, "UTC", fit, None, commutes))
    api = TestClient(app)
    rows = api.get("/api/jobsearch/postings", params={"sort": "fit"}).json()["items"]
    keys = [r["key"] for r in rows]
    assert keys.index("devjobs:a1") < keys.index("devjobs:a2")  # same fit, shorter commute
    row = next(r for r in rows if r["key"] == "devjobs:a1")
    assert row["commute"]["homes"]["Home A"] == {"car": 7.0, "bike": 14.0, "tooFar": False}
    detail = api.get("/api/jobsearch/postings/devjobs/a2").json()
    assert detail["commute"]["minutes"] == pytest.approx(21.9)
