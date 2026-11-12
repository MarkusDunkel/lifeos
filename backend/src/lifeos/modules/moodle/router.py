from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter
from pydantic import BaseModel

from lifeos.connectors.moodle.client import MoodleError
from lifeos.connectors.moodle.sync import MoodleSync


class ConnectRequest(BaseModel):
    username: str | None = None
    password: str | None = None
    token: str | None = None


class SelectRequest(BaseModel):
    ids: list[int]


def build_router(sync: MoodleSync, missing: list[str]) -> APIRouter:
    """``missing`` lists the settings still needed before Moodle can be used."""
    router = APIRouter(prefix="/api/moodle", tags=["moodle"])

    def _access():
        if sync.access is None:
            raise MoodleError(f"Moodle is not set up: set {', '.join(missing)}", 400)
        return sync.access

    @router.get("/status")
    def status() -> dict:
        access = sync.access
        return {
            "configured": not missing,
            "missing": missing,
            "connected": access is not None and access.connected,
            "url": access.base_url if access else None,
            **(access.info() if access else {}),
            **sync.status(),
        }

    @router.post("/connect")
    def connect(body: ConnectRequest) -> dict:
        # The password is passed through to Moodle once and never stored or logged.
        return _access().connect(body.username, body.password, body.token)

    @router.post("/disconnect")
    def disconnect() -> dict:
        _access().disconnect()
        return {"status": "disconnected"}

    @router.get("/courses")
    def courses() -> list[dict]:
        return sync.courses()

    @router.put("/courses")
    def select(body: SelectRequest) -> dict:
        sync.select(body.ids)
        return {"selected": body.ids}

    @router.post("/sync")
    def run_sync() -> dict:
        return asdict(sync.sync())

    return router
