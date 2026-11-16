from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, HTTPException

from lifeos.core.git_sync import GitSync


def build_router(sync: GitSync, interval: int, jobs_file: Path | None = None) -> APIRouter:
    """Status of the automatic vault sync and the worker jobs, and a manual "sync now".

    ``interval`` is ``LIFEOS_GIT_SYNC_INTERVAL``; 0 means the sync is disabled here.
    """
    router = APIRouter(tags=["vault-sync"])

    @router.get("/api/vault-sync/status")
    def status() -> dict:
        return {"enabled": interval > 0, "interval": interval, **sync.status()}

    @router.post("/api/vault-sync/run")
    def run() -> dict:
        if interval <= 0:
            raise HTTPException(400, "vault sync is disabled (LIFEOS_GIT_SYNC_INTERVAL=0)")
        return asdict(sync.run())

    @router.get("/api/jobs/status")
    def jobs() -> dict:
        """Last run of every worker job (``state/jobs.json``)."""
        if jobs_file and jobs_file.exists():
            try:
                return json.loads(jobs_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return {}

    return router
