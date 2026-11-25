"""Google sign-in via the OAuth 2.0 web flow (authorization code, offline access).

Scopes are chosen so that LifeOS can *read* every calendar but *write* only the
calendars it created itself (``calendar.app.created``): Google enforces that the
user's own calendars stay untouched. Mail is ``gmail.readonly``: LifeOS can search and
read, Google refuses any send, delete, move or label change.

The refresh token is stored in ``LIFEOS_STATE_DIR``, outside the vault.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from lifeos.core.vault import write_atomic

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
SCOPES = " ".join(
    [
        "openid",
        "email",
        "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
        "https://www.googleapis.com/auth/calendar.events.readonly",
        "https://www.googleapis.com/auth/calendar.app.created",
        "https://www.googleapis.com/auth/gmail.readonly",
    ]
)
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
TOKEN_FILE = "google-token.json"
STATE_FILE = "google-oauth-state.json"
STATE_TTL = 900


class GoogleError(Exception):
    """Google rejected a request or the calendar is not usable (HTTP ``status``)."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class GoogleAuth:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        state_dir: Path,
        transport: httpx.BaseTransport | None = None,
        clock=time.time,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_path = state_dir / TOKEN_FILE
        self.state_path = state_dir / STATE_FILE
        self._http = httpx.Client(timeout=30, transport=transport)
        self._clock = clock
        self._lock = threading.Lock()

    # -- storage -------------------------------------------------------------------

    def _load(self) -> dict[str, Any] | None:
        if not self.token_path.is_file():
            return None
        return json.loads(self.token_path.read_text(encoding="utf-8"))

    def _save(self, path: Path, data: dict[str, Any]) -> None:
        write_atomic(path, json.dumps(data, indent=2))
        with contextlib.suppress(OSError):  # e.g. Windows
            os.chmod(path, 0o600)

    @property
    def connected(self) -> bool:
        return self.token_path.is_file()

    def account(self) -> str | None:
        data = self._load()
        return data.get("account") if data else None

    def disconnect(self) -> None:
        data = self._load()
        if data and data.get("refresh_token"):
            with contextlib.suppress(httpx.HTTPError):
                self._http.post(REVOKE_URL, data={"token": data["refresh_token"]})
        self.token_path.unlink(missing_ok=True)

    # -- authorization code flow ---------------------------------------------------

    def authorize_url(self, redirect_uri: str) -> str:
        """Where to send the browser; Google redirects back to ``redirect_uri``."""
        state = secrets.token_urlsafe(24)
        self._save(
            self.state_path,
            {"state": state, "redirect_uri": redirect_uri, "expires_at": self._clock() + STATE_TTL},
        )
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPES,
            "access_type": "offline",
            "prompt": "consent",  # always hand out a refresh token
            "include_granted_scopes": "true",
            "state": state,
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    def complete(self, code: str, state: str) -> str:
        """Exchange the code from the callback; returns the account's e-mail (or "")."""
        pending = None
        if self.state_path.is_file():
            pending = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.state_path.unlink(missing_ok=True)
        if (
            not pending
            or not secrets.compare_digest(str(pending.get("state", "")), state)
            or pending.get("expires_at", 0) < self._clock()
        ):
            raise GoogleError("sign-in expired or was not started here, please try again", 400)
        status, data = self._post(
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": pending["redirect_uri"],
            }
        )
        if status != 200:
            raise GoogleError(_describe(data), 400)
        granted = set(str(data.get("scope", SCOPES)).split())
        needed = set(SCOPES.split()) - {"openid", "email"}
        missing = sorted(s.rsplit("/", 1)[-1] for s in needed - granted)
        if missing:
            raise GoogleError(f"please allow all requested permissions (missing: {missing})", 400)
        account = _email_of(data.get("id_token", ""))
        self._store(data, account)
        return account

    def _post(self, form: dict[str, str]) -> tuple[int, dict[str, Any]]:
        try:
            r = self._http.post(TOKEN_URL, data=form)
        except httpx.HTTPError as exc:
            raise GoogleError(f"cannot reach Google: {exc}") from exc
        try:
            return r.status_code, r.json()
        except ValueError as exc:
            raise GoogleError(f"unexpected answer from Google (HTTP {r.status_code})") from exc

    def _store(self, data: dict[str, Any], account: str | None = None) -> None:
        previous = self._load() or {}
        self._save(
            self.token_path,
            {
                "refresh_token": data.get("refresh_token") or previous.get("refresh_token"),
                "access_token": data["access_token"],
                "expires_at": self._clock() + int(data.get("expires_in", 3600)) - 60,
                "account": account or previous.get("account"),
                # granted permissions, for the control room (refreshes may omit them)
                "scope": data.get("scope") or previous.get("scope"),
            },
        )

    # -- access tokens -------------------------------------------------------------

    def access_token(self) -> str:
        with self._lock:
            data = self._load()
            if not data:
                raise GoogleError("not connected to Google (Plan → Calendars → Connect)", 400)
            if data.get("access_token") and data.get("expires_at", 0) > self._clock():
                return data["access_token"]
            status, answer = self._post(
                {
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": data.get("refresh_token") or "",
                }
            )
            if status != 200:
                raise GoogleError(
                    f"Google sign-in expired, please connect again ({_describe(answer)})", 409
                )
            self._store(answer)
            return answer["access_token"]


def _email_of(id_token: str) -> str:
    """The e-mail claim of an ID token, for display only (not verified)."""
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return str(claims.get("email", ""))
    except (IndexError, ValueError):
        return ""


def _describe(data: dict[str, Any]) -> str:
    text = data.get("error_description") or data.get("error") or "unknown error"
    return str(text).splitlines()[0]
