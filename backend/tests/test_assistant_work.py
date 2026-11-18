"""The assistant works on approved tickets in a copy of the vault; the code applies changes."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from test_agents import FakeGit, mirrored, team
from test_assistant import APPS, ASSISTANT, TASKS, WRITER, GitHub

from lifeos.agents.assistant_work import TaskResult, make_assistant_workflow, writable
from lifeos.agents.claude_code import FILE_TOOLS, CallResult, RateLimit
from lifeos.core.vault import Vault

NOTE = "# Bewerbungen\n\nAlte Notiz.\n"


class FakeClaude:
    """Stands in for Claude Code: changes files in the working directory, then answers."""

    def __init__(self, edit: Callable[[Path], None], result: TaskResult) -> None:
        self.edit = edit
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def __call__(self, settings, timeout):  # used as the factory
        self.timeout = timeout
        return self

    def call(self, **kw: Any) -> CallResult:
        self.calls.append(kw)
        self.edit(kw["workdir"])
        return CallResult(
            structured=self.result.model_dump(),
            tokens=120_000,
            list_usd=0.8,
            rate_limit=RateLimit("allowed", {"five_hour": 0.2}),
        )


def task(**kw: Any) -> TaskResult:
    base = dict(summary="Regel ergänzt", changes=["agents/writer.md: neue Regel"],
                please_check=["Formulierung der Regel"], question="", pull_requests=[])  # fmt: skip
    return TaskResult(**(base | kw))


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    root = tmp_path / "vault"
    for rel, text in {
        "CLAUDE.md": "# Vault rules\n",
        "boards/tasks.md": TASKS,
        "boards/apps.md": APPS,
        "agents/writer.md": WRITER,
        "agents/assistant.md": ASSISTANT,
        "agents/writer/runs.md": "# Runs\n",
        "ready/knowledge/Note.md": NOTE,
        ".lifeos/secret.json": "{}",
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


def approved(gh, vault) -> str:
    mirrored(gh, vault)
    item = gh.ticket(3, "Writer: never name a salary", Status="Todo",
                     Responsible="AI: KI-Assistent")  # fmt: skip
    gh.add_comment("I1", "Bitte auch im Leitfaden erwähnen.")
    return item


def run(gh, vault, claude, git=None) -> list[str]:
    workflow = make_assistant_workflow(claude=claude)
    return team(gh, vault, "tasks", {"assistant": workflow}, git=git).poll()


def test_writable() -> None:
    assert writable("agents/writer.md") and writable("agents/texts.yaml")
    assert writable("boards/apps.md") and writable("ready/knowledge/Career/Guide.md")
    assert not writable("agents/writer/runs.md") and not writable("CLAUDE.md")
    assert not writable(".lifeos/state.json") and not writable("ready/tickets/1.md")


def test_allowed_changes_are_applied_and_the_rest_is_reported(gh, vault) -> None:
    item = approved(gh, vault)

    def edit(work: Path) -> None:
        assert not (work / ".lifeos").exists()  # state and secrets are not copied
        assert (work / "CLAUDE.md").is_file()
        rules = work / "agents/writer.md"
        rules.write_text(rules.read_text(encoding="utf-8") + "- Never name a salary.\n",
                         encoding="utf-8")  # fmt: skip
        (work / "ready/knowledge/Guide.md").write_text("# Guide\n", encoding="utf-8")
        (work / "agents/writer/runs.md").write_text("forged\n", encoding="utf-8")
        (work / "CLAUDE.md").write_text("# changed rules\n", encoding="utf-8")
        (work / "ready/knowledge/Note.md").unlink()

    claude = FakeClaude(edit, task())
    git = FakeGit()
    events = run(gh, vault, claude, git)
    assert events == ["#1 assistant: done – Regel ergänzt"]

    call = claude.calls[0]
    assert call["tools"] == FILE_TOOLS and call["max_turns"] == 60
    assert "Bitte auch im Leitfaden erwähnen." in call["prompt"]
    assert "[owner] Bitte auch" in call["prompt"]

    root = vault.root
    assert "- Never name a salary." in (root / "agents/writer.md").read_text(encoding="utf-8")
    assert (root / "ready/knowledge/Guide.md").is_file()
    assert (root / "agents/writer/runs.md").read_text(encoding="utf-8").startswith("# Runs")
    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == "# Vault rules\n"
    assert (root / "ready/knowledge/Note.md").read_text(encoding="utf-8") == NOTE

    report = gh.comments[-1][1]
    assert "**Done:** Regel ergänzt" in report
    link = "[agents/writer.md](https://github.com/someone/vault/blob/main/agents/writer.md)"
    assert link in report
    assert "`CLAUDE.md`: not a file the assistant may change" in report
    assert "`agents/writer/runs.md`: not a file" in report
    assert "`ready/knowledge/Note.md`: deleted" in report
    assert "120k tokens" in report
    assert gh.values(item) == {"Status": "Review", "Responsible": "AI: KI-Assistent"}
    committed = git.commits[0][0]
    assert {"agents/writer.md", "ready/knowledge/Guide.md"} <= set(committed)


def test_invalid_definition_is_not_applied(gh, vault) -> None:
    approved(gh, vault)

    def edit(work: Path) -> None:
        (work / "agents/writer.md").write_text("---\nname: Wrong Name\n---\n", encoding="utf-8")

    run(gh, vault, FakeClaude(edit, task()))
    assert vault.resolve("agents/writer.md").read_text(encoding="utf-8") == WRITER
    report = gh.comments[-1][1]
    assert "`agents/writer.md`: would no longer be valid" in report
    assert "No file was changed." in report


def test_file_changed_meanwhile_is_not_overwritten(gh, vault) -> None:
    approved(gh, vault)

    def edit(work: Path) -> None:
        (work / "ready/knowledge/Note.md").write_text("# by the assistant\n", encoding="utf-8")
        vault.resolve("ready/knowledge/Note.md").write_text("# by the owner\n", encoding="utf-8")

    run(gh, vault, FakeClaude(edit, task()))
    note = vault.resolve("ready/knowledge/Note.md").read_text(encoding="utf-8")
    assert note == "# by the owner\n"
    assert "changed in the vault in the meantime" in gh.comments[-1][1]


def test_question_blocks_and_applies_nothing(gh, vault) -> None:
    item = approved(gh, vault)

    def edit(work: Path) -> None:
        (work / "ready/knowledge/Note.md").write_text("half done\n", encoding="utf-8")

    events = run(gh, vault, FakeClaude(edit, task(question="Gilt das auch für Praktika?")))
    assert events == ["#1 assistant: blocked – Gilt das auch für Praktika?"]
    assert vault.resolve("ready/knowledge/Note.md").read_text(encoding="utf-8") == NOTE
    assert gh.values(item)["AI"] == "blocked"


def test_api_billing_cannot_work_on_tickets(gh, vault) -> None:
    path = vault.resolve("agents/assistant.md")
    text = path.read_text(encoding="utf-8").replace("billing: subscription", "billing: api")
    path.write_text(text, encoding="utf-8")
    approved(gh, vault)
    events = run(gh, vault, FakeClaude(lambda work: None, task()))
    assert "blocked" in events[0] and "billing: subscription" in gh.comments[-1][1]


# -- access: full ---------------------------------------------------------------------------


def set_access(vault: Vault, access: str) -> None:
    path = vault.resolve("agents/assistant.md")
    billing = "billing: subscription"
    text = path.read_text(encoding="utf-8").replace(billing, f"{billing}\naccess: {access}")
    path.write_text(text, encoding="utf-8")


def full_access(vault: Vault) -> None:
    set_access(vault, "full")
    root = vault.root
    for args in (
        ["init", "-q"],
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-qm", "x"],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True)  # fmt: skip


def test_full_access_works_in_the_vault_and_logs_every_step(gh, vault, monkeypatch) -> None:
    full_access(vault)
    monkeypatch.setenv("LIFEOS_GITHUB_TOKEN", "ghp_secret_value_123")
    vault.resolve("ready/knowledge/Draft.md").write_text("owner's pending edit\n", encoding="utf-8")
    item = approved(gh, vault)

    def edit(work: Path) -> None:
        assert work == vault.root  # no copy: the real vault
        (work / "CLAUDE.md").write_text("# changed rules\n", encoding="utf-8")
        (work / "ready/knowledge/Note.md").unlink()
        (work / "ready/knowledge/New.md").write_text("# New\n", encoding="utf-8")

    claude = FakeClaude(edit, task())
    uses = [{"name": "Bash", "input": {"command": "echo ghp_secret_value_123 && ls"}},
            {"name": "Edit", "input": {"file_path": "CLAUDE.md", "old_string": "x"}}]  # fmt: skip
    original = claude.call

    def call(**kw: Any) -> CallResult:
        result = original(**kw)
        result.tool_uses = uses
        return result

    claude.call = call  # type: ignore[method-assign]
    git = FakeGit()
    events = run(gh, vault, claude, git)
    assert events == ["#1 assistant: done – Regel ergänzt"]
    kw = claude.calls[0]
    assert kw["tools"] == ("default",) and kw["unrestricted"] is True
    assert kw["max_turns"] == 150 and claude.timeout == 45 * 60
    assert "Do not commit or push the vault" in kw["system"]

    committed = git.commits[0][0]
    assert {"CLAUDE.md", "ready/knowledge/Note.md", "ready/knowledge/New.md"} <= set(committed)
    assert "ready/knowledge/Draft.md" not in committed  # pending before the run: not ours
    log_rel = next(p for p in committed if p.startswith("agents/assistant/logs/"))
    log = vault.resolve(log_rel).read_text(encoding="utf-8")
    assert "1. **Bash** – `echo *** && ls`" in log and "ghp_secret_value_123" not in log
    assert "2. **Edit** – `CLAUDE.md`" in log
    report = gh.comments[-1][1]
    assert "**Every step of this run:**" in report and log_rel in report
    assert gh.values(item)["Status"] == "Review"


def test_full_access_question_lists_files_already_changed(gh, vault) -> None:
    full_access(vault)
    approved(gh, vault)

    def edit(work: Path) -> None:
        (work / "ready/knowledge/Note.md").write_text("half\n", encoding="utf-8")

    run(gh, vault, FakeClaude(edit, task(question="Welche Version?")))
    question = gh.comments[-1][1]
    assert "Welche Version?" in question
    assert "already changed these files" in question and "`ready/knowledge/Note.md`" in question


def test_unknown_access_blocks(gh, vault) -> None:
    set_access(vault, "root")
    approved(gh, vault)
    events = run(gh, vault, FakeClaude(lambda work: None, task()))
    assert "blocked" in events[0] and "must be one of sandbox, full" in gh.comments[-1][1]
