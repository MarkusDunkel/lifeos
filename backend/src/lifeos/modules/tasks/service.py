"""What the study planner reads from the vault and remembers between runs.

- mirror notes (Moodle, Teams) with their revision, so a run only looks at changed material;
- the Moodle task facts (due dates, submissions) the connector wrote;
- the run log: which notes and facts the last run already handled.

A ticket created for a source carries ``<!-- lifeos:task <source> -->`` in its text, e.g.
``moodle:assign:123`` or ``note:<moodle_id>:<short id>``. The marker lives on GitHub, so
the same task is recognised again in every run.
"""

from __future__ import annotations

import json
import re
from typing import Any

from lifeos.core.extract.text import front_matter
from lifeos.core.vault import INBOX, KNOWLEDGE, STATE, Vault, revision_of

LOG_FILE = f"{STATE}/agents/uni-tasks.json"
FACTS_FILE = f"{INBOX}/moodle/tasks.json"
MIRROR_TYPES = {"moodle-file", "moodle-course"}
MARKER = re.compile(r"<!--\s*lifeos:task\s+(\S+)\s*-->")


class UniTasks:
    def __init__(self, vault: Vault):
        self.vault = vault

    def _json(self, rel: str) -> Any:
        path = self.vault.resolve(rel)
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def mirrors(self, types: set[str] | frozenset[str] = frozenset(MIRROR_TYPES)) -> dict[str, str]:
        """Vault-relative path → revision of every mirror note of the given types
        (default: the Moodle mirrors)."""
        return {rel: rev for rel, (rev, _) in self.mirror_types(types).items()}

    def mirror_types(self, types: set[str] | frozenset[str]) -> dict[str, tuple[str, str]]:
        """Vault-relative path → (revision, note type) of every mirror note of ``types``."""
        root = self.vault.resolve(KNOWLEDGE)
        out: dict[str, tuple[str, str]] = {}
        for path in sorted(root.rglob("*.md")) if root.is_dir() else []:
            if any(part.startswith(".") for part in path.relative_to(root).parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            kind = front_matter(text).get("type")
            if kind in types:
                rel = path.relative_to(self.vault.root).as_posix()
                out[rel] = (revision_of(path) or "", kind)
        return out

    def facts(self) -> list[dict[str, Any]]:
        return (self._json(FACTS_FILE) or {}).get("tasks", [])

    def _log(self) -> dict[str, Any]:
        log = self._json(LOG_FILE) or {}
        log.setdefault("notes", {})
        log.setdefault("facts", {})
        log.setdefault("done_suggested", [])
        return log
