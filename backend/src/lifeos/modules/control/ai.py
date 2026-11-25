"""Which processes call a model, with which agent, and how often they did today.

The counters already exist in each module (the planner's and the matcher's daily limits,
the board agents' run logs, the supervisor's report); this only reads them.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from lifeos.agents.config import AgentConfig, load_config
from lifeos.agents.runlog import runlog_path
from lifeos.core.vault import Vault, VaultPathError
from lifeos.modules.career.matcher import STATE_FILE as MATCHER_STATE
from lifeos.modules.control.catalog import WORKFLOW_AGENTS, Catalog
from lifeos.modules.planning.planner import STATE_FILE as PLANNER_STATE

PLANNER = "calendar-planner"
MATCHER = "job-matcher"
SUPERVISOR = "control-room"


def _read(vault: Vault, rel: str) -> Any:
    try:
        return json.loads(vault.resolve(rel).read_text(encoding="utf-8"))
    except (OSError, ValueError, VaultPathError):
        return None


def _runs_logged(vault: Vault, agent: str, today: str) -> int:
    try:
        text = vault.resolve(runlog_path(agent)).read_text(encoding="utf-8")
    except (OSError, VaultPathError):
        return 0
    return sum(1 for line in text.splitlines() if line.startswith(f"## {today}"))


def _runs_today(vault: Vault, agent: str, today: str, report: dict | None) -> int:
    if agent == PLANNER:
        return int(((_read(vault, PLANNER_STATE) or {}).get("runs") or {}).get(today, 0))
    if agent == MATCHER:
        day = ((_read(vault, MATCHER_STATE) or {}).get("days") or {}).get(today) or {}
        return int(day.get("auto", 0)) + int(day.get("manual", 0))
    if agent == SUPERVISOR:
        report = report or {}
        return (
            int((report.get("calls") or {}).get("n", 0)) if report.get("callsDay") == today else 0
        )
    return _runs_logged(vault, agent, today)


def _limit(agent: str, raw: dict) -> int | None:
    if agent == MATCHER:
        return int(raw.get("daily_limit", 10)) + int(raw.get("manual_limit", 20))
    if "daily_limit" in raw:
        return int(raw["daily_limit"])
    return None


def ai_usage(
    vault: Vault, catalog: Catalog, now: datetime, report: dict | None,
    config: AgentConfig | None = None,
) -> dict[str, list[dict]]:  # fmt: skip
    """``{process id: [{agent, title, model, billing, runsToday, dailyLimit}]}``."""
    cfg = config or load_config(vault)
    today = now.date().isoformat()
    result: dict[str, list[dict]] = {}
    for process in catalog.processes:
        names: list[str] = []
        for name in process.ai:
            if name == WORKFLOW_AGENTS:
                names += sorted(n for n, a in cfg.agents.items() if a.runner == "workflow")
            else:
                names.append(name)
        if not process.ai:
            continue
        entries = []
        for name in names:
            agent = cfg.agents.get(name)
            entries.append({
                "agent": name,
                "title": agent.title if agent else name,
                "model": agent.model if agent else None,
                "billing": agent.billing if agent else None,
                "configured": agent is not None,
                "runsToday": _runs_today(vault, name, today, report),
                "dailyLimit": _limit(name, agent.raw) if agent else None,
            })  # fmt: skip
        result[process.id] = entries
    return result
