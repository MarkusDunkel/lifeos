"""Tickets the AI adds to the inbox (the ``New`` column of the inbox board).

Adding is allowed directly; the owner approves by moving a ticket to Todo.
"""

from __future__ import annotations

import logging
from typing import Any

from lifeos.agents import comments
from lifeos.agents.config import AgentConfig, BoardContract
from lifeos.agents.mirror import STATUS, split_project

log = logging.getLogger("lifeos.agents")


# The general AI assistant (vault `agents/assistant.md`): it answers `KI:` comments and works
# on approved proposals, so proposals name it as responsible when it exists.
ASSISTANT = "assistant"


def assistant_option(cfg: AgentConfig) -> str | None:
    agent = cfg.agents.get(ASSISTANT)
    return agent.responsible_option if agent else None


def owner_option(contract: BoardContract) -> str | None:
    """The person in the Responsible field: its first fixed option in the contract."""
    name = contract.ai.responsible_field
    spec = next((f for f in contract.fields if f.name == name), None)
    return spec.options[0] if spec and spec.options else None


def create_inbox_ticket(
    gh: Any,
    cfg: AgentConfig,
    repo: str | None,
    title: str,
    body: str,
    author: str = "lifeos",
    responsible: str | None = None,
) -> str | None:
    """A new ticket in the inbox column of the inbox board (proposals, notices).

    Adding is allowed directly; the owner approves by moving the ticket to Todo.
    ``responsible`` is an option of the Responsible field (default: the owner). Returns the
    ticket URL, or None when the inbox board or repository is missing.
    """
    created = create_board_ticket(gh, cfg, repo, title, body, author, responsible)
    return created["url"] if created else None


def create_board_ticket(
    gh: Any,
    cfg: AgentConfig,
    repo: str | None,
    title: str,
    body: str,
    author: str = "lifeos",
    responsible: str | None = None,
    column: str | None = None,
) -> dict[str, Any] | None:
    """A new ticket on the inbox board, in ``column`` (default: the inbox column).

    Returns ``{"url", "id", "number", "itemId", "status"}`` of the new ticket, or None when
    the inbox board or repository is missing.
    """
    contract = cfg.inbox
    if contract is None or not contract.inbox or not repo:
        log.warning("no inbox board/repository for: %s", title)
        return None
    owner, number = split_project(contract.project)
    project = gh.project(owner, number)
    meta = gh.repo(repo)
    issue = gh.create_issue(
        meta.id, {"title": title[:250], "body": f"{comments.marker(author)}\n{body}"}
    )
    item_id = gh.add_to_project(project.id, issue["id"])
    status = column or contract.inbox
    if status in project.fields[STATUS].options:
        gh.set_field(project.id, item_id, project.fields[STATUS], status)
    field_name = contract.ai.responsible_field
    options = getattr(project.fields.get(field_name or ""), "options", {})
    person = responsible if responsible in options else owner_option(contract)
    if field_name and person and person in options:
        gh.set_field(project.id, item_id, project.fields[field_name], person)
    return {"url": issue["url"], "id": issue["id"], "number": issue.get("number"),
            "itemId": item_id, "status": status}  # fmt: skip
