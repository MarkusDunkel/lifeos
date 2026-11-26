"""Whose turn is it? Mirrors each board ticket's turn to the issue itself.

GitHub project views only show one project. To see across all boards what the owner has to
do (approve, review, work on) and what the AI is working on, the turn lives on the issue:

- owner's turn: the owner is an assignee (GitHub's "Assigned to me" lists it);
- AI's turn: the label ``TURN_LABEL``;
- nobody's turn: neither.

The turn comes from the column's ``turn`` in the board contract, the Responsible field
(``AI: …`` = the AI) and the AI field (``blocked`` always makes it the owner's turn).
Closed tickets are nobody's turn. Only what this module set itself is ever removed again:
an assignee or label the owner added by hand stays.

State (what was set per issue) lives in ``.lifeos/agents/turn.json``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from lifeos.agents.config import AI_FIELD, BLOCKED, AgentConfig, BoardContract
from lifeos.agents.mirror import STATUS, split_project
from lifeos.connectors.github.client import GitHubError, item_values
from lifeos.core.vault import write_atomic

log = logging.getLogger("lifeos.agents")

TURN_LABEL = "🤖 KI dran"
LABEL_COLOR = "8250df"
OWNER, AI, NOBODY = "owner", "ai", "none"
RANK = {NOBODY: 0, AI: 1, OWNER: 2}  # a ticket on two boards: the owner's turn wins


def turn_of(contract: BoardContract, values: dict[str, Any], state: str) -> str:
    """The turn of one board item."""
    if state != "OPEN":
        return NOBODY
    if values.get(AI_FIELD) == BLOCKED:
        return OWNER
    column = contract.column(values.get(STATUS))
    if column is None:
        return NOBODY
    rule = column.turn
    if rule in (OWNER, AI):
        return rule
    if rule.startswith("responsible"):
        who = str(values.get(contract.ai.responsible_field or "") or "")
        if who.startswith("AI:"):
            return AI
        # a human or nobody named: the owner's own ticket
        return NOBODY if rule == "responsible-ai" else OWNER
    return NOBODY


class TurnMirror:
    def __init__(self, gh: Any, state_file: Path, owner_login: str | None = None) -> None:
        self.gh = gh
        self.state_file = state_file
        self.owner_login = owner_login

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, state: dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.state_file, json.dumps(state, ensure_ascii=False, indent=1))

    def sync(self, cfg: AgentConfig) -> list[str]:
        boards = [c for c in cfg.boards.values() if any(col.turn != NOBODY for col in c.columns)]
        if not boards:
            return []
        wanted: dict[str, dict[str, Any]] = {}  # issue id -> {turn, repo, number, has}
        for contract in boards:
            owner, number = split_project(contract.project)
            project = self.gh.project(owner, number)
            for node in self.gh.project_items(project.id):
                issue = node.get("content") or {}
                if issue.get("__typename") != "Issue":
                    continue
                values = item_values(node, project.fields)
                turn = turn_of(contract, values, issue.get("state", "OPEN"))
                labels = (issue.get("labels") or {}).get("nodes", [])
                assignees = (issue.get("assignees") or {}).get("nodes", [])
                entry = wanted.setdefault(issue["id"], {
                    "turn": NOBODY,
                    "repo": (issue.get("repository") or {}).get("nameWithOwner"),
                    "number": issue.get("number"),
                    "labels": {n["name"] for n in labels},
                    "assignees": {n["login"] for n in assignees},
                })  # fmt: skip
                if RANK[turn] > RANK[entry["turn"]]:
                    entry["turn"] = turn
        state = self._load()
        set_by_us: dict[str, list[str]] = state.get("set", {})
        login = self.owner_login or self.gh.viewer_login()
        metas: dict[str, Any] = {}
        events: list[str] = []
        for issue_id, entry in wanted.items():
            repo = entry["repo"]
            if not repo:
                continue
            ours = set(set_by_us.get(issue_id, []))
            want = (
                {"assignee"}
                if entry["turn"] == OWNER
                else {"label"}
                if entry["turn"] == AI
                else set()
            )
            have = {
                *(["assignee"] if login in entry["assignees"] else []),
                *(["label"] if TURN_LABEL in entry["labels"] else []),
            }
            add, remove = want - have, (have - want) & ours
            if not add and not remove:
                kept = ours & have
                if kept:
                    set_by_us[issue_id] = sorted(kept)
                else:
                    set_by_us.pop(issue_id, None)
                continue
            try:
                meta = metas.get(repo) or metas.setdefault(repo, self.gh.repo(repo))
                if "label" in add and TURN_LABEL not in meta.labels:
                    meta.labels[TURN_LABEL] = self.gh.create_label(meta.id, TURN_LABEL, LABEL_COLOR)
                user_id = meta.assignees.get(login)
                if "assignee" in add and user_id:
                    self.gh.add_assignees(issue_id, [user_id])
                if "assignee" in remove and user_id:
                    self.gh.remove_assignees(issue_id, [user_id])
                if "label" in add:
                    self.gh.add_labels(issue_id, [meta.labels[TURN_LABEL]])
                if "label" in remove and TURN_LABEL in meta.labels:
                    self.gh.remove_labels(issue_id, [meta.labels[TURN_LABEL]])
            except GitHubError as exc:
                events.append(f"turn #{entry['number']}: {exc}")
                continue
            now_ours = (ours | add) - remove
            if now_ours:
                set_by_us[issue_id] = sorted(now_ours)
            else:
                set_by_us.pop(issue_id, None)
            events.append(f"turn #{entry['number']}: {entry['turn']}")
        state["set"] = set_by_us
        self._save(state)
        return events
