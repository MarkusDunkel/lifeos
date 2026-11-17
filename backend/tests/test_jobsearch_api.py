from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from test_devjobs import ID1, ID2, ID3, Site

from lifeos.connectors.devjobs.client import DevjobsClient
from lifeos.core.auth import hash_password
from lifeos.core.config import Settings
from lifeos.main import create_app
from lifeos.modules.jobsearch.importer import ImportServer, run_import
from lifeos.modules.jobsearch.model import Posting

PASSWORD = "correct horse battery staple"
TOKEN = "t" * 40


def posting(posting_id: str, title: str = "Developer") -> dict:
    return Posting(
        source="devjobs",
        id=posting_id,
        url=f"https://devjobs.at/job/{posting_id}",
        title=title,
        company="Example GmbH",
    ).model_dump(mode="json")


@pytest.fixture
def app_settings(vault_root: Path) -> Settings:
    return Settings(
        vault_root=vault_root,
        session_secret="x" * 40,
        password_hash=hash_password(PASSWORD),
        jobsearch_import_token=TOKEN,
        timezone="Europe/Vienna",
    )


@pytest.fixture
def machine(app_settings: Settings) -> TestClient:
    return TestClient(create_app(app_settings), headers={"Authorization": f"Bearer {TOKEN}"})


@pytest.fixture
def browser(app_settings: Settings) -> TestClient:
    c = TestClient(create_app(app_settings))
    assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return c


def test_token_only_opens_the_import_routes(app_settings: Settings) -> None:
    anon = TestClient(create_app(app_settings))
    assert anon.get("/api/jobsearch/import/status").status_code == 401
    wrong = {"Authorization": "Bearer " + "w" * 40}
    assert anon.get("/api/jobsearch/import/status", headers=wrong).status_code == 401
    good = {"Authorization": f"Bearer {TOKEN}"}
    assert anon.get("/api/jobsearch/import/status", headers=good).status_code == 200
    assert anon.get("/api/jobsearch/status", headers=good).status_code == 401
    assert anon.get("/api/notes", headers=good).status_code == 401


def test_import_disabled_without_token(vault_root: Path) -> None:
    settings = Settings(vault_root=vault_root, session_secret="x" * 40)
    anon = TestClient(create_app(settings))
    r = anon.get("/api/jobsearch/import/status", headers={"Authorization": "Bearer "})
    assert r.status_code == 401


def test_diff_postings_finish_cycle(machine: TestClient, browser: TestClient) -> None:
    r = machine.post("/api/jobsearch/import/diff", json={"source": "devjobs", "ids": [ID1, ID2]})
    assert r.json()["unknown"] == [ID1, ID2]

    r = machine.post("/api/jobsearch/import/postings", json={"postings": [posting(ID1)]})
    assert r.json() == {"stored": 1}
    r = machine.post("/api/jobsearch/import/diff", json={"source": "devjobs", "ids": [ID1, ID2]})
    assert r.json()["unknown"] == [ID2] and r.json()["known"] == 1

    # ID1 disappears from the board: marked as gone, hidden from the list
    machine.post("/api/jobsearch/import/postings", json={"postings": [posting(ID2)]})
    r = machine.post("/api/jobsearch/import/diff", json={"source": "devjobs", "ids": [ID2]})
    assert r.json()["gone"] == 1

    assert machine.get("/api/jobsearch/import/status").json()["doneToday"] is False
    r = machine.post("/api/jobsearch/import/finish", json={"source": "devjobs", "new": 2})
    assert r.json()["doneToday"] is True

    status = browser.get("/api/jobsearch/status").json()
    assert status["counts"]["new"] == 1 and status["counts"]["gone"] == 1
    rows = browser.get("/api/jobsearch/postings").json()
    assert [row["id"] for row in rows] == [ID2]
    assert "description" not in rows[0]


def test_incomplete_import_is_not_done_today(machine: TestClient) -> None:
    machine.post("/api/jobsearch/import/finish", json={"source": "devjobs", "complete": False})
    assert machine.get("/api/jobsearch/import/status").json()["doneToday"] is False


def test_invalid_input_is_rejected(machine: TestClient) -> None:
    bad_id = machine.post("/api/jobsearch/import/diff", json={"source": "devjobs", "ids": ["../x"]})
    assert bad_id.status_code == 400
    bad_source = machine.post("/api/jobsearch/import/diff", json={"source": "../x", "ids": []})
    assert bad_source.status_code == 400
    bad_posting = posting(ID1) | {"id": "../../etc"}
    r = machine.post("/api/jobsearch/import/postings", json={"postings": [bad_posting]})
    assert r.status_code == 422


def test_run_import_end_to_end(machine: TestClient, browser: TestClient) -> None:
    """PC importer against the real API (via the test client) and a fake devjobs.at."""

    def to_app(request: httpx.Request) -> httpx.Response:
        r = machine.request(
            request.method,
            request.url.path,
            content=request.content,
            headers={"Content-Type": "application/json"},
        )
        return httpx.Response(r.status_code, content=r.content)

    server = ImportServer("http://test", TOKEN, transport=httpx.MockTransport(to_app))
    site = Site()
    devjobs = DevjobsClient(httpx.MockTransport(site), sleep=lambda s: None)

    first = run_import(server, devjobs, limit=1)
    assert (first.listed, first.new, first.complete) == (2, 1, False)
    second = run_import(server, devjobs)
    assert (second.new, second.pending, second.complete) == (1, 1, True)
    third = run_import(server, devjobs)
    assert third.new == 0 and third.pending == 0
    assert sum(1 for call in site.calls if call.startswith("/job/")) == 2  # nothing fetched twice
    assert ID3 not in {row["id"] for row in browser.get("/api/jobsearch/postings").json()}
    assert browser.get("/api/jobsearch/status").json()["doneToday"] is True
