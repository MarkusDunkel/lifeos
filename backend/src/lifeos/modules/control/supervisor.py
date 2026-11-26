"""The AI supervisor (``agents/control-room.md``): explains the current problems.

It never decides the traffic lights – the fixed rules do. When something is yellow or red
it reads the problem processes, their recent runs and the latest vault commits, and writes
a short diagnosis (cause, what to do) to ``state/control-report.json`` for the control
room. It calls the model only when the set of problems changed, or once a day while they
persist, and at most ``daily_limit`` times a day. No tickets, no notifications.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from lifeos.agents.config import AgentDef, load_config
from lifeos.agents.llm import Llm
from lifeos.modules.control.health import FAIL, OK, WARN
from lifeos.modules.control.service import Control
from lifeos.modules.control.store import REPORT_FILE, read_json, write_json

AGENT = "control-room"
REFRESH = timedelta(hours=20)  # re-diagnose persisting problems about once a day

SYSTEM = """You are the supervisor of LifeOS, a personal system with background jobs on a
server, scheduled tasks on the owner's PC and a few manual syncs. Fixed rules have already
marked processes yellow (late, skipped) or red (failed, missing precondition, overdue).
Explain each problem to the owner in plain words: the most likely cause and the one concrete
thing to do (a button in LifeOS, a command, "turn on the PC", or "nothing, it recovers by
itself"). Name what is affected (which folder or calendar is out of date). Problems with one
cause (e.g. the worker is down) get one finding. Be brief: the summary is one or two
sentences, each finding one sentence for cause and one for action. When you name a process
in the text, use its label with its global id, e.g. "Moodle sync (P-30)". Never invent facts
that are not in the data; say when the cause is unclear. {language}"""


class Finding(BaseModel):
    process: str = Field(description="id of the process (or system check) the finding is about")
    cause: str
    action: str
    affected: str = ""


class Diagnosis(BaseModel):
    summary: str
    findings: list[Finding]


def signature(problems: list[dict]) -> str:
    """Changes when a problem appears, goes or changes its status – not with its age."""
    key = sorted((p["id"], p["level"], p.get("status") or "", p.get("message") or "")
                 for p in problems)  # fmt: skip
    return hashlib.sha1(json.dumps(key).encode()).hexdigest()[:16]


class Supervisor:
    def __init__(
        self,
        control: Control,
        llm_for: Callable[[AgentDef], Llm],
        git_log: Callable[[], list[str]] | None = None,
    ) -> None:
        self.control = control
        self.llm_for = llm_for
        self.git_log = git_log or self._git_log
        self.path = control.state_dir / REPORT_FILE if control.state_dir else None

    def problems(self, status: dict) -> list[dict]:
        # name targets the way the owner knows them (folder or calendar), not by node id
        names = {n["id"]: f"{n['label']} ({n['detail']})" if n["detail"] else n["label"]
                 for n in status["nodes"]}  # fmt: skip
        found = [
            {k: p[k] for k in ("id", "ref", "label", "where", "trigger", "level", "reason",
                               "status", "message", "lastRun", "lastSuccess", "checks", "hint",
                               "description")}
            | {"recentRuns": p["history"][-5:],
               "writes": [names.get(o, o) for o in p["outputs"]]}
            for p in status["processes"]
            if p["enabled"] and p["level"] in (WARN, FAIL)
        ]  # fmt: skip
        for c in status["system"]:
            if c["ok"] is not True:
                found.append({"id": c["id"], "label": c["label"], "where": "system",
                              "level": FAIL if c["ok"] is False else WARN,
                              "reason": c["message"], "hint": c["fix"]})  # fmt: skip
        return found

    def run(self, now: datetime | None = None) -> tuple[str, str]:
        """Returns ``(status, message)`` for the worker's job record."""
        now = now or datetime.now(UTC)
        if self.path is None:
            return "skipped", "LIFEOS_STATE_DIR is not set"
        cfg = load_config(self.control.vault)
        agent = cfg.agents.get(AGENT)
        if agent is None:
            return "skipped", f"no agents/{AGENT}.md in the vault"
        status = self.control.status(now)
        problems = self.problems(status)
        previous = read_json(self.path) or {}
        if not problems:
            if previous.get("level") != OK:
                self._save(previous, now, OK, "Everything runs as expected.", [], signature([]))
            return "skipped", "all green"

        sig = signature(problems)
        last = _parse(previous.get("at"))
        if previous.get("signature") == sig and last and now - last < REFRESH:
            return "skipped", "unchanged"
        # per local calendar day, like every other agent's counter (and the agents page)
        today = now.astimezone(ZoneInfo(self.control.settings.timezone)).date().isoformat()
        calls = previous.get("calls", {}) if previous.get("callsDay") == today else {}
        limit = int(agent.raw.get("daily_limit", 12))
        if calls.get("n", 0) >= limit:
            return "skipped", f"daily limit of {limit} diagnoses reached"

        language = agent.raw.get("language")
        system = SYSTEM.format(language=f"Write in {language}." if language else "")
        prompt = json.dumps(
            {"now": now.isoformat(timespec="seconds"), "timezone": self.control.settings.timezone,
             "problems": problems, "recentVaultCommits": self.git_log()},
            ensure_ascii=False, indent=1,
        )  # fmt: skip
        effort = agent.raw.get("effort") or "low"
        if isinstance(effort, dict):  # `effort: {diagnose: low}` like the other agents
            effort = effort.get("diagnose", "low")
        try:
            diagnosis, usage = self.llm_for(agent).parse(system, prompt, Diagnosis, str(effort))
        except Exception as exc:  # the model is optional: the lights work without it
            return "error", f"diagnosis failed: {type(exc).__name__}: {exc}"
        level = FAIL if any(p["level"] == FAIL for p in problems) else WARN
        report = self._save(
            previous, now, level, diagnosis.summary,
            [f.model_dump() for f in diagnosis.findings], sig,
        )  # fmt: skip
        report.update(callsDay=today, calls={"n": calls.get("n", 0) + 1}, model=agent.model,
                      costUsd=round(usage.usd, 4))  # fmt: skip
        write_json(self.path, report)
        return "ok", f"{len(diagnosis.findings)} finding(s) for {len(problems)} problem(s)"

    def _save(
        self, previous: dict, now: datetime, level: str, summary: str,
        findings: list[dict], sig: str,
    ) -> dict[str, Any]:  # fmt: skip
        assert self.path is not None
        report = {
            **{k: previous[k] for k in ("callsDay", "calls") if k in previous},
            "at": now.isoformat(timespec="seconds"),
            "level": level,
            "summary": summary,
            "findings": findings,
            "signature": sig,
        }
        write_json(self.path, report)
        return report

    def _git_log(self) -> list[str]:
        try:
            out = subprocess.run(
                ["git", "log", "-15", "--format=%cI %s"], cwd=self.control.vault.root,
                capture_output=True, text=True, timeout=20, check=False,
            )  # fmt: skip
        except (OSError, subprocess.SubprocessError):
            return []
        return out.stdout.splitlines()


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
