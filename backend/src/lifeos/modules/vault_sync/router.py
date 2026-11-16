from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from lifeos.core.git_sync import GitSync


def build_router(sync: GitSync, interval: int) -> APIRouter:
    """Status of the automatic vault sync and a manual "sync now".

    ``interval`` is ``LIFEOS_GIT_SYNC_INTERVAL``; 0 means the sync is disabled here.
    """
    router = APIRouter(prefix="/api/vault-sync", tags=["vault-sync"])

    @router.get("/status")
    def status() -> dict:
        return {"enabled": interval > 0, "interval": interval, **sync.status()}

    @router.post("/run")
    def run() -> dict:
        if interval <= 0:
            raise HTTPException(400, "vault sync is disabled (LIFEOS_GIT_SYNC_INTERVAL=0)")
        return asdict(sync.run())

    return router
