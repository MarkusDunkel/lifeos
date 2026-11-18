"""One round of the AI teammates: mirror every board contract, then let agents take tickets.

Used by the worker job ``agents`` and the CLI (``lifeos agents sync|run``).
"""

from __future__ import annotations

import logging
from typing import Any

from lifeos.agents import comments
from lifeos.agents.config import AgentConfig, BoardContract, load_config
from lifeos.agents.mirror import STATUS, BoardMirror, split_project
from lifeos.agents.team import Team, Workflow
from lifeos.connectors.github.client import GitHubError
from lifeos.core.vault import STATE, Vault

log = logging.getLogger("lifeos.agents")

STATE_DIR = f"{STATE}/agents"
BOARDS_STATE = f"{STATE_DIR}/boards.json"


def runs_state(board: str) -> str:
    return f"{STATE_DIR}/runs-{board}.json"


def owner_option(contract: BoardContract) -> str | None:
    """The person in the Responsible field: its first fixed option in the contract."""
    name = contract.ai.responsible_field
    spec = next((f for f in contract.fields if f.name == name), None)
    return spec.options[0] if spec and spec.options else None


def create_inbox_ticket(
    gh: Any, cfg: AgentConfig, repo: str | None, title: str, body: str, author: str = "lifeos"
) -> str | None:
    """A new ticket in the inbox column of the inbox board (proposals, notices).

    Adding is allowed directly; the owner approves by moving the ticket to Todo.
    Returns the ticket URL, or None when the inbox board or repository is missing.
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
    if contract.inbox in project.fields[STATUS].options:
        gh.set_field(project.id, item_id, project.fields[STATUS], contract.inbox)
    responsible = contract.ai.responsible_field
    person = owner_option(contract)
    if responsible and person and person in getattr(project.fields.get(responsible), "options", {}):
        gh.set_field(project.id, item_id, project.fields[responsible], person)
    return issue["url"]


def run_round(
    gh: Any,
    vault: Vault,
    workflows: dict[str, Workflow],
    repo: str | None,
    git: Any | None = None,
    timezone: str = "UTC",
    mirror_only: bool = False,
) -> tuple[list[str], bool]:
    """Returns the events and whether something failed."""
    cfg = load_config(vault)
    events = [f"config: {e}" for e in cfg.errors]
    failed = bool(cfg.errors)
    t = comments.Texts.load(vault)

    def on_drift(contract: BoardContract, diff: list[str]) -> None:
        items = "\n".join(f"- {d}" for d in diff)
        body = t("drift_body", title=contract.title, path=contract.path, items=items)
        create_inbox_ticket(gh, cfg, repo, t("drift_title", title=contract.title), body)

    mirror = BoardMirror(gh, vault.resolve(BOARDS_STATE), on_drift)
    # the inbox board first: drift tickets and proposals need its New column
    order = sorted(cfg.boards.values(), key=lambda c: not c.inbox)
    for contract in order:
        try:
            events += mirror.sync(contract, cfg)
        except (GitHubError, ValueError) as exc:
            events.append(f"{contract.board}: Spiegeln fehlgeschlagen: {exc}")
            failed = True
    if mirror_only:
        return events, failed
    for contract in order:
        team = Team(
            gh, vault, cfg, contract, workflows, vault.resolve(runs_state(contract.board)),
            git=git, timezone=timezone,
        )  # fmt: skip
        try:
            events += team.poll()
        except (GitHubError, ValueError) as exc:
            events.append(f"{contract.board}: {exc}")
            failed = True
    return events, failed
