from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from lifeos.agents.llm import Usage
from lifeos.connectors.github.client import Project, ProjectField, RepoMeta
from lifeos.core.vault import Vault
from lifeos.modules.tasks.planner import STATE_FILE, UniPlanner

NOW = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
COLUMNS = ["New", "Todo", "In Progress", "Done"]
AGENT = (
    "---\nname: uni-planner\ntitle: Studienplaner\nrunner: job\nmodel: m\n"
    "language: German\ndaily_limit: 5\n---\n\n# Studienplaner\n\n## Regeln\n\n- R1\n\n"
    "## Korrekturen\n\n- K1\n"
)


class FakeGitHub:
    def __init__(self) -> None:
        status = ProjectField("F_S", "Status", "SINGLE_SELECT", {c: c for c in COLUMNS}, {})
        self.fields = {
            "Status": status,
            "Due": ProjectField("F_D", "Due", "DATE", {}, {}),
            "Course": ProjectField("F_C", "Course", "TEXT", {}, {}),
        }
        self.issues_: dict[int, dict[str, Any]] = {}
        self.values: dict[str, dict[str, Any]] = {}  # item id -> values
        self.comments: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[str] = []

    def add(self, number: int, title: str, body: str, status: str = "Todo", due=None,
            state: str = "OPEN") -> None:  # fmt: skip
        self.issues_[number] = {"id": f"I{number}", "number": number, "title": title,
                                "body": body, "state": state, "url": f"u/{number}",
                                "updatedAt": "2026-11-19T10:00:00Z",
                                "labels": {"nodes": [{"name": "university"}]}}  # fmt: skip
        self.values[f"IT{number}"] = {"Status": status, "Due": due}

    def _issue_by_id(self, issue_id: str) -> dict[str, Any]:
        return next(i for i in self.issues_.values() if i["id"] == issue_id)

    def project(self, owner: str, number: int) -> Project:
        return Project("P5", "University", "u", dict(self.fields))

    def repo(self, repo: str) -> RepoMeta:
        return RepoMeta("R1", {"university": "L1"}, {})

    def issues(self, repo: str) -> list[dict[str, Any]]:
        out = []
        for n, issue in self.issues_.items():
            vals = self.values.get(f"IT{n}", {})
            nodes = [{"name": v, "field": {"name": k}} if k == "Status" else
                     {"date": v, "field": {"name": k}} if k == "Due" else
                     {"text": v, "field": {"name": k}}
                     for k, v in vals.items() if v is not None]  # fmt: skip
            item = {"id": f"IT{n}", "project": {"id": "P5"}, "fieldValues": {"nodes": nodes}}
            out.append(issue | {"projectItems": {"nodes": [item]}})
        return out

    def issue_comments(self, issue_id: str) -> list[dict[str, Any]]:
        return self.comments.get(issue_id, [])

    def create_issue(self, repo_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        n = max(self.issues_, default=0) + 1
        self.add(n, changes["title"], changes["body"], status="")
        self.values.pop(f"IT{n}")
        self.calls.append(f"create #{n}")
        return {"id": f"I{n}", "number": n, "url": f"u/{n}"}

    def add_to_project(self, project_id: str, content_id: str) -> str:
        n = self._issue_by_id(content_id)["number"]
        self.values.setdefault(f"IT{n}", {})
        return f"IT{n}"

    def set_field(self, project_id: str, item_id: str, f: ProjectField, value) -> None:
        self.values[item_id][f.name] = value
        self.calls.append(f"set {item_id} {f.name}={value}")

    def update_issue(self, issue_id: str, changes: dict[str, Any]) -> None:
        issue = self._issue_by_id(issue_id)
        issue.update(changes)
        issue["updatedAt"] = "2026-11-20T08:00:01Z"
        self.calls.append(f"update {issue['number']} {sorted(changes)}")

    def add_comment(self, issue_id: str, body: str) -> None:
        self.comments.setdefault(issue_id, []).append(
            {"body": body, "createdAt": "2026-11-20T08:00:00Z", "author": {"login": "me"}}
        )
        self.calls.append(f"comment {issue_id}")


class FakeLlm:
    def __init__(self, plan: dict[str, Any]) -> None:
        self.plan = plan
        self.prompts: list[dict[str, Any]] = []
        self.systems: list[str] = []

    def parse(self, system, prompt, schema, effort, images=None):
        self.systems.append(system)
        self.prompts.append(json.loads(prompt))
        return schema.model_validate(self.plan), Usage()


def note(root: Path, rel: str, kind: str, text: str) -> None:
    path = root / "ready" / "knowledge" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntype: {kind}\n---\n\n{text}\n", encoding="utf-8")


@pytest.fixture
def setup(tmp_path: Path):
    root = tmp_path / "vault"
    (root / "agents").mkdir(parents=True)
    (root / "agents" / "uni-planner.md").write_text(AGENT, encoding="utf-8")
    note(root, "Moodle/PCM/PCM.md", "moodle-course", "Projektaufgabe #2 due 27.11.")
    note(root, "Teams/Chats/Gruppe.md", "teams-messages", "Treffen Freitag, Folien fertig?")
    note(root, "Teams/PCM/Plan.md", "folder-file", "Kapitel 6-7")
    note(root, "Planning/Goals.md", "note", "not an input")
    gh = FakeGitHub()
    gh.add(19, "[PCM] Projektaufgabe #2", "text\n\n<!-- lifeos:task moodle:assign:1 -->",
           due="2026-11-27")  # fmt: skip
    vault = Vault.open(root)
    planner = UniPlanner(vault, lambda: gh, "me/board", "me/5", "Europe/Vienna",
                         clock=lambda: NOW)  # fmt: skip
    return vault, gh, planner


def test_creates_changes_comments_and_never_deletes(setup):
    vault, gh, planner = setup
    correction = "Achtung: Abgabe ist am 28.11., 100 % verlässlich"
    gh.comments["I19"] = [{"body": correction, "createdAt": "2026-11-19T09:00:00Z",
                           "author": {"login": "me"}}]  # fmt: skip
    llm = FakeLlm({
        "summary": "1 neu, 1 geändert",
        "create": [{"title": "[PCM] Gruppentreffen vorbereiten", "body": "Folien",
                    "due": "2026-11-21", "course": "PCM", "source": "note:chat:1"},
                   {"title": "dup", "body": "x", "source": "moodle:assign:1"}],
        "update": [{"number": 19, "due": "2026-11-28", "reason": "Korrektur des Besitzers"},
                   {"number": 99, "close": True, "reason": "not ours"}],
        "comment": [{"number": 19, "text": "Hinweis"}],
    })  # fmt: skip
    status, message = planner.run(lambda agent: llm)
    assert status == "ok", message

    prompt = llm.prompts[0]
    assert {n["type"] for n in prompt["notes"]} == {
        "moodle-course",
        "teams-messages",
        "folder-file",
    }
    ticket = prompt["tickets"][0]
    assert ticket["ownerCorrections"][0]["text"].startswith("Achtung")
    assert prompt["rules"] == "- R1" and prompt["corrections"] == "- K1"
    assert "Write comments in German." in llm.systems[0]

    created = gh.issues_[20]
    assert "<!-- lifeos:task note:chat:1 -->" in created["body"]
    assert gh.values["IT20"] == {"Status": "New", "Due": "2026-11-21", "Course": "PCM"}
    assert gh.values["IT19"]["Due"] == "2026-11-28"
    assert not any("dup" in i["title"] for i in gh.issues_.values())  # same source key
    assert 99 not in gh.issues_ and not any("delete" in c for c in gh.calls)
    own = [c["body"] for c in gh.comments["I19"][1:]]
    assert all(c.startswith("<!-- lifeos-agent: uni-planner -->") for c in own)
    assert any("due 2026-11-28" in c for c in own)

    state = json.loads(vault.resolve(STATE_FILE).read_text())
    assert state["considered"] == {"moodle": 1, "teams-messages": 1, "teams-files": 1}
    assert state["pending"] == {} and state["runs"] == {"2026-11-20": 1}

    # nothing new (our own changes do not count): no second model call
    assert planner.run(lambda agent: llm) == ("skipped", "nothing new")
    assert len(llm.prompts) == 1


def test_owner_comment_triggers_a_run(setup):
    vault, gh, planner = setup
    llm = FakeLlm({"summary": "ok"})
    planner.run(lambda agent: llm)
    gh.issues_[19]["updatedAt"] = "2026-11-21T07:00:00Z"
    gh.comments["I19"] = [{"body": "Das ist eine Gruppenarbeit", "createdAt": "x",
                           "author": {"login": "me"}}]  # fmt: skip
    assert planner.run(lambda agent: llm)[0] == "ok"
    assert len(llm.prompts) == 2


def test_close_keeps_markers_and_budget_leaves_rest_pending(setup):
    vault, gh, planner = setup
    big = "x" * 11_000
    for i in range(12):
        note(vault.root, f"Teams/Files/f{i}.md", "folder-file", big)
    change = {"number": 19, "close": True, "body": "neu", "reason": "entfällt laut Teams"}
    llm = FakeLlm({"summary": "s", "update": [change]})
    planner.run(lambda agent: llm)
    issue = gh.issues_[19]
    assert issue["state"] == "CLOSED"
    assert issue["body"].startswith("neu") and "lifeos:task moodle:assign:1" in issue["body"]
    state = json.loads(vault.resolve(STATE_FILE).read_text())
    total = sum(state["considered"].values()) + sum(state["pending"].values())
    assert total == 15 and state["pending"].get("teams-files", 0) > 0
    assert state["considered"]["teams-files"] < 12


def test_skips_without_agent_file(setup):
    vault, gh, planner = setup
    (vault.root / "agents" / "uni-planner.md").unlink()
    assert planner.run(lambda agent: FakeLlm({"summary": ""}))[0] == "skipped"
