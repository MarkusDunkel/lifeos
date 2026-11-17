"""The applications board (GitHub Projects v2) and the watcher that reacts to it.

Moving a ticket between columns is how the user steers the process. The worker polls the
board, compares each item's status with the last one it saw (``board.json``) and runs the
handler of the new column once. Column names are the user's (German) board content.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lifeos.connectors.github.client import GitHubError, Project, ProjectField, item_values
from lifeos.core.vault import write_atomic

log = logging.getLogger("lifeos.applications")

STATUS = "Status"
SUGGESTED = "Vorschlag"
SUGGESTION_DECLINED = "Vorschlag abgelehnt"
NEW = "Neu"
ACTIVE = "Aktiv"
IN_REVIEW = "In Review"
SENT = "Versendet"
INTERVIEW_1 = "1. Gespräch"
INTERVIEW_2 = "2. Gespräch"
INTERVIEW_3 = "3.+ Gespräch"
OFFER = "Angebot"
ACCEPTED = "Zugesagt"
REJECTED = "Absage erhalten"
DECLINED = "Von mir abgelehnt"
COLUMNS = [
    SUGGESTED, SUGGESTION_DECLINED, NEW, ACTIVE, IN_REVIEW, SENT,
    INTERVIEW_1, INTERVIEW_2, INTERVIEW_3, OFFER, ACCEPTED, REJECTED, DECLINED,
]  # fmt: skip
CLOSING = {SUGGESTION_DECLINED, ACCEPTED, REJECTED, DECLINED}
ROUND_FIELD = "Gesprächsrunde"
COMPANY_FIELD = "Firma"
POSTING_FIELD = "Inserat"
EXTRA_FIELDS = {ROUND_FIELD: "NUMBER", COMPANY_FIELD: "TEXT", POSTING_FIELD: "TEXT"}
LABEL = "bewerbung"
BOARD_TITLE = "Bewerbungen"


class BoardError(Exception):
    pass


def split_project(ref: str) -> tuple[str, int]:
    owner, _, number = ref.partition("/")
    if not owner or not number.isdigit():
        raise BoardError(f"project must look like owner/number: {ref}")
    return owner, int(number)


def setup_board(gh: Any, repo: str, project_ref: str | None) -> str:
    """Create (or complete) the board: columns, extra fields, linked repo, label.

    Returns ``owner/number``. Safe to run again.
    """
    owner = repo.split("/", 1)[0]
    if project_ref:
        owner, number = split_project(project_ref)
    else:
        created = gh.create_project(gh.owner_id(owner), BOARD_TITLE)
        number = created["number"]
    project = gh.project(owner, number)
    meta = gh.repo(repo)
    try:
        gh.link_repository(project.id, meta.id)
    except GitHubError as exc:  # already linked
        log.info("link repository: %s", exc)
    status = project.fields.get(STATUS)
    if status is None:
        gh.create_field(project.id, STATUS, "SINGLE_SELECT", COLUMNS)
    elif list(status.options) != COLUMNS:
        gh.set_options(status.id, COLUMNS)
    for name, data_type in EXTRA_FIELDS.items():
        if name not in project.fields:
            gh.create_field(project.id, name, data_type)
    if LABEL not in meta.labels:
        gh.create_label(meta.id, LABEL)
    return f"{owner}/{number}"


@dataclass
class BoardItem:
    item_id: str
    issue_id: str
    number: int
    title: str
    url: str
    state: str  # OPEN | CLOSED
    status: str | None
    values: dict[str, Any] = field(default_factory=dict)


def board_items(raw: list[dict[str, Any]], fields: dict[str, ProjectField]) -> list[BoardItem]:
    items = []
    for node in raw:
        issue = node.get("content") or {}
        if issue.get("__typename") != "Issue":
            continue  # draft items and pull requests are not applications
        values = item_values(node, fields)
        items.append(
            BoardItem(
                item_id=node["id"],
                issue_id=issue["id"],
                number=issue["number"],
                title=issue["title"],
                url=issue["url"],
                state=issue["state"],
                status=values.get(STATUS),
                values=values,
            )
        )
    return items


Handler = Callable[[BoardItem], str | None]


class BoardWatcher:
    """Runs the handler of a column once when an item arrives there."""

    def __init__(
        self,
        gh: Any,
        project: Project,
        state_file: Path,
        handlers: dict[str, Handler] | None = None,
        trigger_on_first_sight: frozenset[str] = frozenset({ACTIVE}),
    ) -> None:
        self.gh = gh
        self.project = project
        self.state_file = state_file
        self.handlers = handlers or {}
        self.trigger_on_first_sight = trigger_on_first_sight

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def poll(self) -> list[str]:
        """Check the board once; returns a line per transition that was handled."""
        state = self._load()
        events: list[str] = []
        items = board_items(self.gh.project_items(self.project.id), self.project.fields)
        for item in items:
            seen = state.get(item.item_id)
            if seen is not None and seen.get("status") == item.status:
                continue
            first = seen is None
            entry = {"status": item.status, "since": _now(), "issue": item.number}
            if not first or item.status in self.trigger_on_first_sight:
                event = self._handle(item)
                if event:
                    events.append(event)
                    entry["note"] = event
            state[item.item_id] = entry
            # Save after every item: a crash must not run handlers twice.
            write_atomic(self.state_file, json.dumps(state, ensure_ascii=False, indent=1))
        return events

    def _handle(self, item: BoardItem) -> str | None:
        if item.status in CLOSING and item.state == "OPEN":
            self.gh.update_issue(item.issue_id, {"state": "CLOSED"})
        elif item.status not in CLOSING and item.state == "CLOSED":
            self.gh.update_issue(item.issue_id, {"state": "OPEN"})
        handler = self.handlers.get(item.status or "")
        if handler is None:
            return f"#{item.number} → {item.status}" if item.status in CLOSING else None
        try:
            note = handler(item)
        except Exception as exc:  # report on the ticket, never stop the watcher
            log.exception("handler for %s failed on #%s", item.status, item.number)
            self.gh.add_comment(item.issue_id, f"LifeOS: step '{item.status}' failed: {exc}")
            return f"#{item.number} {item.status}: failed ({exc})"
        prefix = f"#{item.number} {item.status}"
        return f"{prefix}: {note}" if note else f"#{item.number} → {item.status}"

    def set_status(self, item: BoardItem, status: str) -> None:
        """Move an item (e.g. to In Review) and remember it, so it does not re-trigger."""
        self.gh.set_field(self.project.id, item.item_id, self.project.fields[STATUS], status)
        state = self._load()
        state[item.item_id] = {"status": status, "since": _now(), "issue": item.number}
        write_atomic(self.state_file, json.dumps(state, ensure_ascii=False, indent=1))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
