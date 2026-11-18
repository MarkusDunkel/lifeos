"""Workflows that agents with ``runner: workflow`` run, by the name in their definition.

A workflow gets a :class:`~lifeos.agents.team.RunContext`, returns a
:class:`~lifeos.agents.team.RunResult` and raises
:class:`~lifeos.agents.team.AgentBlocked` when it needs an answer from the owner. It only
returns content; board writes, the run log and the commit are done by the team.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from lifeos.agents.config import AgentDef
from lifeos.agents.llm import Llm, LlmSettings, default_llm
from lifeos.agents.releases import AppRepo, DeployQueue
from lifeos.agents.team import Workflow

if TYPE_CHECKING:
    from lifeos.core.config import Settings


def llm_settings(settings: Settings) -> LlmSettings:
    return LlmSettings(
        eur_per_usd=settings.agents_eur_per_usd,
        claude_command=settings.claude_command,
        claude_timeout=settings.claude_timeout,
        public_url=settings.public_url,
    )


def build_llm_factory(settings: Settings) -> Callable[[AgentDef], Llm]:
    """Model access per agent, as its ``billing:`` asks (used by the assistant)."""
    model_settings = llm_settings(settings)
    return lambda agent: default_llm(agent, model_settings)


def build_release(settings: Settings) -> tuple[DeployQueue | None, AppRepo | None]:
    """The deploy queue and app repository for releasing pull requests (both None when
    LIFEOS_APP_REPO is not set)."""
    if not settings.app_repo or not settings.github_token:
        return None, None
    queue = DeployQueue(settings.state_dir / "deploy") if settings.state_dir else None
    return queue, AppRepo(settings.app_repo, settings.deploy_branch, settings.github_token)


def build_workflows(settings: Settings) -> dict[str, Workflow]:
    from lifeos.agents.assistant_work import make_assistant_workflow
    from lifeos.modules.applications import writer

    model_settings = llm_settings(settings)
    return {
        "application-writer": writer.make_application_writer(model_settings),
        "assistant": make_assistant_workflow(
            model_settings, app_repo=settings.app_repo, deploy_branch=settings.deploy_branch
        ),
    }
