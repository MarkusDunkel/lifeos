from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lifeos.core.vault import Vault
from lifeos.modules.applications.profile import PERSONAL_FILE
from lifeos.modules.career.prefilter import (
    CACHE_FILE,
    FitIndex,
    Home,
    Scorer,
    haversine,
    load_homes,
    terms,
)
from lifeos.modules.career.profiles import (
    OVERVIEW_FILE,
    PROFILES_DIR,
    parse_overview,
    parse_profile,
)
from lifeos.modules.jobsearch.model import Location, Posting
from lifeos.modules.jobsearch.router import build_router
from lifeos.modules.jobsearch.store import JobStore

DATA = """# Data Engineer / Data Platform Engineer

## Typische Stellentitel

- Data Engineer
- Analytics Engineer

## Skills

| Skill | Anteil | Begründung |
|---|---|---|
| SQL | Kern | x |
| Python | Kern | x |
| Databricks / Spark | Kern | x |
| Airflow / Dagster | Neben | x |
| ERP-Integration (SAP/Odoo) | Rand | x |
"""

QA = """# Test Automation Engineer

## Typische Stellentitel

- QA Engineer

## Skills

| Skill | Anteil |
|---|---|
| Playwright | Kern |
| Python | Neben |
"""

OVERVIEW = """| Profil | Gewicht % | Status |
|---|---|---|
| `data` | 70 | aktiv |
| `qa` | 30 | aktiv |
| `old` | 0 | verworfen |

| Skill | Stand | Synonyme |
|---|---|---|
| Python | 2 | Python3 |
"""

PERSONAL = """name: Someone
homes:
  - {label: Home A, lat: 47.0, lon: 15.5, radius_km: 30}
  - {label: Home B, lat: 47.9, lon: 15.0}
  - {label: broken}
"""

GRAZ = Location(city="Graz", lat=47.07, lon=15.44)
VIENNA = Location(city="Wien", lat=48.21, lon=16.37)


def posting(pid: str, title: str, skills: list[str], desc: str = "", locs=None) -> Posting:
    return Posting(source="devjobs", id=pid, url=f"https://example.org/{pid}", title=title,
                   company="ACME", skills=skills, description=desc,
                   locations=locs if locs is not None else [GRAZ])  # fmt: skip


def scorer(homes: list[Home] | None = None) -> Scorer:
    profiles = [parse_profile(DATA, "data", "d"), parse_profile(QA, "qa", "q"),
                parse_profile(QA, "old", "o")]  # fmt: skip
    return Scorer(profiles, parse_overview(OVERVIEW), homes if homes is not None else
                  [Home("Home A", 47.0, 15.5, 30)])  # fmt: skip


def test_terms_split_compound_skill_names() -> None:
    assert terms("Databricks / Spark", []) == ["Databricks", "Spark"]
    assert terms("ERP-Integration (SAP/Odoo)", ["SAP"]) == ["ERP-Integration", "SAP", "Odoo"]
    assert terms("CI/CD", []) == ["CI/CD"]


def test_haversine_graz_vienna() -> None:
    assert haversine(47.07, 15.44, 48.21, 16.37) == pytest.approx(145, abs=5)


def test_titles_are_read_from_the_profile() -> None:
    assert parse_profile(DATA, "data", "d").titles == ["Data Engineer", "Analytics Engineer"]


def test_a_matching_posting_fits_its_profile_and_is_near() -> None:
    fit = scorer().score(posting("1", "Senior Data Engineer (m/w/d)", ["SQL", "Python"],
                                 "We use Spark and Airflow."))  # fmt: skip
    assert fit.profile == "data" and fit.profile_title.startswith("Data Engineer")
    data = fit.profiles[0]
    assert data.title_match == 100
    # SQL 3 + Python 3 (listed) + Spark 3×0.7 + Airflow 2×0.7 = 9.5 of 12 points
    assert data.skills == round(100 * 9.5 / 12)
    assert data.matched == ["SQL", "Python", "Databricks / Spark", "Airflow / Dagster"]
    assert [p.slug for p in fit.profiles] == ["data", "qa"]  # the dropped profile is ignored
    assert fit.homes[0].label == "Home A" and fit.homes[0].km < 10
    assert fit.passed


def test_far_postings_do_not_pass_unless_remote() -> None:
    far = scorer().score(posting("2", "Data Engineer", ["SQL", "Python"], locs=[VIENNA]))
    assert not far.homes and far.nearest.label == "Home A" and not far.passed
    remote = scorer().score(posting("3", "Data Engineer", ["SQL", "Python"],
                                    "This role is 100% remote.", locs=[VIENNA]))  # fmt: skip
    assert remote.remote and remote.passed


def test_aliases_and_word_boundaries() -> None:
    fit = scorer().score(posting("4", "QA Engineer", ["Python3"], "Sparkling office"))
    data = next(p for p in fit.profiles if p.slug == "data")
    assert "Python" in data.matched  # alias from the overview
    assert "Databricks / Spark" not in data.matched  # "Sparkling" is not Spark


def test_load_homes_skips_broken_entries(tmp_path: Path) -> None:
    vault = Vault.open(tmp_path)
    path = vault.resolve(PERSONAL_FILE)
    path.parent.mkdir(parents=True)
    path.write_text(PERSONAL, encoding="utf-8")
    assert [(h.label, h.radius_km) for h in load_homes(vault)] == [("Home A", 30), ("Home B", 40)]


# -- cache and API ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    root = tmp_path / "vault"
    files = {
        f"{PROFILES_DIR}/data/Profile.md": DATA,
        f"{PROFILES_DIR}/qa/Profile.md": QA,
        OVERVIEW_FILE: OVERVIEW,
        PERSONAL_FILE: PERSONAL,
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return Vault.open(root)


@pytest.fixture
def store(vault: Vault) -> JobStore:
    s = JobStore(vault)
    s.add([
        posting("a1", "Data Engineer", ["SQL", "Python", "Spark"]),
        posting("b2", "QA Engineer", ["Playwright"], locs=[VIENNA]),
        posting("c3", "Office Manager", []),
    ])  # fmt: skip
    return s


def test_fit_index_caches_until_the_profiles_change(vault: Vault, store: JobStore) -> None:
    index = FitIndex(vault, store)
    keys = list(store.index())
    first = index.all(keys)
    assert first["devjobs:a1"].profile == "data"
    cache = vault.resolve(CACHE_FILE).read_text(encoding="utf-8")
    index.all(keys)
    assert vault.resolve(CACHE_FILE).read_text(encoding="utf-8") == cache
    overview = vault.resolve(OVERVIEW_FILE)
    overview.write_text(OVERVIEW.replace("| `qa` | 30 | aktiv |", "| `qa` | 30 | pausiert |"),
                        encoding="utf-8")  # fmt: skip
    again = index.all(keys)
    assert [p.slug for p in again["devjobs:b2"].profiles] == ["data"]


def test_postings_sorted_and_filtered_by_fit(vault: Vault, store: JobStore) -> None:
    app = FastAPI()
    app.include_router(build_router(store, "UTC", FitIndex(vault, store)))
    client = TestClient(app)
    rows = client.get("/api/jobsearch/postings", params={"sort": "fit"}).json()["items"]
    assert [r["key"] for r in rows][0] == "devjobs:a1"
    assert rows[0]["fit"]["homes"][0]["label"] == "Home A"
    by_qa = client.get("/api/jobsearch/postings", params={"profile": "qa"}).json()["items"]
    assert by_qa[0]["key"] == "devjobs:b2"
    near = client.get("/api/jobsearch/postings", params={"home": "Home A"}).json()
    assert {r["key"] for r in near["items"]} == {"devjobs:a1", "devjobs:c3"}
    passed = client.get("/api/jobsearch/postings", params={"passed": True}).json()
    assert [r["key"] for r in passed["items"]] == ["devjobs:a1"]
    default = client.get("/api/jobsearch/postings").json()["items"]
    assert all("fit" in r for r in default)  # the page gets its fit also without sorting
    options = client.get("/api/jobsearch/fit/options").json()
    assert [p["slug"] for p in options["profiles"]] == ["data", "qa"]
    assert [h["label"] for h in options["homes"]] == ["Home A", "Home B"]
    detail = client.get("/api/jobsearch/postings/devjobs/a1").json()
    assert detail["fit"]["profile"] == "data"
