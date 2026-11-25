"""Collects everything the traffic lights need; shared by the web app and the worker."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from lifeos.connectors.github.sync import STATE_DIR as GITHUB_STATE
from lifeos.connectors.onedrive.sync import STATE_FILE as PRESENTATIONS_STATE
from lifeos.core.config import Settings
from lifeos.core.vault import Vault, VaultPathError
from lifeos.modules.control.catalog import Catalog, build_catalog
from lifeos.modules.control.checks import disk_check, run_checks
from lifeos.modules.control.health import Sources, evaluate
from lifeos.modules.control.store import REPORT_FILE, PcJobs, RunRequests, read_json

DEPLOYER_HEARTBEAT = "deploy/heartbeat.json"


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

    def sources(self) -> Sources:
        heartbeat = read_json(self.state_dir / DEPLOYER_HEARTBEAT) if self.state_dir else None
        return Sources(
            jobs=read_json(self.jobs_file, {}) or {},
            pc=self.pc.all(),
            manual={
                "tickets": (self._vault_json(f"{GITHUB_STATE}/meta.json") or {}).get("synced_at"),
                "presentations": self._mtime(PRESENTATIONS_STATE),
            },
            deployer=(heartbeat or {}).get("at"),
            checks=run_checks(self.settings),
            system_checks=[disk_check(self.vault.root)],
        )

    def status(self, now: datetime | None = None) -> dict[str, Any]:
        result = evaluate(self.catalog, self.sources(), now or datetime.now(UTC), self.tz)
        result["report"] = self.report()
        result["requests"] = self.requests.all()
        return result

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
