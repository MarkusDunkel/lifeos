from __future__ import annotations

from pathlib import Path

import pytest
from test_agents import FakeGitHub

from lifeos.agents.config import ConfigError, load_config, parse_contract
from lifeos.agents.mirror import BoardMirror
from lifeos.agents.turn import TURN_LABEL, TurnMirror, turn_of
from lifeos.core.vault import Vault

TASKS = """---
board: tasks
title: Team Tasks
project: someone/3
inbox: New
ai: {pickup: Todo, working: In Progress, review: Review, responsible_field: Responsible}
columns:
  - {name: New, owner: Owner, turn: owner}
  - {name: Todo, owner: responsible, turn: responsible-ai}
  - {name: In Progress, owner: responsible, turn: responsible}
  - {name: Review, owner: Owner, turn: owner}
  - {name: Done, owner: Owner}
fields:
  - {name: Responsible, type: single_select, options: [Owner, "AI: Helper"]}
  - {name: AI, type: single_select, options: [running, blocked]}
views:
  - {name: Board, layout: board, manual: "Group by Status"}
  - {name: Mine, layout: table, filter: "assignee:@me", fields: [Title, Status]}
---

## README

Tasks.
"""


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    root = tmp_path / "vault"
    (root / "boards").mkdir(parents=True)
    (root / "boards/tasks.md").write_text(TASKS, encoding="utf-8")
    return Vault.open(root)


@pytest.fixture
def gh() -> FakeGitHub:
    fake = FakeGitHub()
    fake.add_project(3, ["New", "Todo", "In Progress", "Review", "Done"])
    fake.projects[3]["title"] = "Team Tasks"
    fake.create_field("P3", "Responsible", "SINGLE_SELECT", ["Owner", "AI: Helper"])
    fake.create_field("P3", "AI", "SINGLE_SELECT", ["running", "blocked"])
    return fake


def contract():
    return parse_contract(TASKS, "boards/tasks.md")


@pytest.mark.parametrize(
    ("values", "state", "expected"),
    [
        ({"Status": "New"}, "OPEN", "owner"),
        ({"Status": "Todo", "Responsible": "AI: Helper"}, "OPEN", "ai"),
        ({"Status": "Todo", "Responsible": "Owner"}, "OPEN", "none"),
        ({"Status": "Todo"}, "OPEN", "none"),
        ({"Status": "In Progress", "Responsible": "AI: Helper"}, "OPEN", "ai"),
        ({"Status": "In Progress", "Responsible": "Owner"}, "OPEN", "owner"),
        ({"Status": "In Progress"}, "OPEN", "owner"),
        ({"Status": "Review", "Responsible": "AI: Helper"}, "OPEN", "owner"),
        ({"Status": "Done"}, "OPEN", "none"),
        ({"Status": "Todo", "Responsible": "AI: Helper", "AI": "blocked"}, "OPEN", "owner"),
        ({"Status": "New"}, "CLOSED", "none"),
        ({}, "OPEN", "none"),
    ],
)
def test_turn_of(values, state, expected) -> None:
    assert turn_of(contract(), values, state) == expected


def test_invalid_turn_and_view_are_rejected() -> None:
    with pytest.raises(ConfigError):
        parse_contract(TASKS.replace("turn: owner}", "turn: me}", 1), "boards/tasks.md")
    with pytest.raises(ConfigError):
        parse_contract(TASKS.replace("layout: board", "layout: gantt"), "boards/tasks.md")


def test_turn_mirror_sets_and_clears_only_its_own(gh: FakeGitHub, vault: Vault) -> None:
    cfg = load_config(vault)
    mine = gh.ticket(3, "approve me", Status="New")
    ai = gh.ticket(3, "for the AI", Status="Todo", Responsible="AI: Helper")
    manual = gh.ticket(3, "owner assigned by hand", Status="Done")
    gh.issues[gh.items[manual]["issue"]]["assignees"] = {"nodes": [{"login": "owner"}]}
    turn = TurnMirror(gh, vault.resolve(".lifeos/agents/turn.json"))

    turn.sync(cfg)
    issue = lambda item: gh.issues[gh.items[item]["issue"]]  # noqa: E731
    assert issue(mine)["assignees"]["nodes"] == [{"login": "owner"}]
    assert [n["name"] for n in issue(ai)["labels"]["nodes"]] == [TURN_LABEL]
    assert issue(manual)["assignees"]["nodes"] == [{"login": "owner"}]  # left alone

    calls = len(gh.calls)
    assert turn.sync(cfg) == []  # nothing changed: no writes
    assert len(gh.calls) == calls

    gh.values(mine)["Status"] = "Todo"  # approved, owner's own ticket in Todo: nobody's turn
    gh.values(ai)["Status"] = "Review"  # AI handed over: owner's turn
    turn.sync(cfg)
    assert issue(mine)["assignees"]["nodes"] == []
    assert issue(ai)["labels"]["nodes"] == []
    assert issue(ai)["assignees"]["nodes"] == [{"login": "owner"}]


def test_views_are_created_once_and_owner_told(gh: FakeGitHub, vault: Vault) -> None:
    told = []
    mirror = BoardMirror(gh, vault.resolve(".lifeos/agents/boards.json"),
                         on_views=lambda c, views: told.extend(v.name for v in views))  # fmt: skip
    cfg = load_config(vault)
    gh.views_ = {"P3": [{"id": "V0", "name": "Own view", "layout": "TABLE_LAYOUT"}]}
    mirror.sync(cfg.boards["tasks"], cfg)
    names = [v["name"] for v in gh.views("P3")]
    assert names == ["Own view", "Board", "Mine"]
    assert gh.views("P3")[2]["filter"] == "assignee:@me"
    assert told == ["Board"]

    calls = len(gh.calls)
    mirror.sync(cfg.boards["tasks"], cfg)
    assert [c for c in gh.calls[calls:] if "view" in c] == []
    assert told == ["Board"]


def test_title_is_mirrored(gh: FakeGitHub, vault: Vault) -> None:
    gh.projects[3]["title"] = "Old name"
    cfg = load_config(vault)
    BoardMirror(gh, vault.resolve(".lifeos/agents/boards.json")).sync(cfg.boards["tasks"], cfg)
    assert gh.projects[3]["title"] == "Team Tasks"


def test_turn_is_mirrored_while_the_agent_works(gh: FakeGitHub, vault: Vault) -> None:
    from test_agents import HELPER

    from lifeos.agents.service import run_round
    from lifeos.agents.team import RunResult

    (vault.root / "agents").mkdir()
    (vault.root / "agents/helper.md").write_text(HELPER, encoding="utf-8")
    item = gh.ticket(3, "stuck", Status="In Progress", Responsible="AI: Helper", AI="blocked")
    issue = lambda: gh.issues[gh.items[item]["issue"]]  # noqa: E731
    run_round(gh, vault, {}, "someone/board")
    assert issue()["assignees"]["nodes"] == [{"login": "owner"}]  # blocked: the owner's turn

    seen = []

    def help_(ctx) -> RunResult:
        # in the middle of the run: already the AI's turn on the issue
        seen.append(
            (list(issue()["assignees"]["nodes"]), [n["name"] for n in issue()["labels"]["nodes"]])
        )
        return RunResult(report="done", summary="done")

    gh.values(item).pop("AI")  # the block is lifted
    run_round(gh, vault, {"help": help_}, "someone/board")
    assert seen == [([], [TURN_LABEL])]
    assert issue()["assignees"]["nodes"] == [{"login": "owner"}]  # Review: the owner again
