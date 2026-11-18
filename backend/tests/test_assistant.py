"""The assistant: the owner's `KI:` comments on any board become tickets in the inbox."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from test_agents import APPS, TASKS, WRITER, FakeGit, FakeGitHub, mirrored

from lifeos.agents import comments
from lifeos.agents.assistant import Assistant, WorkOrder, is_order
from lifeos.agents.config import load_config
from lifeos.agents.llm import Usage
from lifeos.agents.team import Proposal, RunContext, RunResult
from lifeos.core.vault import Vault

ASSISTANT = """---
name: assistant
title: KI-Assistent
runner: workflow
workflow: assistant
board: tasks
billing: subscription
---

# KI-Assistent

## Summary

Turns work orders into tickets.
"""

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
OWNER = "the-owner"


class GitHub(FakeGitHub):
    """Adds comment metadata, the viewer and issue update times to the fake."""

    def __init__(self) -> None:
        super().__init__()
        self.meta: list[dict[str, Any]] = []  # per comment: author, createdAt
        self.clock = NOW

    def add_comment(self, subject_id: str, body: str, author: str = OWNER) -> None:
        super().add_comment(subject_id, body)
        n = len(self.comments)
        when = self.clock.isoformat()
        self.meta.append(
            {"id": f"C{n}", "author": {"login": author}, "createdAt": when, "url": f"https://c/{n}"}
        )
        if subject_id in self.issues:
            self.issues[subject_id]["updatedAt"] = f"{self.clock.isoformat()}#{n}"

    def issue_comments(self, issue_id: str) -> list[dict[str, Any]]:
        return [
            {**meta, "body": body}
            for (subject, body), meta in zip(self.comments, self.meta, strict=True)
            if subject == issue_id
        ]

    def viewer_login(self) -> str:
        return OWNER


class FakeLlm:
    def __init__(self, order: WorkOrder) -> None:
        self.order = order
        self.prompts: list[tuple[str, str]] = []

    def parse(self, system, prompt, schema, effort):
        assert schema is WorkOrder
        self.prompts.append((system, prompt))
        return self.order, Usage(0.0, 0, 52_000, 0.31, {"five_hour": 0.4})


def order(**kw: Any) -> WorkOrder:
    base = dict(understood="Bei englischen Inseraten soll auch der Lebenslauf englisch sein.",
                existing_ticket=None, title="Writer: CV in English for English postings",
                body="**Anlass:** …\n\n**Vorschlag:** …", responsible="assistant",
                current_ticket="Gilt auch hier – beim nächsten Lauf anwenden.")  # fmt: skip
    return WorkOrder(**(base | kw))


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    root = tmp_path / "vault"
    for rel, text in {
        "boards/tasks.md": TASKS,
        "boards/apps.md": APPS,
        "agents/writer.md": WRITER,
        "agents/assistant.md": ASSISTANT,
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return Vault.open(root)


@pytest.fixture
def gh() -> GitHub:
    fake = GitHub()
    fake.add_project(3, ["New", "Todo", "In Progress", "Review", "Done"])
    fake.add_project(4, ["New", "Active", "In Review"])
    return fake


def assistant(gh, vault, llm, git=None) -> Assistant:
    return Assistant(gh, vault, load_config(vault), "someone/board", lambda agent: llm,
                     git=git, clock=lambda: gh.clock)  # fmt: skip


def first_round(gh, vault, llm) -> None:
    """The first round only remembers the current state (no reaction to old comments)."""
    assert assistant(gh, vault, llm).poll() == []
    gh.clock += timedelta(minutes=1)


def test_is_order() -> None:
    assert is_order("KI: mach das") and is_order("  ki : mach das")
    assert not is_order("Bitte KI: mach das") and not is_order("Kino: heute")


def test_ki_comment_becomes_inbox_ticket_and_reply(gh, vault) -> None:
    mirrored(gh, vault)
    gh.ticket(4, "Company – Role", Status="In Review")  # I1 on the applications board
    gh.add_comment("I1", "KI: bei englischen Inseraten den Lebenslauf ganz englisch")  # old
    llm = FakeLlm(order())
    first_round(gh, vault, llm)
    gh.add_comment("I1", "KI: bei englischen Inseraten den Lebenslauf ganz englisch")
    git = FakeGit()
    events = assistant(gh, vault, llm, git).poll()
    assert events == ["#1 assistant: work order done – Writer: CV in English for English postings"]
    assert len(llm.prompts) == 1  # the old comment was ignored

    system, prompt = llm.prompts[0]
    assert "<order>\nbei englischen Inseraten den Lebenslauf ganz englisch\n</order>" in prompt
    assert "Board: Applications" in prompt and "Column: In Review" in prompt
    assert "- writer (Writer, board apps)" in system and "Turns work orders into tickets." in system

    new = gh.issues["I2"]  # the new ticket
    assert new["title"] == "Writer: CV in English for English postings"
    assert comments.agent_of(new["body"]) == "assistant"
    assert "> bei englischen Inseraten den Lebenslauf ganz englisch" in new["body"]
    item = next(i for i in gh.items.values() if i["issue"] == "I2")
    assert item["values"] == {"Status": "New", "Responsible": "AI: KI-Assistent"}

    subject, reply = gh.comments[-1]
    assert subject == "I1" and comments.agent_of(reply) == "assistant"
    assert "**Understood as:** Bei englischen Inseraten" in reply
    link = "[Writer: CV in English for English postings](https://github.com/someone/board/issues/2)"
    assert link in reply
    assert "**For this ticket:** Gilt auch hier" in reply
    assert "52k tokens" in reply
    log = vault.resolve("agents/assistant/runs.md").read_text(encoding="utf-8")
    assert "#1 Company – Role" in log and "Board: Applications" in log
    assert git.commits[0][1].startswith("agent(assistant): #1 work order done")

    gh.clock += timedelta(minutes=1)
    assert assistant(gh, vault, llm).poll() == []  # answered once, not again
    assert len(llm.prompts) == 1


def test_only_the_owners_unmarked_ki_comments_count(gh, vault) -> None:
    mirrored(gh, vault)
    gh.ticket(4, "Company – Role", Status="In Review")
    llm = FakeLlm(order())
    first_round(gh, vault, llm)
    gh.add_comment("I1", "KI: do something", author="stranger")
    gh.add_comment("I1", comments.marker("writer") + "\nKI: quoted by an agent")
    gh.add_comment("I1", "Just feedback, no order.")
    assert assistant(gh, vault, llm).poll() == []
    assert llm.prompts == []


def test_order_on_an_existing_topic_is_added_there(gh, vault) -> None:
    mirrored(gh, vault)
    gh.ticket(3, "Writer: CV in English", Status="New")  # I1, open in the inbox
    gh.ticket(4, "Company – Role", Status="In Review")  # I2
    llm = FakeLlm(order(existing_ticket=1, title="", body="Also the education entries."))
    first_round(gh, vault, llm)
    gh.add_comment("I2", "KI: auch die Ausbildung englisch")
    assistant(gh, vault, llm).poll()
    assert "#1 [New] Writer: CV in English" in llm.prompts[0][1]
    assert len(gh.issues) == 2  # no new ticket
    addition = next(body for subject, body in gh.comments if subject == "I1")
    assert "Also the education entries." in addition
    assert "> auch die Ausbildung englisch" in addition
    assert "Added to the open ticket [#1 Writer: CV in English]" in gh.comments[-1][1]


def test_failure_is_answered_once(gh, vault) -> None:
    mirrored(gh, vault)
    gh.ticket(4, "Company – Role", Status="In Review")

    class Broken:
        def parse(self, *args):
            raise RuntimeError("model unavailable")

    first_round(gh, vault, Broken())
    gh.add_comment("I1", "KI: mach was")
    events = assistant(gh, vault, Broken()).poll()
    assert events == ["#1 assistant: work order error"]
    assert "could not process this work order" in gh.comments[-1][1]
    assert "model unavailable" in gh.comments[-1][1]
    gh.clock += timedelta(minutes=1)
    assert assistant(gh, vault, Broken()).poll() == []


def test_without_assistant_definition_nothing_happens(gh, vault) -> None:
    vault.resolve("agents/assistant.md").unlink()
    mirrored(gh, vault)
    gh.ticket(4, "Company – Role", Status="In Review")
    gh.add_comment("I1", "KI: mach was")
    assert assistant(gh, vault, FakeLlm(order())).poll() == []


def test_agent_proposals_become_inbox_tickets_for_the_assistant(gh, vault) -> None:
    from test_agents import team

    mirrored(gh, vault)
    gh.ticket(4, "Company – Role", Status="Active")

    def write(ctx: RunContext) -> RunResult:
        proposal = Proposal("Writer: never name a salary", "Occasion: …")
        return RunResult(report="Done.", summary="Company", proposals=[proposal])

    team(gh, vault, "apps", {"write": write}, repo="someone/board").poll()
    new = next(i for i in gh.issues.values() if i["title"] == "Writer: never name a salary")
    assert "Proposed by Writer while working on someone/board#1" in new["body"]
    item = next(i for i in gh.items.values() if i["issue"] == new["id"])
    assert item["values"] == {"Status": "New", "Responsible": "AI: KI-Assistent"}
    report = gh.comments[-1][1]
    assert "**Proposals in the inbox**" in report
    assert f"[Writer: never name a salary]({new['url']})" in report
