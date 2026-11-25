"""Read-only Gmail API client: search and read messages, nothing else.

The token carries only ``gmail.readonly``, so Google itself refuses any write. On top of
that this client only ever sends GET requests.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from lifeos.connectors.google.auth import GoogleError

API = "https://gmail.googleapis.com/gmail/v1/users/me"


@dataclass
class MailBody:
    text: str
    html: str


class GmailClient:
    def __init__(
        self, token: Callable[[], str], transport: httpx.BaseTransport | None = None
    ) -> None:
        self._token = token
        self._http = httpx.Client(timeout=30, transport=transport)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token()}"}
        try:
            r = self._http.get(API + path, headers=headers, params=params)
        except httpx.HTTPError as exc:
            raise GoogleError(f"cannot reach Gmail: {exc}") from exc
        try:
            data = r.json()
        except ValueError as exc:
            raise GoogleError(f"unexpected answer from Gmail (HTTP {r.status_code})") from exc
        if r.status_code >= 400:
            message = (data.get("error") or {}).get("message") or f"HTTP {r.status_code}"
            if r.status_code == 403 and "scope" in message.lower():
                message = "no mail permission yet, connect Google again (Plan → Calendars)"
            raise GoogleError(f"Gmail: {message}", 502 if r.status_code >= 500 else 400)
        return data

    def search(self, query: str, limit: int = 50) -> list[str]:
        """Message ids matching a Gmail search (newest first), at most ``limit``."""
        ids: list[str] = []
        token: str | None = None
        while len(ids) < limit:
            params: dict[str, Any] = {"q": query, "maxResults": min(500, limit - len(ids))}
            if token:
                params["pageToken"] = token
            page = self._get("/messages", params)
            ids += [m["id"] for m in page.get("messages", [])]
            token = page.get("nextPageToken")
            if not token:
                break
        return ids[:limit]

    def message(self, message_id: str) -> dict[str, Any]:
        return self._get(f"/messages/{quote(message_id, safe='')}", {"format": "full"})


def headers(message: dict[str, Any]) -> dict[str, str]:
    """Header name (lower case) → value of a ``format=full`` message."""
    return {
        h["name"].lower(): h["value"]
        for h in (message.get("payload") or {}).get("headers", [])
        if "name" in h and "value" in h
    }


def body(message: dict[str, Any]) -> MailBody:
    """The first text/plain and text/html part, decoded."""
    found = {"text/plain": "", "text/html": ""}

    def walk(part: dict[str, Any]) -> None:
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if mime in found and data and not found[mime]:
            found[mime] = _decode(data, _charset(part))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(message.get("payload") or {})
    return MailBody(found["text/plain"], found["text/html"])


def summary(message: dict[str, Any]) -> dict[str, Any]:
    h = headers(message)
    return {
        "id": message.get("id"),
        "thread_id": message.get("threadId"),
        "date": h.get("date", ""),
        "from": h.get("from", ""),
        "to": h.get("to", ""),
        "subject": h.get("subject", ""),
        "snippet": message.get("snippet", ""),
        "labels": message.get("labelIds", []),
    }


def _charset(part: dict[str, Any]) -> str:
    for h in part.get("headers", []) or []:
        if h.get("name", "").lower() == "content-type" and "charset=" in h.get("value", ""):
            return h["value"].split("charset=", 1)[1].split(";")[0].strip().strip('"')
    return "utf-8"


def _decode(data: str, charset: str) -> str:
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    try:
        return raw.decode(charset or "utf-8")
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")
