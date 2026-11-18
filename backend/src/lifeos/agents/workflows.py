"""Workflows that agents with ``runner: workflow`` run, by the name in their definition.

A workflow gets a :class:`~lifeos.agents.team.RunContext`, returns a
:class:`~lifeos.agents.team.RunResult` and raises
:class:`~lifeos.agents.team.AgentBlocked` when it needs an answer from the owner. It only
returns content; board writes, the run log and the commit are done by the team.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lifeos.agents.team import Workflow

if TYPE_CHECKING:
    from lifeos.core.config import Settings


def build_workflows(settings: Settings) -> dict[str, Workflow]:
    from lifeos.modules.applications import writer

    return {
        "application-writer": writer.make_application_writer(
            writer.Settings(eur_per_usd=settings.agents_eur_per_usd)
        ),
    }
