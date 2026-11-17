from __future__ import annotations

import json
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
            "title": title,
            "fields": {"Status": ProjectField("F_status", "Status", "SINGLE_SELECT",
                                              {"Todo": "o1", "Done": "o2"})},
        }  # fmt: skip
        self.calls.append("create_project")
        return {"id": f"P{number}", "number": number, "url": "https://example.org/p"}

    def project(self, owner: str, number: int) -> Project:
        p = self.projects[number]
        return Project(p["id"], p["title"], "https://example.org/p", dict(p["fields"]))

    def rename_project(self, project_id: str, title: str) -> None:
        self._project_by_id(project_id)["title"] = title
        self.calls.append("rename_project")

    def rename_field(self, field_id: str, name: str) -> None:
        for p in self.projects.values():
            for old, f in list(p["fields"].items()):
                if f.id == field_id:
                    p["fields"][name] = ProjectField(f.id, name, f.type, f.options)
                    del p["fields"][old]
                    for item in self.items.values():
                        if old in item["values"]:
                            item["values"][name] = item["values"].pop(old)
        self.calls.append("rename_field")

    def rename_label(self, label_id: str, name: str) -> None:
        old = next(k for k, v in self.labels.items() if v == label_id)
        self.labels[name] = self.labels.pop(old)
        self.calls.append("rename_label")

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
        for item in self.items.values():  # like GitHub: replacing options clears values
            item["values"].pop("Status", None)
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
    assert {"Interview round", "Company", "Posting"} <= set(project.fields)
    assert project.title == "LifeOS Applications"
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
    assert values["Status"] == NEW and values["Company"] == "Beispiel GmbH"
    assert values["Posting"].startswith("https://devjobs.at/job/")

    folder = vault.resolve(result["folder"])
    assert folder.name.endswith("Beispiel GmbH – Application Manager")
    assert folder.parent.as_posix().endswith("ready/knowledge/Career/Applications")
    snapshot = (folder / "Posting.md").read_text(encoding="utf-8")
    assert "Betreuung von Anwendungen" in snapshot and "Technologies: SAP" in snapshot
    assert "https://example.org/i/1" in (folder / "Application.md").read_text(encoding="utf-8")
    assert jobs.index()["devjobs:" + "a" * 32]["application"] == "https://example.org/i/1"
    assert apps.list()[0]["issue"] == 1

    with pytest.raises(ApplicationError) as err:
        apps.start("devjobs:" + "a" * 32)
    assert err.value.status == 409


def test_manual_posting(vault, jobs, gh, board) -> None:
    apps = service(vault, jobs, gh, board)
    manual = ManualPosting(company="Firma X", title="IT-Projektleiter", text="Aufgaben …")
    result = apps.start(manual=manual)
    assert result["issue"] == 1 and "Posting" not in gh.items["PI1"]["values"]


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
    assert w.poll() == ["#1 Active: drafted"]
    assert w.poll() == []  # still Active: nothing again
    assert seen == ["Beispiel GmbH – Application Manager (m/w/d)"]

    item = board_items(gh.project_items("x"), w.project.fields)[0]
    w.set_status(item, IN_REVIEW)  # the writer moves it on: no trigger
    assert w.poll() == []
    gh.drag("PI1", ACTIVE)
    assert w.poll() == ["#1 Active: drafted"] and len(seen) == 2


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
    assert w.poll() == ["#1 → Rejected"]
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


def test_setup_board_migrates_the_german_board(gh: FakeGitHub) -> None:
    """The first board had German names; tickets keep their column through the rename."""
    created = gh.create_project("U_me", "Bewerbungen")
    number = created["number"]
    legacy = ["Neu", "Aktiv", "In Review", "Versendet"]
    gh.create_field(created["id"], "Status", "SINGLE_SELECT", legacy)
    gh.create_field(created["id"], "Firma", "TEXT")
    gh.labels["bewerbung"] = "L_bewerbung"
    gh.create_issue("R1", {"title": "X – Y"})
    item_id = gh.add_to_project(created["id"], "I1")
    project = gh.project("me", number)
    gh.set_field(created["id"], item_id, project.fields["Status"], "Aktiv")
    gh.set_field(created["id"], item_id, project.fields["Firma"], "X")

    setup_board(gh, "me/board", f"me/{number}")
    project = gh.project("me", number)
    assert project.title == "LifeOS Applications"
    assert list(project.fields["Status"].options) == COLUMNS
    assert gh.items[item_id]["values"] == {"Status": ACTIVE, "Company": "X"}
    assert "application" in gh.labels and "bewerbung" not in gh.labels


def test_migrate_vault_moves_the_german_layout(tmp_path: Path) -> None:
    from lifeos.modules.applications.migrate import migrate_vault

    root = tmp_path / "vault"
    old = root / "ready/knowledge/Karriere"
    (old / "Lebenslauf").mkdir(parents=True)
    (old / "Lebenslauf/Stammdaten.yaml").write_text("name: A\n", encoding="utf-8")
    (old / "Lebenslauf/Lebenslauf.yaml").write_text("positions: []\n", encoding="utf-8")
    (old / "Lebenslauf/portrait.jpg").write_bytes(b"jpg")
    (old / "Bewerbungs-Leitfaden.md").write_text("# Leitfaden\n", encoding="utf-8")
    (old / "Bewerbungen/Beispiele").mkdir(parents=True)
    (old / "Bewerbungen/Beispiele/APG.md").write_text(
        "---\nfirma: APG\nstelle: Data\n---\n\nText\n", encoding="utf-8"
    )
    app = old / "Bewerbungen/2026-11-17 X – Y"
    app.mkdir()
    (app / "Bewerbung.md").write_text(
        "---\nfirma: X\nstelle: Y\nerstellt: '2026'\n---\n\n# Bewerbung: X – Y\n\n"
        "Inserat: [[Inserat]]\n\n## Verlauf\n\n"
        "- 2026-11-17: zur Bewerbung ausgewählt (Spalte Neu)\n",
        encoding="utf-8",
    )
    (app / "Inserat.md").write_text(
        "---\nfirma: X\nort: Graz\n---\n\n- Technologien: SAP\n", encoding="utf-8"
    )
    state = root / ".lifeos/applications"
    state.mkdir(parents=True)
    folder = "ready/knowledge/Karriere/Bewerbungen/2026-11-17 X – Y"
    (state / "index.json").write_text(json.dumps({"1": {"folder": folder}}), encoding="utf-8")

    moved = migrate_vault(Vault.open(root))
    new = root / "ready/knowledge/Career"
    assert (new / "CV/personal.yaml").read_text(encoding="utf-8") == "name: A\n"
    assert (new / "CV/cv.yaml").is_file() and (new / "CV/portrait.jpg").is_file()
    assert (new / "Application-Guide.md").is_file()
    assert "company: APG" in (new / "Applications/Examples/APG.md").read_text(encoding="utf-8")
    folder_new = new / "Applications/2026-11-17 X – Y"
    application = (folder_new / "Application.md").read_text(encoding="utf-8")
    assert "company: X" in application and "# Application: X – Y" in application
    assert "Posting: [[Posting]]" in application and "selected for an application" in application
    posting = (folder_new / "Posting.md").read_text(encoding="utf-8")
    assert "place: Graz" in posting and "Technologies: SAP" in posting
    index = json.loads((state / "index.json").read_text(encoding="utf-8"))
    assert index["1"]["folder"] == "ready/knowledge/Career/Applications/2026-11-17 X – Y"
    assert not old.exists() and moved
    assert migrate_vault(Vault.open(root)) == []  # safe to run again
