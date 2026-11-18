"""One round of the AI teammates: mirror every board contract, then let agents take tickets.

Used by the worker job ``agents`` and the CLI (``lifeos agents sync|run``). After the
teams, the assistant answers new ``KI:`` comments on all boards.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from lifeos.agents import comments
from lifeos.agents.assistant import Assistant
from lifeos.agents.config import AgentDef, BoardContract, load_config
from lifeos.agents.inbox import create_inbox_ticket, owner_option  # noqa: F401 - API
from lifeos.agents.llm import Llm
from lifeos.agents.mirror import BoardMirror
from lifeos.agents.releases import AppRepo, DeployQueue, Releases
from lifeos.agents.team import Team, Workflow
from lifeos.connectors.github.client import GitHubError
from lifeos.core.vault import STATE, Vault

log = logging.getLogger("lifeos.agents")

STATE_DIR = f"{STATE}/agents"
BOARDS_STATE = f"{STATE_DIR}/boards.json"


def runs_state(board: str) -> str:
    return f"{STATE_DIR}/runs-{board}.json"


def run_round(
    gh: Any,
    vault: Vault,
    workflows: dict[str, Workflow],
    repo: str | None,
    git: Any | None = None,
    timezone: str = "UTC",
    mirror_only: bool = False,
    llm_factory: Callable[[AgentDef], Llm] | None = None,
    eur_per_usd: float = 0.9,
    queue: DeployQueue | None = None,
    app: AppRepo | None = None,
) -> tuple[list[str], bool]:
    """Returns the events and whether something failed.

    Without ``llm_factory`` the assistant (``KI:`` comments) does not run.
    """
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
            events.append(f"{contract.board}: mirroring failed: {exc}")
            failed = True
    if mirror_only:
        return events, failed
    releases = Releases(gh, vault, cfg, git, queue, app)
    for contract in order:
        team = Team(
            gh, vault, cfg, contract, workflows, vault.resolve(runs_state(contract.board)),
            git=git, timezone=timezone, repo=repo, releases=releases,
        )  # fmt: skip
        try:
            events += team.poll()
        except (GitHubError, ValueError) as exc:
            events.append(f"{contract.board}: {exc}")
            failed = True
    try:
        events += releases.poll()
    except (GitHubError, ValueError) as exc:
        events.append(f"releases: {exc}")
        failed = True
    if llm_factory is not None:
        assistant = Assistant(gh, vault, cfg, repo, llm_factory, git=git, timezone=timezone,
                              eur_per_usd=eur_per_usd)  # fmt: skip
        try:
            events += assistant.poll()
        except (GitHubError, ValueError) as exc:
            events.append(f"assistant: {exc}")
            failed = True
    return events, failed
