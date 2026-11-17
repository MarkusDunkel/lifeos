from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from lifeos.cli import main
from lifeos.connectors.github.model import Ticket, dump
from lifeos.core.vault import Vault
from lifeos.modules.tasks.service import TasksError, UniTasks

REPO = "me/board"
BOARD = f"ready/tickets/{REPO}"
TODAY = date(2026, 10, 1)


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def mirror_note(title: str) -> str:
    return (
        f"---\ntype: moodle-file\ntitle: {title}\nmoodle_id: '1:/{title}.pdf'\n---\n\n# {title}\n"
    )


def fact(
    key: str, due: str | None, submitted: bool | None = False, title: str = "Homework"
) -> dict:
    return {
        "key": key,
        "kind": key.split(":")[1],
        "title": title,
        "due": due,
        "submitted": submitted,
    }


def draft(name: str, marker: str | None, due: str | None = "2026-10-03", **kw) -> str:
    body = f"Text\n\n<!-- lifeos:task {marker} -->" if marker else "Text"
    project = {"Status": kw.pop("status", "Todo")} | ({"Due": due} if due else {})
    ticket = Ticket(
        repo=REPO,
        number=kw.pop("number", None),
        title=kw.pop("title", name),
        labels=kw.pop("labels", ["university"]),
        projects={"Tasks": project},
        body=body,
        url=kw.pop("url", None),
        **kw,
    )
    return dump(ticket)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "vault"
    write(r, "ready/knowledge/Moodle/CA/01 Week 1/Script.md", mirror_note("Script"))
    write(r, "ready/knowledge/Moodle/CA/CA.md", "---\ntype: moodle-course\nmoodle_id: '10'\n---\n")
    write(r, "ready/knowledge/Own note.md", "# not a mirror\n")
    fields = {
        "Status": {"type": "SINGLE_SELECT", "options": ["Todo", "Done"]},
        "Due": {"type": "DATE", "options": []},
    }
    meta = {
        "primary": "Tasks",
        "projects": {"Tasks": {"ref": "me/1", "url": "u", "fields": fields}},
        "labels": {REPO: ["bug", "university"]},
        "synced_at": "2026-10-01T00:00:00Z",
    }
    write(r, ".lifeos/github/meta.json", json.dumps(meta))
    facts = [fact("moodle:assign:60", "2026-10-03T23:59+02:00"), fact("moodle:quiz:70", None)]
    write(r, "inbox/moodle/tasks.json", json.dumps({"tasks": facts}))
    write(r, f"{BOARD}/1-unrelated.md", draft("Unrelated", None, labels=["bug"], number=1, url="u"))
    return r


def tool(root: Path) -> UniTasks:
    return UniTasks(Vault.open(root))


def test_first_input_lists_everything(root: Path) -> None:
    data = tool(root).input(TODAY)
    assert data["first_run"] is True
    assert data["board"]["repo"] == REPO and data["board"]["label_exists"]
    assert data["board"]["has_due_field"] and data["board"]["status_options"] == ["Todo", "Done"]
    assert sorted(data["notes"]["changed"]) == [
        "ready/knowledge/Moodle/CA/01 Week 1/Script.md",
        "ready/knowledge/Moodle/CA/CA.md",
    ]
    assert [f["status"] for f in data["facts"]] == ["new", "new"]
    assert data["untracked_facts"] == ["moodle:assign:60", "moodle:quiz:70"]
    assert data["tickets"] == []  # the unrelated bug ticket is not listed


def test_done_makes_the_next_run_incremental(root: Path) -> None:
    write(root, f"{BOARD}/new-homework-ab12.md", draft("[CA] Homework", "moodle:assign:60"))
    assert tool(root).done()["recorded_notes"] == 2
    data = tool(root).input(TODAY)
    assert data["first_run"] is False
    assert data["notes"] == {"changed": [], "removed": [], "unchanged": 2}
    assert [f["status"] for f in data["facts"]] == ["unchanged", "unchanged"]
    assert data["untracked_facts"] == ["moodle:quiz:70"]
    assert data["hints"] == []
    # A changed slide and a removed note show up again.
    write(root, "ready/knowledge/Moodle/CA/01 Week 1/Script.md", mirror_note("Script") + "new\n")
    (root / "ready/knowledge/Moodle/CA/CA.md").unlink()
    data = tool(root).input(TODAY)
    assert data["notes"]["changed"] == ["ready/knowledge/Moodle/CA/01 Week 1/Script.md"]
    assert data["notes"]["removed"] == ["ready/knowledge/Moodle/CA/CA.md"]


def test_hints_for_changed_due_dates_and_submissions(root: Path) -> None:
    # The issue exists on GitHub already (numbered file, marker kept in the body).
    write(
        root,
        f"{BOARD}/12-homework.md",
        draft("[CA] Homework", "moodle:assign:60", number=12, url="u"),
    )
    tool(root).done()
    facts = [fact("moodle:assign:60", "2026-10-10T23:59+02:00", submitted=True)]
    write(root, "inbox/moodle/tasks.json", json.dumps({"tasks": facts}))
    hints = tool(root).input(TODAY)["hints"]
    assert {
        "type": "due_changed",
        "ticket": f"{BOARD}/12-homework.md",
        "key": "moodle:assign:60",
        "from": "2026-10-03",
        "to": "2026-10-10",
    } in hints
    assert {
        "type": "submitted",
        "ticket": f"{BOARD}/12-homework.md",
        "key": "moodle:assign:60",
    } in hints
    # Once recorded, the submission is not suggested again (e.g. after the user undid Done).
    tool(root).done()
    assert tool(root).input(TODAY)["hints"] == []


def test_user_changes_to_due_are_kept(root: Path) -> None:
    write(
        root,
        f"{BOARD}/12-homework.md",
        draft("[CA] Homework", "moodle:assign:60", number=12, url="u"),
    )
    tool(root).done()
    # The user moved Due by hand; Moodle did not change: no hint.
    write(
        root,
        f"{BOARD}/12-homework.md",
        draft("[CA] Homework", "moodle:assign:60", due="2026-10-01", number=12, url="u"),
    )
    assert tool(root).input(TODAY)["hints"] == []


def test_old_and_submitted_facts_are_not_untracked(root: Path) -> None:
    facts = [
        fact("moodle:assign:1", "2026-09-01T10:00+02:00"),  # more than 14 days ago
        fact("moodle:assign:2", "2026-09-25T10:00+02:00"),  # recent
        fact("moodle:assign:3", "2026-12-01T10:00+01:00", submitted=True),
    ]
    write(root, "inbox/moodle/tasks.json", json.dumps({"tasks": facts}))
    assert tool(root).input(TODAY)["untracked_facts"] == ["moodle:assign:2"]


def test_check_finds_broken_drafts(root: Path) -> None:
    write(root, f"{BOARD}/new-ok-0001.md", draft("[CA] Homework", "moodle:assign:60"))
    write(root, f"{BOARD}/new-dup-0002.md", draft("[CA] Again", "moodle:assign:60"))
    write(root, f"{BOARD}/new-nolabel-0003.md", draft("No label", "note:x:1", labels=[]))
    write(root, f"{BOARD}/new-nomarker-0004.md", draft("No marker", None))
    write(root, f"{BOARD}/new-baddate-0005.md", draft("Bad date", "note:x:2", due="3.10.2026"))
    write(root, f"{BOARD}/new-status-0006.md", draft("Bad status", "note:x:3", status="Later"))
    write(root, f"{BOARD}/new-broken-0007.md", "no frontmatter")
    result = tool(root).check()
    assert not result["ok"]
    text = "\n".join(result["errors"])
    assert "marker moodle:assign:60 is already used by" in text
    assert "new-nolabel-0003.md: label 'university' is missing" in text
    assert "new-broken-0007.md: ticket file has no frontmatter" in text
    assert "new-baddate-0005.md: Due must look like YYYY-MM-DD" in text
    assert "new-status-0006.md: Status 'Later' is not one of Todo, Done" in text
    # A draft without the label or marker is not one of ours unless it has one of them.
    assert "new-nomarker-0004.md: the task marker is missing" in text
    with pytest.raises(TasksError, match="fix the drafts first"):
        tool(root).done()


def test_check_warns_when_the_board_meta_is_stale(root: Path) -> None:
    stale = {"primary": "Tasks", "projects": {"Tasks": {"fields": {}}}, "labels": {REPO: []}}
    write(root, ".lifeos/github/meta.json", json.dumps(stale))
    result = tool(root).check()
    assert result["ok"] and len(result["warnings"]) == 2


def test_cli(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tasks", "input", "--vault", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["board"]["repo"] == REPO
    write(root, f"{BOARD}/new-x-0001.md", "broken")
    assert main(["tasks", "check", "--vault", str(root)]) == 1
    assert main(["tasks", "done", "--vault", str(root)]) == 1
    (root / BOARD / "new-x-0001.md").unlink()
    assert main(["tasks", "done", "--vault", str(root)]) == 0
    assert (root / ".lifeos/agents/uni-tasks.json").is_file()


def test_init_vault_installs_the_command_without_overwriting(tmp_path: Path) -> None:
    from lifeos.core.vault_init import init_vault

    root = tmp_path / "v"
    init_vault(root)
    command = (root / ".claude/commands/uni-tasks.md").read_text(encoding="utf-8")
    assert "lifeos tasks input" in command and "lifeos:task" in command
    settings = json.loads((root / ".claude/settings.json").read_text(encoding="utf-8"))
    assert "Bash(gh:*)" in settings["permissions"]["deny"]
    (root / ".claude/commands/uni-tasks.md").write_text("mine", encoding="utf-8")
    init_vault(root)
    assert (root / ".claude/commands/uni-tasks.md").read_text(encoding="utf-8") == "mine"
