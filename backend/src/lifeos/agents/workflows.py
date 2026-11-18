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
from lifeos.agents.team import Workflow

if TYPE_CHECKING:
    from lifeos.core.config import Settings


def llm_settings(settings: Settings) -> LlmSettings:
    return LlmSettings(
        eur_per_usd=settings.agents_eur_per_usd,
        claude_command=settings.claude_command,
        claude_timeout=settings.claude_timeout,
    )


def build_llm_factory(settings: Settings) -> Callable[[AgentDef], Llm]:
    """Model access per agent, as its ``billing:`` asks (used by the assistant)."""
    model_settings = llm_settings(settings)
    return lambda agent: default_llm(agent, model_settings)


def build_workflows(settings: Settings) -> dict[str, Workflow]:
    from lifeos.modules.applications import writer

    return {"application-writer": writer.make_application_writer(llm_settings(settings))}
