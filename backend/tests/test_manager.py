"""The LifeOS manager: reads boards and control room, the code applies its board actions."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from test_agents import APPS, TASKS
from test_assistant import ASSISTANT, GitHub

from lifeos.agents import comments
from lifeos.agents.claude_code import CallResult
from lifeos.core.vault import Vault
from lifeos.modules.manager.service import AGENT, STATE_FILE, Manager, ManagerResult

NOW = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)
MANAGER = """---
name: lifeos-manager
title: LifeOS-Manager
runner: job
model: m
billing: subscription
language: German
max_open_proposals: 1
---

# LifeOS-Manager

## Regeln

- Look after everything.
"""


def status(level: str = "ok") -> dict[str, Any]:
    process = {"id": "moodle-sync", "label": "Moodle sync", "enabled": True, "level": level,
               "status": "error" if level == "fail" else "ok", "message": "", "history": [],
               "canRequest": True, "inputs": [], "outputs": []}  # fmt: skip
    return {"level": level, "processes": [process], "nodes": [], "system": [], "report": None}


class FakeClaude:
    def __init__(self, result: dict[str, Any], edit=None) -> None:
        self.result = result
        self.edit = edit
        self.calls: list[dict[str, Any]] = []

    def __call__(self, settings, timeout):
        return self

    def call(self, **kw: Any) -> CallResult:
        self.calls.append(kw)
        if self.edit:
            self.edit(kw["workdir"])
        return CallResult(structured=self.result, tokens=90_000,
                          tool_uses=[{"name": "Read", "input": {"file_path": "x"}}])  # fmt: skip


def answer(**kw: Any) -> dict[str, Any]:
    base = dict(summary="Alles geprüft", fixed=[], unblock=[], run_now=[], comments=[],
                create_tickets=[], please_check=[])  # fmt: skip
    return base | kw


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    root = tmp_path / "vault"
    for rel, text in {
        "boards/tasks.md": TASKS,
        "boards/apps.md": APPS,
        "agents/assistant.md": ASSISTANT,
        "agents/lifeos-manager.md": MANAGER,
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return Vault.open(root)


@pytest.fixture
def gh() -> GitHub:
    fake = GitHub()
    fake.add_project(3, ["New", "Todo", "In Progress", "Review", "Done"])
    fake.add_project(4, ["New", "Active", "In Review"])
    for number in (3, 4):
        project = fake.projects[number]
        project["fields"]["AI"] = type(project["fields"]["Status"])(
            f"F{number}_AI", "AI", "SINGLE_SELECT", {"running": "r", "blocked": "b"}, {}
        )
    project = fake.projects[3]
    project["fields"]["Responsible"] = type(project["fields"]["Status"])(
        "F3_R", "Responsible", "SINGLE_SELECT", {"Owner": "o", "AI: KI-Assistent": "a"}, {}
    )
    return fake


def manager(gh, vault, claude, requested=None, commits=None, st=None) -> Manager:
    requested = requested if requested is not None else []
    commits = commits if commits is not None else []
    return Manager(
        vault, lambda: st or status(), lambda: gh, ["someone/3", "someone/4"], "someone/board",
        requested.append, lambda paths, msg: commits.append((paths, msg)),
        claude=claude, clock=lambda: NOW,
    )  # fmt: skip


def url(gh: GitHub, item_id: str) -> str:
    return gh.issues[gh.items[item_id]["issue"]]["url"]


def test_unblocks_a_blocked_ticket_and_reports(gh, vault) -> None:
    item = gh.ticket(3, "Guide: never name the grade", Status="Todo", AI="blocked",
                     Responsible="AI: KI-Assistent")  # fmt: skip
    claude = FakeClaude(answer(unblock=[{"url": url(gh, item), "reason": "billing is set"}]))
    commits: list = []
    status_, message = manager(gh, vault, claude, commits=commits).run()
    assert status_ == "ok", message
    assert "AI" not in gh.values(item)  # cleared: the assistant takes it again
    assert gh.values(item)["Status"] == "Todo"  # the column stays
    body = gh.comments[-1][1]
    assert comments.agent_of(body) == AGENT and "billing is set" in body
    call = claude.calls[0]
    assert call["unrestricted"] and call["workdir"] == vault.root
    picture = json.loads(call["prompt"])
    assert picture["boards"][0]["fields"]["AI"] == "blocked"
    assert picture["controlRoom"]["processes"][0]["id"] == "moodle-sync"
    paths, msg = commits[-1]
    assert "agents/lifeos-manager/runs.md" in paths and msg.startswith(f"agent({AGENT})")
    assert "unblocked #1" in vault.resolve("agents/lifeos-manager/runs.md").read_text("utf-8")


def test_limits_are_checked_by_the_code(gh, vault) -> None:
    running = gh.ticket(3, "Busy", Status="In Progress", AI="running")
    free = gh.ticket(3, "Free", Status="Todo")
    claude = FakeClaude(answer(
        unblock=[{"url": url(gh, running), "reason": "x"}, {"url": url(gh, free), "reason": "x"},
                 {"url": "https://elsewhere/1", "reason": "x"}],
        comments=[{"url": url(gh, running), "text": "hi"}],
        run_now=[{"job": "nope", "reason": "x"}, {"job": "moodle-sync", "reason": "retry"}],
    ))  # fmt: skip
    requested: list = []
    status_, message = manager(gh, vault, claude, requested=requested).run()
    assert gh.values(running)["AI"] == "running"
    assert gh.comments == []
    assert requested == ["moodle-sync"]
    assert message.count("refused") == 5, message


def test_tickets_land_in_new_and_proposals_are_capped(gh, vault) -> None:
    claude = FakeClaude(answer(create_tickets=[
        {"kind": "needs_you", "title": "Turn on the PC", "body": "Teams files are 3 days old."},
        {"kind": "proposal", "title": "Automate X", "body": "…"},
        {"kind": "proposal", "title": "Automate Y", "body": "…"},
    ]))  # fmt: skip
    status_, message = manager(gh, vault, claude).run()
    created = {i["title"]: i for i in gh.issues.values()}
    assert set(created) == {"Turn on the PC", "Automate X"}
    values = {gh.issues[i["issue"]]["title"]: i["values"] for i in gh.items.values()}
    assert values["Turn on the PC"] == {"Status": "New", "Responsible": "Owner"}
    assert values["Automate X"] == {"Status": "New", "Responsible": "AI: KI-Assistent"}
    assert comments.agent_of(created["Automate X"]["body"]) == AGENT
    assert "refused proposal 'Automate Y'" in message


def test_skips_the_model_when_nothing_changed(gh, vault) -> None:
    gh.ticket(3, "Something", Status="Todo")
    claude = FakeClaude(answer())
    m = manager(gh, vault, claude)
    assert m.run()[0] == "ok"
    assert m.run() == ("skipped", "nothing changed")
    assert len(claude.calls) == 1
    # a new problem in the control room is news
    m.status = lambda: status("fail")
    assert m.run()[0] == "ok"
    assert len(claude.calls) == 2
    # once a day it runs anyway
    state = json.loads(vault.resolve(STATE_FILE).read_text("utf-8"))
    state["lastRun"] = (NOW - timedelta(hours=21)).isoformat()
    vault.resolve(STATE_FILE).write_text(json.dumps(state), "utf-8")
    assert m.run()[0] == "ok"
    assert len(claude.calls) == 3


def test_files_it_changes_are_committed(gh, vault) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q", str(vault.root)], check=True)

    def edit(root: Path) -> None:
        (root / "agents" / "assistant.md").write_text(ASSISTANT + "\nfixed\n", "utf-8")

    commits: list = []
    claude = FakeClaude(answer(fixed=["assistant.md: typo"]), edit=edit)
    assert manager(gh, vault, claude, commits=commits).run()[0] == "ok"
    paths, _ = commits[-1]
    assert "agents/assistant.md" in paths


def test_needs_subscription_and_agent_file(gh, vault) -> None:
    claude = FakeClaude(answer())
    path = vault.resolve("agents/lifeos-manager.md")
    path.write_text(MANAGER.replace("billing: subscription", "billing: api"), "utf-8")
    assert manager(gh, vault, claude).run()[0] == "error"
    path.unlink()
    assert manager(gh, vault, claude).run()[0] == "skipped"
    assert claude.calls == []


def test_schema_is_complete() -> None:
    schema = ManagerResult.model_json_schema()
    assert {"unblock", "run_now", "comments", "create_tickets"} <= set(schema["properties"])
