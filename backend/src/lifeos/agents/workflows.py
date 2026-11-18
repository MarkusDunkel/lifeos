"""Workflows that agents with ``runner: workflow`` run, by the name in their definition.

A workflow gets a :class:`~lifeos.agents.team.RunContext`, returns a
:class:`~lifeos.agents.team.RunResult` and raises
:class:`~lifeos.agents.team.AgentBlocked` when it needs an answer from the owner. It only
returns content; board writes, the run log and the commit are done by the team.
"""

from __future__ import annotations

from lifeos.agents.team import Workflow

WORKFLOWS: dict[str, Workflow] = {}
