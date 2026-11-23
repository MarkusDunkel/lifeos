"""Calendars: Google sign-in, choice of input calendars, mirror sync."""

from __future__ import annotations

from dataclasses import asdict
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from lifeos.connectors.google.auth import GoogleAuth, GoogleError
from lifeos.connectors.google.sync import CalendarSync

CALLBACK = "/api/calendar/google/callback"
RETURN_TO = "/plan"


class CalendarInput(BaseModel):
    id: str
    label: str = ""


class InputsBody(BaseModel):
    inputs: list[CalendarInput]


def build_router(
    sync: CalendarSync, auth: GoogleAuth | None, missing: list[str], public_url: str = ""
) -> APIRouter:
    """``missing`` lists the settings still needed before Google can be used."""
    router = APIRouter(prefix="/api/calendar", tags=["calendar"])

    def _auth() -> GoogleAuth:
        if auth is None:
            raise GoogleError(f"Google Calendar is not set up: set {', '.join(missing)}", 400)
        return auth

    def _redirect_uri(request: Request) -> str:
        base = public_url.rstrip("/") or str(request.base_url).rstrip("/")
        return base + CALLBACK

    @router.get("/status")
    def status() -> dict:
        return {
            "configured": not missing,
            "missing": missing,
            "connected": auth is not None and auth.connected,
            "account": auth.account() if auth else None,
            **sync.config(),
            "last": sync.last_report(),
        }

    @router.post("/google/connect")
    def connect(request: Request) -> dict:
        return {"url": _auth().authorize_url(_redirect_uri(request))}

    @router.get(CALLBACK.removeprefix("/api/calendar"), include_in_schema=False)
    def callback(code: str = "", state: str = "", error: str = "") -> RedirectResponse:
        query: dict[str, str] = {"tab": "calendars"}
        if error:
            query["google"] = f"error: {error}"
        else:
            try:
                _auth().complete(code, state)
                query["google"] = "connected"
                sync.sync()  # create the target right away
            except GoogleError as exc:
                query["google"] = f"error: {exc}"
        return RedirectResponse(f"{RETURN_TO}?{urlencode(query)}", status_code=303)

    @router.post("/google/disconnect")
    def disconnect() -> dict:
        _auth().disconnect()
        return {"status": "disconnected"}

    @router.get("/calendars")
    def calendars() -> list[dict]:
        _auth()
        return sync.calendars()

    @router.put("/inputs")
    def set_inputs(body: InputsBody) -> dict:
        return sync.set_inputs([i.model_dump() for i in body.inputs])

    @router.post("/sync")
    def run_sync() -> dict:
        _auth()
        return asdict(sync.sync())

    @router.get("/events")
    def events() -> dict:
        return sync.store()

    return router
