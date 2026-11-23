"""Small Google Calendar API v3 client (only what LifeOS needs)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from lifeos.connectors.google.auth import GoogleError

API = "https://www.googleapis.com/calendar/v3"


class CalendarApi(Protocol):
    def calendars(self) -> list[dict[str, Any]]: ...

    def events(
        self,
        calendar_id: str,
        time_min: str,
        time_max: str,
        private_property: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def create_calendar(self, summary: str, timezone: str) -> str: ...

    def insert_event(self, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]: ...

    def patch_event(
        self, calendar_id: str, event_id: str, body: dict[str, Any]
    ) -> dict[str, Any]: ...

    def delete_event(self, calendar_id: str, event_id: str) -> None: ...


class GoogleCalendarClient:
    def __init__(
        self, token: Callable[[], str], transport: httpx.BaseTransport | None = None
    ) -> None:
        self._token = token
        self._http = httpx.Client(timeout=30, transport=transport)

    def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token()}"}
        try:
            r = self._http.request(method, API + path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise GoogleError(f"cannot reach Google Calendar: {exc}") from exc
        if r.status_code == 204 or (method == "DELETE" and r.status_code in (404, 410)):
            return {}
        try:
            data = r.json()
        except ValueError as exc:
            raise GoogleError(f"unexpected answer from Google (HTTP {r.status_code})") from exc
        if r.status_code >= 400:
            message = (data.get("error") or {}).get("message") or f"HTTP {r.status_code}"
            raise GoogleError(f"Google Calendar: {message}", 502 if r.status_code >= 500 else 400)
        return data

    def _pages(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(50):  # safety net
            page = self._call(
                "GET", path, params={**params, **({"pageToken": token} if token else {})}
            )
            items += page.get("items", [])
            token = page.get("nextPageToken")
            if not token:
                break
        return items

    def calendars(self) -> list[dict[str, Any]]:
        return self._pages("/users/me/calendarList", {"maxResults": 250})

    def events(
        self,
        calendar_id: str,
        time_min: str,
        time_max: str,
        private_property: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": "true",
            "maxResults": 2500,
        }
        if private_property:
            params["privateExtendedProperty"] = private_property
        return self._pages(f"/calendars/{quote(calendar_id, safe='')}/events", params)

    def create_calendar(self, summary: str, timezone: str) -> str:
        return self._call("POST", "/calendars", json={"summary": summary, "timeZone": timezone})[
            "id"
        ]

    def insert_event(self, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", f"/calendars/{quote(calendar_id, safe='')}/events", json=body)

    def patch_event(self, calendar_id: str, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        path = f"/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}"
        return self._call("PATCH", path, json=body)

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        path = f"/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}"
        self._call("DELETE", path)
