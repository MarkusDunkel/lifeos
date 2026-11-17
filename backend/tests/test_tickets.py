from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import lifeos.main
from lifeos.connectors.github.client import Project, ProjectField, RepoMeta
from lifeos.connectors.github.model import Ticket, dump, load, merge, merge_text
from lifeos.connectors.github.sync import parse_plan, render_plan
from lifeos.core.auth import hash_password
from lifeos.core.config import Settings
from lifeos.main import create_app

PASSWORD = "pw-for-tests"
REPO = "me/board"
TICKETS = "ready/tickets/me/board"
TASKS = "Tasks"  # primary project (P1)
APPS = "Apps"  # second project (P2)
PROJECT_IDS = {TASKS: "P1", APPS: "P2"}


def item(n: int, project: str, status: str | None) -> dict:
    values = [{"name": status, "field": {"name": "Status"}}] if status else []
    return {
        "id": f"PI{n}-{PROJECT_IDS[project]}",
        "project": {"id": PROJECT_IDS[project]},
        "fieldValues": {"nodes": values},
    }


def issue(n: int, title: str, body: str = "", state: str = "OPEN", labels=(), boards=None) -> dict:
    return {
        "id": f"I{n}",
        "number": n,
        "title": title,
        "body": body,
        "state": state,
        "url": f"https://github.com/{REPO}/issues/{n}",
        "updatedAt": "2026-01-01T00:00:00Z",
        "labels": {"nodes": [{"name": x} for x in labels]},
        "assignees": {"nodes": []},
        "projectItems": {"nodes": [item(n, p, s) for p, s in (boards or {}).items()]},
    }


class FakeGitHub:
    """In-memory stand-in for the GitHub API with two boards; records every write."""

    def __init__(self) -> None:
        self.issues_by_number = {
            1: issue(1, "First", "line a\nline b\nline c", labels=["bug"], boards={TASKS: "Todo"}),
            2: issue(2, "Second", "body two", state="CLOSED", boards={TASKS: "Done"}),
            3: issue(3, "Job at X", boards={APPS: "New"}),
        }
        self.writes: list[tuple[str, Any]] = []
        self.clock = 0
        tasks = ProjectField("F1", "Status", "SINGLE_SELECT", {"Todo": "o1", "Done": "o2"})
        apps = ProjectField("F2", "Status", "SINGLE_SELECT", {"New": "o3", "Active": "o4"})
        self._projects = {
            1: Project("P1", TASKS, "https://example.invalid/1", {"Status": tasks}),
            2: Project("P2", APPS, "https://example.invalid/2", {"Status": apps}),
        }
        self._repo = RepoMeta("R1", {"bug": "L1", "docs": "L2"}, {"me": "U1"})

    def touch(self, n: int) -> None:
        self.clock += 1
        self.issues_by_number[n]["updatedAt"] = f"2026-01-02T00:00:{self.clock:02d}Z"

    def _items(self, n: int) -> list[dict]:
        return self.issues_by_number[n]["projectItems"]["nodes"]

    # -- GitHubApi ---------------------------------------------------------------
    def project(self, owner: str, number: int) -> Project:
        return self._projects[number]

    def repo(self, repo: str) -> RepoMeta:
        return self._repo

    def issues(self, repo: str) -> list[dict]:
        return copy.deepcopy(list(self.issues_by_number.values()))

    def issue(self, repo: str, number: int) -> dict:
        return copy.deepcopy(self.issues_by_number[number])

    def update_issue(self, issue_id: str, changes: dict) -> None:
        self.writes.append(("update", changes))
        n = int(issue_id[1:])
        i = self.issues_by_number[n]
        for key in ("title", "body"):
            if key in changes:
                i[key] = changes[key]
        if "state" in changes:
            i["state"] = changes["state"]
        if "labelIds" in changes:
            names = {v: k for k, v in self._repo.labels.items()}
            i["labels"]["nodes"] = [{"name": names[x]} for x in changes["labelIds"]]
        self.touch(n)

    def create_issue(self, repo_id: str, changes: dict) -> dict:
        self.writes.append(("create", changes))
        n = max(self.issues_by_number) + 1
        self.issues_by_number[n] = issue(n, changes["title"], changes.get("body", ""))
        return {"id": f"I{n}", "number": n, "url": "u"}

    def add_to_project(self, project_id: str, content_id: str) -> str:
        self.writes.append(("add", (project_id, content_id)))
        n = int(content_id[1:])
        title = next(t for t, pid in PROJECT_IDS.items() if pid == project_id)
        new = item(n, title, None)
        self._items(n).append(new)
        return new["id"]

    def set_field(self, project_id: str, item_id: str, f: ProjectField, value) -> None:
        self.writes.append(("field", (project_id, f.name, value)))
        n = int(item_id[2:].split("-")[0])
        target = next(i for i in self._items(n) if i["id"] == item_id)
        values = [{"name": value, "field": {"name": f.name}}] if value else []
        target["fieldValues"]["nodes"] = values
        self.touch(n)


@pytest.fixture
def gh() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def api(vault_root: Path, gh: FakeGitHub, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(lifeos.main, "GitHubClient", lambda token: gh)
    settings = Settings(
        vault_root=vault_root,
        session_secret="x" * 40,
        password_hash=hash_password(PASSWORD),
        github_token="test-token",
        github_repos=REPO,
        github_projects="me/1,me/2",
    )
    c = TestClient(create_app(settings))
    assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return c


def sync(api: TestClient) -> dict:
    r = api.post("/api/tickets/sync")
    assert r.status_code == 200, r.text
    return r.json()


def path_of(vault_root: Path, n: int) -> Path:
    return next((vault_root / TICKETS).glob(f"{n}-*.md"))


def edit(api: TestClient, rel: str, **fields) -> dict:
    doc = api.get("/api/tickets/file", params={"path": rel}).json()
    r = api.put(
        "/api/tickets/file", json={"path": rel, "fields": fields, "revision": doc["revision"]}
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_sync_writes_files_inbox_and_meta(api: TestClient, vault_root: Path) -> None:
    report = sync(api)
    assert (report["created"], report["errors"]) == (3, [])
    text = path_of(vault_root, 1).read_text(encoding="utf-8")
    assert path_of(vault_root, 1).name == "1-first.md"
    assert "projects:\n  Tasks:\n    Status: Todo" in text
    t = load(text, REPO)
    assert (t.title, t.state, t.labels) == ("First", "open", ["bug"])
    assert t.projects == {TASKS: {"Status": "Todo"}}
    assert t.body == "line a\nline b\nline c"
    assert (vault_root / "inbox/github/me/board/issues.json").is_file()
    meta = api.get("/api/tickets").json()["meta"]
    assert meta["primary"] == TASKS and list(meta["projects"]) == [TASKS, APPS]
    assert meta["projects"][APPS]["fields"]["Status"]["options"] == ["New", "Active"]
    assert meta["projects"][TASKS]["url"] == "https://example.invalid/1"
    assert meta["labels"][REPO] == ["bug", "docs"]
    data = api.get("/api/tickets").json()
    by_number = {t["number"]: t for t in data["tickets"]}
    assert by_number[3]["projects"] == {APPS: {"Status": "New"}}
    assert all(t["changes"] == [] for t in data["tickets"])
    assert sync(api)["unchanged"] == 3


def test_edit_plan_apply_roundtrip(api: TestClient, gh: FakeGitHub, vault_root: Path) -> None:
    sync(api)
    rel = "me/board/1-first.md"
    saved = edit(
        api,
        rel,
        title="First, renamed",
        labels=["bug", "docs"],
        projects={TASKS: {"Status": "Done"}},
    )
    assert saved["changes"] == ["labels", "project:Tasks:Status", "title"]

    plan = api.post("/api/tickets/plans")
    assert plan.status_code == 200, plan.text
    text = plan.json()["plan"]
    assert "## Update me/board#1: First, renamed" in text and "Projects: Tasks, Apps." in text
    assert "- Tasks · Status: Todo → Done" in text and "- labels: +docs" in text
    assert api.post("/api/tickets/plans").status_code == 409  # one open plan at a time

    applied = api.post(f"/api/tickets/plans/{plan.json()['id']}/apply").json()
    assert applied["status"] == "applied" and "| done |" in applied["result"]
    assert ("update", {"title": "First, renamed", "labelIds": ["L1", "L2"]}) in gh.writes
    assert ("field", ("P1", "Status", "Done")) in gh.writes
    tickets = api.get("/api/tickets").json()["tickets"]
    assert all(t["changes"] == [] for t in tickets)  # synced again after applying
    again = api.post(f"/api/tickets/plans/{plan.json()['id']}/apply")
    assert again.status_code == 409


def test_ticket_can_join_a_second_project(api: TestClient, gh: FakeGitHub) -> None:
    sync(api)
    rel = "me/board/1-first.md"
    saved = edit(api, rel, projects={TASKS: {"Status": "Todo"}, APPS: {"Status": "Active"}})
    assert saved["changes"] == ["project:Apps", "project:Apps:Status"]
    plan = api.post("/api/tickets/plans").json()
    assert "- Add to project: Apps" in plan["plan"]
    assert "- Apps · Status: (empty) → Active" in plan["plan"]
    api.post(f"/api/tickets/plans/{plan['id']}/apply")
    assert ("add", ("P2", "I1")) in gh.writes and ("field", ("P2", "Status", "Active")) in gh.writes
    doc = api.get("/api/tickets/file", params={"path": rel}).json()
    assert doc["projects"] == {TASKS: {"Status": "Todo"}, APPS: {"Status": "Active"}}
    assert doc["changes"] == []


def test_removing_from_a_project_is_refused(api: TestClient) -> None:
    sync(api)
    edit(api, "me/board/3-job-at-x.md", projects={})
    r = api.post("/api/tickets/plans")
    assert r.status_code == 409 and "removing a ticket from Apps" in r.json()["detail"]


def test_flat_project_form_means_the_primary_project(api: TestClient, vault_root: Path) -> None:
    sync(api)
    path = path_of(vault_root, 1)
    old = path.read_text(encoding="utf-8").replace(
        "projects:\n  Tasks:\n    Status: Todo", "project:\n  Status: Todo"
    )
    path.write_text(old, encoding="utf-8")
    doc = api.get("/api/tickets/file", params={"path": "me/board/1-first.md"}).json()
    assert doc["projects"] == {TASKS: {"Status": "Todo"}} and doc["changes"] == []
    assert sync(api)["conflicts"] == []
    assert "projects:\n  Tasks:" in path.read_text(encoding="utf-8")  # rewritten in the new form


def test_remote_and_local_changes_merge(api: TestClient, gh: FakeGitHub, vault_root: Path) -> None:
    sync(api)
    edit(
        api,
        "me/board/1-first.md",
        projects={TASKS: {"Status": "Done"}},
        body="line A\nline b\nline c",
    )
    gh.issues_by_number[1]["title"] = "Renamed on GitHub"
    gh.issues_by_number[1]["body"] = "line a\nline b\nline C"
    gh.touch(1)
    report = sync(api)
    assert report["conflicts"] == []
    t = load(path_of(vault_root, 1).read_text(encoding="utf-8"), REPO)
    assert t.title == "Renamed on GitHub" and t.projects == {TASKS: {"Status": "Done"}}
    assert t.body == "line A\nline b\nline C"
    doc = api.get("/api/tickets/file", params={"path": "me/board/1-first.md"}).json()
    assert doc["changes"] == ["body", "project:Tasks:Status"]  # still to be written back


def test_conflicts_block_plans_until_resolved(api: TestClient, gh: FakeGitHub) -> None:
    sync(api)
    rel = "me/board/1-first.md"
    edit(api, rel, title="Mine", body="line a\nMINE\nline c")
    gh.issues_by_number[1]["title"] = "Theirs"
    gh.issues_by_number[1]["body"] = "line a\nTHEIRS\nline c"
    gh.touch(1)
    assert sync(api)["conflicts"] == [rel]
    doc = api.get("/api/tickets/file", params={"path": rel}).json()
    assert doc["conflicts"] == ["body", "title"] and doc["title"] == "Mine"
    assert "<<<<<<< local" in doc["body"] and doc["base"]["title"] == "Theirs"

    blocked = api.post("/api/tickets/plans")
    assert blocked.status_code == 409 and "resolve the conflicts" in blocked.json()["detail"]

    def resolve(field: str, choice: str) -> Any:
        rev = api.get("/api/tickets/file", params={"path": rel}).json()["revision"]
        body = {"path": rel, "field": field, "choice": choice, "revision": rev}
        return api.post("/api/tickets/resolve", json=body)

    assert resolve("title", "remote").json()["title"] == "Theirs"
    assert resolve("body", "local").status_code == 400  # markers still there
    edit(api, rel, body="line a\nMERGED\nline c")
    resolved = resolve("body", "local").json()
    assert resolved["conflicts"] == [] and resolved["changes"] == ["body"]


def test_project_field_conflict_can_take_github(api: TestClient, gh: FakeGitHub) -> None:
    sync(api)
    rel = "me/board/1-first.md"
    edit(api, rel, projects={TASKS: {"Status": "Done"}})
    gh._items(1)[0]["fieldValues"]["nodes"] = []  # cleared on GitHub at the same time
    gh.issues_by_number[1]["projectItems"]["nodes"][0]["fieldValues"]["nodes"] = [
        {"name": "Todo", "field": {"name": "Status"}}
    ]
    gh.touch(1)
    # GitHub still says Todo (= base), so the local Done stands without a conflict.
    assert sync(api)["conflicts"] == []
    doc = api.get("/api/tickets/file", params={"path": rel}).json()
    assert doc["projects"][TASKS]["Status"] == "Done"


def test_apply_skips_stale_operations(api: TestClient, gh: FakeGitHub, vault_root: Path) -> None:
    sync(api)
    edit(api, "me/board/1-first.md", title="Changed 1")
    edit(api, "me/board/2-second.md", title="Changed 2")
    plan_id = api.post("/api/tickets/plans").json()["id"]
    gh.touch(1)  # issue 1 changes on GitHub after the plan
    p2 = path_of(vault_root, 2)
    p2.write_text(p2.read_text(encoding="utf-8") + "\nedited after plan\n", encoding="utf-8")
    result = api.post(f"/api/tickets/plans/{plan_id}/apply").json()["result"]
    assert result.count("| skipped |") == 2
    assert "changed on GitHub" in result and "file changed" in result
    assert not [w for w in gh.writes if w[0] == "update"]


def test_new_ticket_is_created(api: TestClient, gh: FakeGitHub, vault_root: Path) -> None:
    sync(api)
    new = api.post("/api/tickets/new", json={"repo": REPO, "title": "Brand new"}).json()
    assert new["number"] is None and new["changes"] == ["new"]
    edit(api, new["path"], body="Details", labels=["docs"], projects={TASKS: {"Status": "Todo"}})
    plan = api.post("/api/tickets/plans").json()
    assert "## Create in me/board: Brand new" in plan["plan"]
    assert "- Project: Tasks" in plan["plan"] and "- Tasks · Status: Todo" in plan["plan"]
    api.post(f"/api/tickets/plans/{plan['id']}/apply")
    assert (
        "create",
        {"title": "Brand new", "body": "Details", "labelIds": ["L2"], "assigneeIds": []},
    ) in gh.writes
    assert ("field", ("P1", "Status", "Todo")) in gh.writes
    assert not (vault_root / "ready/tickets" / new["path"]).exists()
    t = load(path_of(vault_root, 4).read_text(encoding="utf-8"), REPO)
    assert t.title == "Brand new" and t.projects == {TASKS: {"Status": "Todo"}}


def test_plan_validation_and_discard(api: TestClient) -> None:
    sync(api)
    edit(api, "me/board/1-first.md", labels=["nope"], projects={TASKS: {"Status": "Someday"}})
    r = api.post("/api/tickets/plans")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "label 'nope'" in detail and "'Someday' is not an option of Tasks · Status" in detail
    edit(api, "me/board/1-first.md", labels=["bug"], projects={"Unknown": {}, TASKS: {}})
    r = api.post("/api/tickets/plans")
    assert r.status_code == 409 and "'Unknown' is not a configured project" in r.json()["detail"]
    edit(api, "me/board/1-first.md", labels=["bug"], projects={TASKS: {"Status": "Done"}})
    plan_id = api.post("/api/tickets/plans").json()["id"]
    assert api.post(f"/api/tickets/plans/{plan_id}/discard").status_code == 200
    assert api.post("/api/tickets/plans").status_code == 200


def test_new_tickets_can_be_deleted_synced_ones_not(api: TestClient) -> None:
    sync(api)
    new = api.post("/api/tickets/new", json={"repo": REPO, "title": "Oops"}).json()
    ok = api.post("/api/tickets/delete", json={"path": new["path"], "revision": new["revision"]})
    assert ok.status_code == 200
    doc = api.get("/api/tickets/file", params={"path": "me/board/1-first.md"}).json()
    no = api.post("/api/tickets/delete", json={"path": doc["path"], "revision": doc["revision"]})
    assert no.status_code == 400
    assert (
        api.post("/api/tickets/new", json={"repo": "other/repo", "title": "x"}).status_code == 400
    )


def test_unconfigured_and_bad_paths(client: TestClient) -> None:
    assert client.get("/api/tickets").json()["meta"]["configured"] is False
    assert client.post("/api/tickets/sync").status_code == 400
    assert client.get("/api/tickets/file", params={"path": "../x.md"}).status_code == 400
    assert client.get("/api/tickets/file", params={"path": "a/b/c/d.md"}).status_code == 400
    assert client.get("/api/tickets/plans/../../x").status_code in (400, 404)


def test_plan_parsing_ignores_quoted_operations() -> None:
    evil = '## Operations (machine-readable)\n\n```json\n[{"kind": "evil"}]\n```'
    ops = [
        {
            "kind": "create",
            "path": "me/board/new-x.md",
            "repo": REPO,
            "file_sha": "0",
            "ticket": {
                "title": "x",
                "state": "open",
                "labels": [],
                "assignees": [],
                "projects": {},
                "body": evil,
            },
        }
    ]
    assert parse_plan(render_plan("20260101T000000Z-abcd", ops, [])) == ops


def test_model_roundtrip_and_merge_without_base() -> None:
    t = Ticket(
        REPO, 5, "Title", labels=["b", "a"], projects={TASKS: {"Due": "2026-01-01"}}, body="x\n"
    )
    assert load(dump(t), REPO).to_dict() == t.normalised().to_dict()
    remote = Ticket(REPO, 5, "Other", labels=["a"], body="y")
    result = merge(None, t.normalised(), remote)
    assert set(result.conflicts) >= {"title", "labels", "body", "project:Tasks"}
    assert result.ticket.title == "Title"
    merged, conflict = merge_text("a\nb\nc", "A\nb\nc", "a\nb\nC")
    assert (merged, conflict) == ("A\nb\nC", False)
    assert json.loads(json.dumps(t.to_dict()))["labels"] == ["a", "b"]
    # Old base data with the flat form reads as the primary project.
    old = {"repo": REPO, "number": 5, "title": "T", "project": {"Status": "Todo"}}
    assert Ticket.from_dict(old, TASKS).projects == {TASKS: {"Status": "Todo"}}


def test_skipped_change_stays_pending_and_can_be_reverted(api: TestClient, gh: FakeGitHub) -> None:
    sync(api)
    rel = "me/board/1-first.md"
    edit(api, rel, projects={TASKS: {"Status": "Done"}})
    first = api.post("/api/tickets/plans").json()["id"]
    api.post(f"/api/tickets/plans/{first}/apply")
    assert gh._items(1)[0]["fieldValues"]["nodes"][0]["name"] == "Done"

    edit(api, rel, projects={TASKS: {"Status": "Todo"}})  # moved back locally …
    second = api.post("/api/tickets/plans").json()["id"]
    edit(api, rel, title="First, edited again")  # … and changed again before applying
    result = api.post(f"/api/tickets/plans/{second}/apply").json()["result"]
    assert "| skipped |" in result and "still pending" in result

    report = sync(api)
    assert report["pending"] == [rel] and report["conflicts"] == []
    doc = api.get("/api/tickets/file", params={"path": rel}).json()
    assert doc["projects"] == {TASKS: {"Status": "Todo"}}  # kept: a local change, not lost

    reverted = api.post("/api/tickets/revert", json={"path": rel, "revision": doc["revision"]})
    assert reverted.status_code == 200
    body = reverted.json()
    assert body["projects"] == {TASKS: {"Status": "Done"}} and body["title"] == "First"
    assert body["changes"] == [] and body["conflicts"] == []
    assert sync(api)["pending"] == []


def test_revert_needs_a_synced_ticket(api: TestClient) -> None:
    sync(api)
    new = api.post("/api/tickets/new", json={"repo": REPO, "title": "Draft"}).json()
    r = api.post("/api/tickets/revert", json={"path": new["path"], "revision": new["revision"]})
    assert r.status_code == 400
    assert sync(api)["pending"] == [new["path"]]


def test_deleted_issue_removes_the_file(api: TestClient, gh: FakeGitHub, vault_root: Path) -> None:
    sync(api)
    del gh.issues_by_number[2]  # deleted on GitHub
    report = sync(api)
    assert report["removed"] == ["me/board/2-second.md"] and report["errors"] == []
    assert not list((vault_root / TICKETS).glob("2-*.md"))
    assert not (vault_root / ".lifeos/github/base/me/board/2.json").exists()


def test_deleted_issue_with_local_changes_is_kept(api: TestClient, gh: FakeGitHub) -> None:
    sync(api)
    rel = "me/board/1-first.md"
    edit(api, rel, title="Mine")
    del gh.issues_by_number[1]
    report = sync(api)
    assert report["removed"] == [] and "#1 is gone on GitHub" in report["errors"][0]
    doc = api.get("/api/tickets/file", params={"path": rel}).json()
    api.post("/api/tickets/revert", json={"path": rel, "revision": doc["revision"]})
    assert sync(api)["removed"] == [rel]  # after reverting, the next sync cleans up
