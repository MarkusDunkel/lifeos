"""Deterministic helpers for the AI task run (``/uni-tasks`` in Claude Code).

The AI decides *what* is a task; these helpers make the run incremental and safe:

- ``input``  what changed since the last run (mirror notes, Moodle task facts), the existing
             tickets with their source markers, and hints (due date changed, submitted).
- ``check``  validates the ticket drafts before you review them.
- ``done``   records what was processed, so the next run only looks at new material.

A ticket created for a source carries ``<!-- lifeos:task <source> -->`` in its text, e.g.
``moodle:assign:123`` or ``note:<moodle_id>:<short id>``. The marker survives on GitHub,
so the same task is recognised after the issue was created.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from lifeos.connectors.github.model import load, norm_value
from lifeos.core.extract.text import front_matter
from lifeos.core.vault import INBOX, KNOWLEDGE, STATE, TICKETS, Vault, revision_of, write_atomic

LOG_FILE = f"{STATE}/agents/uni-tasks.json"
FACTS_FILE = f"{INBOX}/moodle/tasks.json"
META_FILE = f"{STATE}/github/meta.json"
MIRROR_TYPES = {"moodle-file", "moodle-course"}
MARKER = re.compile(r"<!--\s*lifeos:task\s+(\S+)\s*-->")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RECENT_DAYS = 14


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class TasksError(Exception):
    pass


class UniTasks:
    def __init__(self, vault: Vault, repo: str | None = None, label: str = "university"):
        self.vault = vault
        self.label = label
        self.meta = self._json(META_FILE) or {}
        self.repo = repo or self._default_repo()

    # -- files -------------------------------------------------------------------

    def _json(self, rel: str) -> Any:
        path = self.vault.resolve(rel)
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def _default_repo(self) -> str:
        repos = list((self.meta.get("labels") or {}).keys())
        if repos:
            return repos[0]
        root = self.vault.resolve(TICKETS)
        found = (
            sorted(
                f"{o.name}/{n.name}"
                for o in root.iterdir()
                if o.is_dir()
                for n in o.iterdir()
                if n.is_dir()
            )
            if root.is_dir()
            else []
        )
        if not found:
            raise TasksError("no ticket repository found: run a ticket sync in LifeOS first")
        return found[0]

    @property
    def draft_dir(self) -> str:
        return f"{TICKETS}/{self.repo}"

    def board(self) -> dict[str, Any]:
        fields = self.meta.get("fields") or {}
        status = fields.get("Status") or {}
        return {
            "repo": self.repo,
            "draft_dir": self.draft_dir,
            "draft_name": f"{self.draft_dir}/new-<slug>-<4 hex digits>.md",
            "label": self.label,
            "label_exists": self.label in (self.meta.get("labels") or {}).get(self.repo, []),
            "status_options": status.get("options") or [],
            "has_due_field": "Due" in fields,
            "meta_synced_at": self.meta.get("synced_at"),
        }

    def mirrors(self) -> dict[str, str]:
        """Vault-relative path → revision of every Moodle mirror note."""
        root = self.vault.resolve(KNOWLEDGE)
        out: dict[str, str] = {}
        for path in sorted(root.rglob("*.md")) if root.is_dir() else []:
            if any(part.startswith(".") for part in path.relative_to(root).parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if front_matter(text).get("type") in MIRROR_TYPES:
                out[path.relative_to(self.vault.root).as_posix()] = revision_of(path) or ""
        return out

    def facts(self) -> list[dict[str, Any]]:
        return (self._json(FACTS_FILE) or {}).get("tasks", [])

    def tickets(self) -> list[dict[str, Any]]:
        """Tickets of the board repo that carry the label or a task marker."""
        directory = self.vault.resolve(self.draft_dir)
        out = []
        for path in sorted(directory.glob("*.md")) if directory.is_dir() else []:
            rel = path.relative_to(self.vault.root).as_posix()
            text = path.read_text(encoding="utf-8")
            try:
                t = load(text, self.repo)
            except ValueError as exc:
                out.append({"path": rel, "error": str(exc), "markers": MARKER.findall(text)})
                continue
            markers = MARKER.findall(t.body)
            if self.label not in t.labels and not markers:
                continue
            out.append(
                {
                    "path": rel,
                    "number": t.number,
                    "draft": t.number is None,
                    "title": t.title,
                    "state": t.state,
                    "labels": t.labels,
                    "status": norm_value(t.project.get("Status")),
                    "due": norm_value(t.project.get("Due")),
                    "markers": markers,
                }
            )
        return out

    def _log(self) -> dict[str, Any]:
        log = self._json(LOG_FILE) or {}
        log.setdefault("notes", {})
        log.setdefault("facts", {})
        log.setdefault("done_suggested", [])
        return log

    # -- input -------------------------------------------------------------------

    def input(self, today: date | None = None) -> dict[str, Any]:
        today = today or datetime.now(UTC).date()
        log = self._log()
        mirrors = self.mirrors()
        changed = [rel for rel, rev in mirrors.items() if log["notes"].get(rel) != rev]
        removed = [rel for rel in log["notes"] if rel not in mirrors]
        tickets = self.tickets()
        by_marker: dict[str, list[dict[str, Any]]] = {}
        for t in tickets:
            for m in t["markers"]:
                by_marker.setdefault(m, []).append(t)

        facts, hints, untracked = [], [], []
        for f in self.facts():
            seen = log["facts"].get(f["key"])
            status = (
                "new"
                if seen is None
                else "changed"
                if (seen.get("due"), seen.get("submitted"), seen.get("title"))
                != (f.get("due"), f.get("submitted"), f.get("title"))
                else "unchanged"
            )
            linked = by_marker.get(f["key"], [])
            facts.append(f | {"status": status, "tickets": [t["path"] for t in linked]})
            due_day = (f.get("due") or "")[:10] or None
            for t in linked:
                if t.get("error"):
                    continue
                moved = seen is None or seen.get("due") != f.get("due") or t["due"] is None
                if due_day and t["due"] != due_day and moved:
                    hints.append(
                        {
                            "type": "due_changed",
                            "ticket": t["path"],
                            "key": f["key"],
                            "from": t["due"],
                            "to": due_day,
                        }
                    )
                if (
                    f.get("submitted")
                    and t["state"] == "open"
                    and t["status"] != "Done"
                    and f["key"] not in log["done_suggested"]
                ):
                    hints.append({"type": "submitted", "ticket": t["path"], "key": f["key"]})
            recent = due_day is None or date.fromisoformat(due_day) >= today - timedelta(
                RECENT_DAYS
            )
            if not linked and not f.get("submitted") and recent:
                untracked.append(f["key"])

        return {
            "generated_at": now_iso(),
            "first_run": not log["notes"],
            "board": self.board(),
            "notes": {
                "changed": changed,
                "removed": removed,
                "unchanged": len(mirrors) - len(changed),
            },
            "facts": facts,
            "untracked_facts": untracked,
            "tickets": tickets,
            "hints": hints,
            "marker": "<!-- lifeos:task <source> -->",
        }

    # -- check -------------------------------------------------------------------

    def check(self) -> dict[str, Any]:
        errors, warnings = [], []
        board = self.board()
        if not board["label_exists"]:
            warnings.append(
                f"label '{self.label}' is not known yet: run a ticket sync in LifeOS"
                " before the plan"
            )
        if not board["has_due_field"]:
            warnings.append("project field 'Due' is not known yet: run a ticket sync in LifeOS")
        owners: dict[str, str] = {}
        for t in self.tickets():
            path = t["path"]
            if t.get("error"):
                errors.append(f"{path}: {t['error']}")
                continue
            if len(t["markers"]) > 1:
                errors.append(f"{path}: more than one task marker")
            for m in t["markers"]:
                if m in owners:
                    errors.append(f"{path}: marker {m} is already used by {owners[m]}")
                owners.setdefault(m, path)
            if t["due"] is not None and not DATE.match(t["due"]):
                errors.append(f"{path}: Due must look like YYYY-MM-DD, got '{t['due']}'")
            options = board["status_options"]
            if t["status"] is not None and options and t["status"] not in options:
                errors.append(f"{path}: Status '{t['status']}' is not one of {', '.join(options)}")
            if t["draft"]:
                if not Path(path).name.startswith("new-"):
                    errors.append(f"{path}: a draft file name must start with 'new-'")
                if not t["title"].strip():
                    errors.append(f"{path}: the title is empty")
                if self.label not in t["labels"]:
                    errors.append(f"{path}: label '{self.label}' is missing")
                if not t["markers"]:
                    errors.append(f"{path}: the task marker is missing")
        return {"ok": not errors, "errors": errors, "warnings": warnings}

    # -- done --------------------------------------------------------------------

    def done(self) -> dict[str, Any]:
        result = self.check()
        if not result["ok"]:
            raise TasksError("fix the drafts first:\n" + "\n".join(result["errors"]))
        log = self._log()
        tickets = self.tickets()
        linked = {m for t in tickets for m in t["markers"]}
        facts = self.facts()
        suggested = set(log["done_suggested"]) | {
            f["key"] for f in facts if f.get("submitted") and f["key"] in linked
        }
        new_log = {
            "last_run": now_iso(),
            "notes": self.mirrors(),
            "facts": {
                f["key"]: {
                    "due": f.get("due"),
                    "submitted": f.get("submitted"),
                    "title": f.get("title"),
                }
                for f in facts
            },
            "done_suggested": sorted(suggested),
        }
        write_atomic(
            self.vault.resolve(LOG_FILE), json.dumps(new_log, indent=2, ensure_ascii=False)
        )
        return {
            "recorded_notes": len(new_log["notes"]),
            "recorded_facts": len(new_log["facts"]),
            "tickets": len(tickets),
            "warnings": result["warnings"],
        }
