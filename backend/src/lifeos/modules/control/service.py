"""Collects everything the traffic lights need; shared by the web app and the worker."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from lifeos.connectors.github.sync import STATE_DIR as GITHUB_STATE
from lifeos.connectors.onedrive.sync import STATE_FILE as PRESENTATIONS_STATE
from lifeos.core import tokens
from lifeos.core.config import Settings
from lifeos.core.vault import KNOWLEDGE, Vault, VaultPathError
from lifeos.modules.applications.service import STATE_DIR as APPLICATIONS_STATE
from lifeos.modules.control.ai import agent_quotas, ai_usage
from lifeos.modules.control.catalog import PC, Catalog, build_catalog
from lifeos.modules.control.checks import disk_check, run_checks
from lifeos.modules.control.health import Sources, evaluate
from lifeos.modules.control.store import REPORT_FILE, PcJobs, RunRequests, read_json
from lifeos.modules.control.tokens import InteractiveUsage

DEPLOYER_HEARTBEAT = "deploy/heartbeat.json"
APPLICATIONS_INDEX = APPLICATIONS_STATE + "/index.json"
PLANNER_STATE = ".lifeos/agents/uni-planner.json"
MANAGER_STATE = ".lifeos/agents/lifeos-manager.json"  # lifeos.modules.manager.service
# input category of the study planner → its node in the diagram
PLANNER_INPUTS = {
    "moodle": "kb-moodle",
    "teams-messages": "kb-teams-chats",
    "teams-files": "kb-teams-files",
}


class Control:
    def __init__(self, settings: Settings, vault: Vault, jobs_file: Path | None) -> None:
        self.settings = settings
        self.vault = vault
        self.jobs_file = jobs_file
        self.state_dir = settings.state_dir
        self.catalog: Catalog = build_catalog(settings)
        self.pc = PcJobs(self.state_dir)
        self.requests = RunRequests(self.state_dir)
        self.tz = ZoneInfo(settings.timezone)
        self.interactive = InteractiveUsage(self.state_dir)

    def sources(self) -> Sources:
        heartbeat = read_json(self.state_dir / DEPLOYER_HEARTBEAT) if self.state_dir else None
        return Sources(
            jobs=read_json(self.jobs_file, {}) or {},
            pc=self.pc.all(),
            manual={
                "tickets": (self._vault_json(f"{GITHUB_STATE}/meta.json") or {}).get("synced_at"),
                "presentations": self._mtime(PRESENTATIONS_STATE),
                "start-application": max(
                    (
                        str(e.get("created"))
                        for e in (self._vault_json(APPLICATIONS_INDEX) or {}).values()
                        if e.get("created")
                    ),
                    default=None,
                ),  # fmt: skip
            },
            deployer=(heartbeat or {}).get("at"),
            server_version=(heartbeat or {}).get("version"),
            checks=run_checks(self.settings),
            system_checks=[disk_check(self.vault.root)],
        )

    def status(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        src = self.sources()
        report = self.report()
        src.ai = ai_usage(self.vault, self.catalog, now.astimezone(self.tz), report)
        src.inputs, src.generators = self.actionable_inputs(src)
        result = evaluate(self.catalog, src, now, self.tz)
        result["report"] = report
        result["requests"] = self.requests.all()
        result["manager"] = self.manager_notes()
        return result

    def agent_quotas(self, now: datetime | None = None) -> dict[str, dict]:
        """Per agent: its processes, runs today, daily limit, used up or not."""
        now = (now or datetime.now(UTC)).astimezone(self.tz)
        ledger = tokens.read(self.state_dir / tokens.LEDGER_FILE) if self.state_dir else []
        return agent_quotas(self.vault, self.catalog, now, self.report(), ledger=ledger)

    def manager_notes(self) -> dict[str, dict[str, Any]]:
        """What the LifeOS manager says about each yellow or red light: its explanation,
        until when it holds, and how many runs it already spent on it."""
        watch = (self._vault_json(MANAGER_STATE) or {}).get("watch") or {}
        attempts = watch.get("attempts") or {}
        notes = {
            pid: {k: note.get(k) for k in ("cause", "expect", "ticket", "at", "until")}
            | {"attempts": attempts.get(pid, 0)}
            for pid, note in (watch.get("explained") or {}).items()
            if isinstance(note, dict)
        }  # fmt: skip
        for pid, count in attempts.items():  # started for it, but not explained yet
            notes.setdefault(pid, {"cause": None, "expect": None, "ticket": None, "at": None,
                                   "until": None, "attempts": count})  # fmt: skip
        return notes

    def actionable_inputs(self, src: Sources) -> tuple[dict[str, dict], dict[str, dict]]:
        """For every process that makes an actionable: how much of each input folder changed
        since its last successful run, plus what the study planner recorded."""
        inputs: dict[str, dict] = {}
        folders = [n.folder for n in self.catalog.nodes if n.folder]
        for p in self.catalog.processes:
            makes = [self.catalog.node(o) for o in p.outputs]
            if not p.enabled or not any(n and n.kind == "actionable" for n in makes):
                continue
            entry = (src.pc if p.where == PC else src.jobs).get(p.id) or {}
            since = entry.get("lastSuccess")
            for node_id in p.inputs:
                node = self.catalog.node(node_id)
                if node and node.folder and since:
                    inside = [f for f in folders if f.startswith(node.folder + "/")]
                    inputs[f"{p.id}:{node_id}"] = {
                        "newSince": self._changed_since(node.folder, since, inside)
                    }
        generators: dict[str, dict] = {}
        planner = self._vault_json(PLANNER_STATE)
        if planner:
            generators["uni-planner"] = {
                k: planner.get(k)
                for k in ("lastRun", "summary", "events", "uncertain", "considered", "pending",
                          "totals", "factsChanged")
            }  # fmt: skip
            for category, node_id in PLANNER_INPUTS.items():
                key = f"uni-planner:{node_id}"
                inputs.setdefault(key, {})
                inputs[key] |= {
                    "pending": (planner.get("pending") or {}).get(category, 0),
                    "total": (planner.get("totals") or {}).get(category, 0),
                    "consideredLastRun": (planner.get("considered") or {}).get(category, 0),
                }
        return inputs, generators

    def _changed_since(self, folder: str, since: str, skip: list[str]) -> int:
        """Notes in a knowledge folder modified after ``since`` (sub-folders of other nodes
        are left out)."""
        try:
            root = self.vault.resolve(f"{KNOWLEDGE}/{folder}")
            cutoff = datetime.fromisoformat(since).timestamp()
        except (VaultPathError, ValueError):
            return 0
        if not root.is_dir():
            return 0
        skipped = [self.vault.root / KNOWLEDGE / f for f in skip]
        count = 0
        for path in root.rglob("*.md"):
            if any(path.is_relative_to(s) for s in skipped):
                continue
            try:
                if path.stat().st_mtime > cutoff:
                    count += 1
            except OSError:
                continue
        return count

    def report(self) -> dict | None:
        return read_json(self.state_dir / REPORT_FILE) if self.state_dir else None

    def _vault_json(self, rel: str) -> dict | None:
        try:
            return read_json(self.vault.resolve(rel))
        except VaultPathError:
            return None

    def _mtime(self, rel: str) -> str | None:
        try:
            path = self.vault.resolve(rel)
        except VaultPathError:
            return None
        if not path.is_file():
            return None
        return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds")
