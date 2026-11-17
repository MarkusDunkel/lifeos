from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import lifeos.main
from lifeos.connectors.github.client import Project, ProjectField, RepoMeta
from lifeos.core.auth import hash_password
from lifeos.core.config import Settings
from lifeos.core.vault import Vault
from lifeos.main import create_app
from lifeos.modules.applications.board import (
    ACTIVE,
    COLUMNS,
    IN_REVIEW,
    LABEL,
    NEW,
    REJECTED,
    SENT,
    BoardWatcher,
    board_items,
    setup_board,
)
from lifeos.modules.applications.service import (
    ApplicationError,
    Applications,
    ManualPosting,
    folder_name,
)
from lifeos.modules.jobsearch.model import Location, Posting
from lifeos.modules.jobsearch.store import JobStore

PASSWORD = "correct horse battery staple"


class FakeGitHub:
    """In-memory GitHub with one repository and Projects v2 boards."""

    def __init__(self) -> None:
        self.labels: dict[str, str] = {}
        self.projects: dict[int, dict[str, Any]] = {}
        self.issues: dict[str, dict[str, Any]] = {}
        self.items: dict[str, dict[str, Any]] = {}  # item id -> {issue, values}
        self.comments: list[tuple[str, str]] = []
        self.linked: list[str] = []
        self.calls: list[str] = []

    # setup
    def owner_id(self, login: str) -> str:
        return f"U_{login}"

    def create_project(self, owner_id: str, title: str) -> dict[str, Any]:
        number = len(self.projects) + 7
        self.projects[number] = {
            "id": f"P{number}",
            "fields": {"Status": ProjectField("F_status", "Status", "SINGLE_SELECT",
                                              {"Todo": "o1", "Done": "o2"})},
        }  # fmt: skip
        self.calls.append("create_project")
        return {"id": f"P{number}", "number": number, "url": "https://example.org/p"}

    def project(self, owner: str, number: int) -> Project:
        p = self.projects[number]
        return Project(p["id"], "Bewerbungen", "https://example.org/p", dict(p["fields"]))

    def _project_by_id(self, project_id: str) -> dict[str, Any]:
        return next(p for p in self.projects.values() if p["id"] == project_id)

    def link_repository(self, project_id: str, repo_id: str) -> None:
        self.linked.append(project_id)

    def create_field(self, project_id: str, name: str, data_type: str, options=None) -> None:
        opts = {o: f"opt-{o}" for o in options or []}
        fields = self._project_by_id(project_id)["fields"]
        fields[name] = ProjectField(f"F_{name}", name, data_type, opts)
        self.calls.append(f"create_field:{name}")

    def set_options(self, field_id: str, options: list[str]) -> None:
        for p in self.projects.values():
            for name, f in p["fields"].items():
                if f.id == field_id:
                    opts = {o: f"opt-{o}" for o in options}
                    p["fields"][name] = ProjectField(f.id, name, f.type, opts)
        self.calls.append("set_options")

    def create_label(self, repo_id: str, name: str, color: str = "") -> str:
        self.labels[name] = f"L_{name}"
        return self.labels[name]

    # repository and issues
    def repo(self, repo: str) -> RepoMeta:
        return RepoMeta("R1", dict(self.labels), {})

    def create_issue(self, repo_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        number = len(self.issues) + 1
        issue = {"id": f"I{number}", "number": number, "url": f"https://example.org/i/{number}"}
        self.issues[issue["id"]] = issue | changes | {"state": "OPEN"}
        return issue

    def update_issue(self, issue_id: str, changes: dict[str, Any]) -> None:
        self.issues[issue_id].update(changes)

    def add_comment(self, subject_id: str, body: str) -> None:
        self.comments.append((subject_id, body))

    # board items
    def add_to_project(self, project_id: str, content_id: str) -> str:
        item_id = f"PI{len(self.items) + 1}"
        self.items[item_id] = {"issue": content_id, "values": {}}
        return item_id

    def set_field(self, project_id: str, item_id: str, f: ProjectField, value: str | None) -> None:
        if f.type == "SINGLE_SELECT":
            assert value in f.options, value
        self.items[item_id]["values"][f.name] = value

    def project_items(self, project_id: str) -> list[dict[str, Any]]:
        nodes = []
        for item_id, item in self.items.items():
            issue = self.issues[item["issue"]]
            values = [
                {"__typename": "Value", "name": v, "field": {"name": k}}
                for k, v in item["values"].items()
            ]
            nodes.append(
                {
                    "id": item_id,
                    "fieldValues": {"nodes": values},
                    "content": {"__typename": "Issue", **issue},
                }
            )
        return nodes

    # test helper: the user drags a card
    def drag(self, item_id: str, status: str) -> None:
        self.items[item_id]["values"]["Status"] = status


@pytest.fixture
def gh() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def board(gh: FakeGitHub) -> str:
    return setup_board(gh, "me/board", None)


def test_setup_board_creates_columns_fields_and_label(gh: FakeGitHub) -> None:
    ref = setup_board(gh, "me/board", None)
    owner, number = ref.split("/")
    project = gh.project(owner, int(number))
    assert list(project.fields["Status"].options) == COLUMNS
    assert {"Gesprächsrunde", "Firma", "Inserat"} <= set(project.fields)
    assert LABEL in gh.labels and gh.linked

    gh.calls.clear()
    assert setup_board(gh, "me/board", ref) == ref  # again: nothing to do
    assert gh.calls == []


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    (tmp_path / "vault").mkdir()
    return Vault.open(tmp_path / "vault")


@pytest.fixture
def jobs(vault: Vault) -> JobStore:
    store = JobStore(vault)
    store.add(
        [
            Posting(
                source="devjobs",
                id="a" * 32,
                url="https://devjobs.at/job/" + "a" * 32,
                title="Application Manager (m/w/d)",
                company="Beispiel GmbH",
                locations=[Location(city="Graz")],
                skills=["SAP"],
                description="## Deine Rolle\n\n- Betreuung von Anwendungen",
            )
        ]
    )
    return store


def service(vault: Vault, jobs: JobStore, gh: FakeGitHub, board: str) -> Applications:
    return Applications(vault, jobs, lambda: gh, "me/board", board, timezone="Europe/Vienna")


def test_start_creates_ticket_folder_and_link(vault, jobs, gh, board) -> None:
    apps = service(vault, jobs, gh, board)
    result = apps.start("devjobs:" + "a" * 32)

    issue = gh.issues["I1"]
    assert issue["title"] == "Beispiel GmbH – Application Manager (m/w/d)"
    assert issue["labelIds"] == [gh.labels[LABEL]]
    values = gh.items["PI1"]["values"]
    assert values["Status"] == NEW and values["Firma"] == "Beispiel GmbH"
    assert values["Inserat"].startswith("https://devjobs.at/job/")

    folder = vault.resolve(result["folder"])
    assert folder.name.endswith("Beispiel GmbH – Application Manager")
    snapshot = (folder / "Inserat.md").read_text(encoding="utf-8")
    assert "Betreuung von Anwendungen" in snapshot and "Technologien: SAP" in snapshot
    assert "https://example.org/i/1" in (folder / "Bewerbung.md").read_text(encoding="utf-8")
    assert jobs.index()["devjobs:" + "a" * 32]["application"] == "https://example.org/i/1"
    assert apps.list()[0]["issue"] == 1

    with pytest.raises(ApplicationError) as err:
        apps.start("devjobs:" + "a" * 32)
    assert err.value.status == 409


def test_manual_posting(vault, jobs, gh, board) -> None:
    apps = service(vault, jobs, gh, board)
    manual = ManualPosting(company="Firma X", title="IT-Projektleiter", text="Aufgaben …")
    result = apps.start(manual=manual)
    assert result["issue"] == 1 and "Inserat" not in gh.items["PI1"]["values"]


def test_start_needs_a_configured_board(vault, jobs) -> None:
    apps = Applications(vault, jobs, None, None, None)
    with pytest.raises(ApplicationError, match="not set up"):
        apps.start("devjobs:x")


def test_folder_names_are_safe() -> None:
    assert folder_name("2026-11-20", "A/B GmbH", 'C: "D" (m/w/d)') == "2026-11-20 A B GmbH – C D"


# -- the watcher ----------------------------------------------------------------------


def watcher(gh: FakeGitHub, board: str, tmp_path: Path, handlers=None) -> BoardWatcher:
    owner, number = board.split("/")
    return BoardWatcher(gh, gh.project(owner, int(number)), tmp_path / "board.json", handlers)


def test_handler_runs_once_per_arrival(vault, jobs, gh, board, tmp_path) -> None:
    service(vault, jobs, gh, board).start("devjobs:" + "a" * 32)
    seen: list[str] = []
    w = watcher(gh, board, tmp_path, {ACTIVE: lambda item: seen.append(item.title) or "drafted"})

    assert w.poll() == []  # first sight in "Neu": just remembered
    gh.drag("PI1", ACTIVE)
    assert w.poll() == ["#1 Aktiv: drafted"]
    assert w.poll() == []  # still Aktiv: nothing again
    assert seen == ["Beispiel GmbH – Application Manager (m/w/d)"]

    item = board_items(gh.project_items("x"), w.project.fields)[0]
    w.set_status(item, IN_REVIEW)  # the writer moves it on: no trigger
    assert w.poll() == []
    gh.drag("PI1", ACTIVE)
    assert w.poll() == ["#1 Aktiv: drafted"] and len(seen) == 2


def test_new_item_already_active_triggers(vault, jobs, gh, board, tmp_path) -> None:
    service(vault, jobs, gh, board).start("devjobs:" + "a" * 32)
    gh.drag("PI1", ACTIVE)
    calls: list[int] = []
    w = watcher(gh, board, tmp_path, {ACTIVE: lambda item: calls.append(1) and None})
    w.poll()
    assert calls == [1]


def test_closing_columns_close_and_reopen(vault, jobs, gh, board, tmp_path) -> None:
    service(vault, jobs, gh, board).start("devjobs:" + "a" * 32)
    w = watcher(gh, board, tmp_path)
    w.poll()
    gh.drag("PI1", REJECTED)
    assert w.poll() == ["#1 → Absage erhalten"]
    assert gh.issues["I1"]["state"] == "CLOSED"
    gh.drag("PI1", SENT)
    w.poll()
    assert gh.issues["I1"]["state"] == "OPEN"


def test_failing_handler_comments_and_does_not_repeat(vault, jobs, gh, board, tmp_path) -> None:
    service(vault, jobs, gh, board).start("devjobs:" + "a" * 32)
    calls: list[int] = []

    def broken(item):
        calls.append(1)
        raise RuntimeError("model unavailable")

    w = watcher(gh, board, tmp_path, {ACTIVE: broken})
    w.poll()
    gh.drag("PI1", ACTIVE)
    assert "failed" in w.poll()[0]
    assert gh.comments and "model unavailable" in gh.comments[0][1]
    assert w.poll() == [] and calls == [1]


# -- API ----------------------------------------------------------------------------------


def test_apply_via_api_and_filter_on_jobs_page(vault, jobs, gh, board, monkeypatch) -> None:
    monkeypatch.setattr(lifeos.main, "GitHubClient", lambda token: gh)
    settings = Settings(
        vault_root=vault.root,
        session_secret="x" * 40,
        password_hash=hash_password(PASSWORD),
        github_token="t",
        github_repos="me/board",
        applications_project=board,
    )
    client = TestClient(create_app(settings))
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200

    key = "devjobs:" + "a" * 32
    r = client.post("/api/applications", json={"posting": key})
    assert r.status_code == 200 and r.json()["issue"] == 1
    assert client.post("/api/applications", json={"posting": key}).status_code == 409
    assert client.get("/api/applications").json()["items"][0]["company"] == "Beispiel GmbH"

    applied = client.get("/api/jobsearch/postings?applied=true").json()
    assert [row["application"] for row in applied["items"]] == ["https://example.org/i/1"]
