"""Build the planner the same way for the web app and the worker."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from lifeos.connectors.google.sync import CalendarSync
from lifeos.core.config import Settings
from lifeos.core.vault import Vault
from lifeos.modules.planning.planner import Planner


def build_planner(
    settings: Settings,
    vault: Vault,
    calendar: CalendarSync,
    study_overview: Callable[[], list[dict[str, Any]]] | None = None,
) -> Planner:
    if study_overview is None:
        from lifeos.modules.study.service import StudyService

        study_overview = StudyService(vault, settings.timezone).overview
    return Planner(vault, settings.timezone, calendar, study_overview, settings.planning_dir)
