from __future__ import annotations

import json
from pathlib import Path

from lifeos.core.vault import Vault
from lifeos.modules.tasks.service import LOG_FILE, MARKER, UniTasks


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def tool(tmp_path: Path) -> UniTasks:
    root = tmp_path / "vault"
    note = "---\ntype: moodle-file\ntitle: Script\nmoodle_id: '1:/Script.pdf'\n---\n\n# Script\n"
    write(root, "ready/knowledge/Moodle/CA/01 Week 1/Script.md", note)
    write(root, "ready/knowledge/Moodle/CA/CA.md", "---\ntype: moodle-course\n---\n")
    write(root, "ready/knowledge/Own note.md", "# not a mirror\n")
    write(root, "ready/knowledge/.trash/Old.md", note)
    return UniTasks(Vault.open(root))


def test_mirrors_are_the_notes_of_the_given_types_with_their_revision(tmp_path: Path) -> None:
    tasks = tool(tmp_path)
    mirrors = tasks.mirrors()
    assert set(mirrors) == {
        "ready/knowledge/Moodle/CA/01 Week 1/Script.md",
        "ready/knowledge/Moodle/CA/CA.md",
    }
    assert all(mirrors.values())
    only_files = tasks.mirror_types({"moodle-file"})
    assert list(only_files.values()) == [(mirrors[next(iter(only_files))], "moodle-file")]


def test_facts_and_log_have_defaults_and_read_what_is_there(tmp_path: Path) -> None:
    tasks = tool(tmp_path)
    assert tasks.facts() == []
    assert tasks._log() == {"notes": {}, "facts": {}, "done_suggested": []}
    root = tasks.vault.root
    write(root, "inbox/moodle/tasks.json", json.dumps({"tasks": [{"key": "moodle:assign:60"}]}))
    write(root, LOG_FILE, json.dumps({"notes": {"a.md": "r1"}}))
    assert tasks.facts() == [{"key": "moodle:assign:60"}]
    assert tasks._log() == {"notes": {"a.md": "r1"}, "facts": {}, "done_suggested": []}


def test_marker_is_found_in_a_ticket_text() -> None:
    assert MARKER.findall("Text\n<!-- lifeos:task moodle:assign:60 -->") == ["moodle:assign:60"]
