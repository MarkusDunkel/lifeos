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


def issue(n: int, title: str, body: str = "", state: str = "OPEN", labels=(), status=None) -> dict:
    items = []
    if status:
        values = [{"name": status, "field": {"name": "Status"}}]
        items = [{"id": f"PI{n}", "project": {"id": "P1"}, "fieldValues": {"nodes": values}}]
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
        "projectItems": {"nodes": items},
    }


class FakeGitHub:
    """In-memory stand-in for the GitHub API; records every write."""

    def __init__(self) -> None:
        self.issues_by_number = {
            1: issue(1, "First", "line a\nline b\nline c", labels=["bug"], status="Todo"),
            2: issue(2, "Second", "body two", state="CLOSED", status="Done"),
        }
        self.writes: list[tuple[str, Any]] = []
        self.clock = 0
        status = ProjectField("F1", "Status", "SINGLE_SELECT", {"Todo": "o1", "Done": "o2"})
        self._project = Project("P1", "Board", "https://example.invalid", {"Status": status})
        self._repo = RepoMeta("R1", {"bug": "L1", "docs": "L2"}, {"me": "U1"})

    def touch(self, n: int) -> None:
        self.clock += 1
        self.issues_by_number[n]["updatedAt"] = f"2026-01-02T00:00:{self.clock:02d}Z"

    # -- GitHubApi ---------------------------------------------------------------
    def project(self, owner: str, number: int) -> Project:
        return self._project

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
        self.writes.append(("add", content_id))
        n = int(content_id[1:])
        item = {"id": f"PI{n}", "project": {"id": "P1"}, "fieldValues": {"nodes": []}}
        self.issues_by_number[n]["projectItems"]["nodes"] = [item]
        return item["id"]

    def set_field(self, project_id: str, item_id: str, f: ProjectField, value) -> None:
        self.writes.append(("field", (f.name, value)))
        n = int(item_id[2:])
        values = [{"name": value, "field": {"name": f.name}}] if value else []
        self.issues_by_number[n]["projectItems"]["nodes"][0]["fieldValues"]["nodes"] = values
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
        github_project="me/1",
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
    assert (report["created"], report["errors"]) == (2, [])
    text = path_of(vault_root, 1).read_text(encoding="utf-8")
    assert path_of(vault_root, 1).name == "1-first.md"
    t = load(text, REPO)
    assert (t.title, t.state, t.labels, t.project) == ("First", "open", ["bug"], {"Status": "Todo"})
    assert t.body == "line a\nline b\nline c"
    assert (vault_root / "inbox/github/me/board/issues.json").is_file()
    data = api.get("/api/tickets").json()
    assert data["meta"]["fields"]["Status"]["options"] == ["Todo", "Done"]
    assert data["meta"]["labels"][REPO] == ["bug", "docs"]
    assert [t["changes"] for t in data["tickets"]] == [[], []]
    assert sync(api)["unchanged"] == 2


def test_edit_plan_apply_roundtrip(api: TestClient, gh: FakeGitHub, vault_root: Path) -> None:
    sync(api)
    rel = "me/board/1-first.md"
    saved = edit(
        api, rel, title="First, renamed", labels=["bug", "docs"], project={"Status": "Done"}
    )
    assert saved["changes"] == ["labels", "project.Status", "title"]

    plan = api.post("/api/tickets/plans")
    assert plan.status_code == 200, plan.text
    text = plan.json()["plan"]
    assert "## Update me/board#1: First, renamed" in text
    assert "- Status: Todo → Done" in text and "- labels: +docs" in text
    assert api.post("/api/tickets/plans").status_code == 409  # one open plan at a time

    applied = api.post(f"/api/tickets/plans/{plan.json()['id']}/apply").json()
    assert applied["status"] == "applied" and "| done |" in applied["result"]
    assert ("update", {"title": "First, renamed", "labelIds": ["L1", "L2"]}) in gh.writes
    assert ("field", ("Status", "Done")) in gh.writes
    tickets = api.get("/api/tickets").json()["tickets"]
    assert all(t["changes"] == [] for t in tickets)  # synced again after applying
    again = api.post(f"/api/tickets/plans/{plan.json()['id']}/apply")
    assert again.status_code == 409


def test_remote_and_local_changes_merge(api: TestClient, gh: FakeGitHub, vault_root: Path) -> None:
    sync(api)
    edit(api, "me/board/1-first.md", project={"Status": "Done"}, body="line A\nline b\nline c")
    gh.issues_by_number[1]["title"] = "Renamed on GitHub"
    gh.issues_by_number[1]["body"] = "line a\nline b\nline C"
    gh.touch(1)
    report = sync(api)
    assert report["conflicts"] == []
    t = load(path_of(vault_root, 1).read_text(encoding="utf-8"), REPO)
    assert t.title == "Renamed on GitHub" and t.project == {"Status": "Done"}
    assert t.body == "line A\nline b\nline C"
    doc = api.get("/api/tickets/file", params={"path": "me/board/1-first.md"}).json()
    assert doc["changes"] == ["body", "project.Status"]  # still to be written back


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
    edit(api, new["path"], body="Details", labels=["docs"], project={"Status": "Todo"})
    plan = api.post("/api/tickets/plans").json()
    assert "## Create in me/board: Brand new" in plan["plan"]
    api.post(f"/api/tickets/plans/{plan['id']}/apply")
    assert (
        "create",
        {"title": "Brand new", "body": "Details", "labelIds": ["L2"], "assigneeIds": []},
    ) in gh.writes
    assert ("field", ("Status", "Todo")) in gh.writes
    assert not (vault_root / "ready/tickets" / new["path"]).exists()
    t = load(path_of(vault_root, 3).read_text(encoding="utf-8"), REPO)
    assert t.title == "Brand new" and t.project == {"Status": "Todo"}


def test_plan_validation_and_discard(api: TestClient) -> None:
    sync(api)
    edit(api, "me/board/1-first.md", labels=["nope"], project={"Status": "Someday"})
    r = api.post("/api/tickets/plans")
    assert r.status_code == 409
    assert (
        "label 'nope'" in r.json()["detail"] and "'Someday' is not an option" in r.json()["detail"]
    )
    edit(api, "me/board/1-first.md", labels=["bug"], project={"Status": "Done"})
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
                "project": {},
                "body": evil,
            },
        }
    ]
    assert parse_plan(render_plan("20260101T000000Z-abcd", ops, None)) == ops


def test_model_roundtrip_and_merge_without_base() -> None:
    t = Ticket(REPO, 5, "Title", labels=["b", "a"], project={"Due": "2026-01-01"}, body="x\n")
    assert load(dump(t), REPO).to_dict() == t.normalised().to_dict()
    remote = Ticket(REPO, 5, "Other", labels=["a"], body="y")
    result = merge(None, t.normalised(), remote)
    assert set(result.conflicts) >= {"title", "labels", "body", "project.Due"}
    assert result.ticket.title == "Title"
    merged, conflict = merge_text("a\nb\nc", "A\nb\nc", "a\nb\nC")
    assert (merged, conflict) == ("A\nb\nC", False)
    assert json.loads(json.dumps(t.to_dict()))["labels"] == ["a", "b"]


def test_skipped_change_stays_pending_and_can_be_reverted(api: TestClient, gh: FakeGitHub) -> None:
    sync(api)
    rel = "me/board/1-first.md"
    edit(api, rel, project={"Status": "Done"})
    first = api.post("/api/tickets/plans").json()["id"]
    api.post(f"/api/tickets/plans/{first}/apply")
    assert (
        gh.issues_by_number[1]["projectItems"]["nodes"][0]["fieldValues"]["nodes"][0]["name"]
        == "Done"
    )

    edit(api, rel, project={"Status": "Todo"})  # moved back locally …
    second = api.post("/api/tickets/plans").json()["id"]
    edit(api, rel, title="First, edited again")  # … and changed again before applying
    result = api.post(f"/api/tickets/plans/{second}/apply").json()["result"]
    assert "| skipped |" in result and "still pending" in result

    report = sync(api)
    assert report["pending"] == [rel] and report["conflicts"] == []
    doc = api.get("/api/tickets/file", params={"path": rel}).json()
    assert doc["project"] == {"Status": "Todo"}  # kept: a local change, not lost

    reverted = api.post("/api/tickets/revert", json={"path": rel, "revision": doc["revision"]})
    assert reverted.status_code == 200
    body = reverted.json()
    assert body["project"] == {"Status": "Done"} and body["title"] == "First"
    assert body["changes"] == [] and body["conflicts"] == []
    assert sync(api)["pending"] == []


def test_revert_needs_a_synced_ticket(api: TestClient) -> None:
    sync(api)
    new = api.post("/api/tickets/new", json={"repo": REPO, "title": "Draft"}).json()
    r = api.post("/api/tickets/revert", json={"path": new["path"], "revision": new["revision"]})
    assert r.status_code == 400
    assert sync(api)["pending"] == [new["path"]]
