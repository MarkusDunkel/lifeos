from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter

from lifeos.connectors.onedrive.auth import MicrosoftAuth, OneDriveError
from lifeos.connectors.onedrive.sync import PresentationsSync


def build_router(
    sync: PresentationsSync, auth: MicrosoftAuth | None, missing: list[str]
) -> APIRouter:
    """``missing`` lists the settings still needed before OneDrive can be used."""
    router = APIRouter(prefix="/api/presentations", tags=["presentations"])

    def _auth() -> MicrosoftAuth:
        if auth is None:
            raise OneDriveError(f"OneDrive is not set up: set {', '.join(missing)}", 400)
        return auth

    @router.get("/status")
    def status() -> dict:
        return {
            "configured": not missing,
            "missing": missing,
            "connected": auth is not None and auth.connected,
            **sync.status(),
        }

    @router.post("/connect")
    def connect() -> dict:
        return _auth().start()

    @router.post("/connect/poll")
    def poll() -> dict:
        return {"status": _auth().poll()}

    @router.post("/disconnect")
    def disconnect() -> dict:
        _auth().disconnect()
        return {"status": "disconnected"}

    @router.post("/sync")
    def run_sync() -> dict:
        return asdict(sync.sync())

    return router
