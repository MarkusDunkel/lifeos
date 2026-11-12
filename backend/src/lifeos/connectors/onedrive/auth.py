"""Microsoft sign-in via the OAuth 2.0 device code flow (personal accounts).

The refresh token is stored in ``LIFEOS_STATE_DIR`` — outside the vault, so it
never ends up in the vault's git history.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from lifeos.core.vault import write_atomic

AUTHORITY = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
SCOPES = "Files.Read offline_access"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
TOKEN_FILE = "onedrive-token.json"


class OneDriveError(Exception):
    """Microsoft rejected a request or OneDrive is not usable (HTTP ``status``)."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


@dataclass
class DeviceFlow:
    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_at: float


class MicrosoftAuth:
    def __init__(
        self,
        client_id: str,
        state_dir: Path,
        transport: httpx.BaseTransport | None = None,
        clock=time.time,
    ):
        self.client_id = client_id
        self.token_path = state_dir / TOKEN_FILE
        self._http = httpx.Client(timeout=30, transport=transport)
        self._clock = clock
        self._flow: DeviceFlow | None = None
        self._lock = threading.Lock()

    # -- token storage -----------------------------------------------------------

    def _load(self) -> dict[str, Any] | None:
        if not self.token_path.is_file():
            return None
        return json.loads(self.token_path.read_text(encoding="utf-8"))

    def _save(self, data: dict[str, Any]) -> None:
        write_atomic(self.token_path, json.dumps(data, indent=2))
        with contextlib.suppress(OSError):  # e.g. Windows
            os.chmod(self.token_path, 0o600)

    @property
    def connected(self) -> bool:
        return self.token_path.is_file()

    def account(self) -> str | None:
        data = self._load()
        return data.get("account") if data else None

    def set_account(self, name: str | None) -> None:
        data = self._load()
        if data is not None and name and data.get("account") != name:
            data["account"] = name
            self._save(data)

    def disconnect(self) -> None:
        self._flow = None
        self.token_path.unlink(missing_ok=True)

    # -- device code flow --------------------------------------------------------

    def _post(self, endpoint: str, form: dict[str, str]) -> tuple[int, dict[str, Any]]:
        try:
            r = self._http.post(f"{AUTHORITY}/{endpoint}", data=form)
        except httpx.HTTPError as exc:
            raise OneDriveError(f"cannot reach Microsoft: {exc}") from exc
        try:
            return r.status_code, r.json()
        except ValueError as exc:
            raise OneDriveError(f"unexpected answer from Microsoft (HTTP {r.status_code})") from exc

    def start(self) -> dict[str, Any]:
        """Begin sign-in; the user enters ``user_code`` at ``verification_uri``."""
        status, data = self._post("devicecode", {"client_id": self.client_id, "scope": SCOPES})
        if status != 200:
            raise OneDriveError(_describe(data), 400)
        self._flow = DeviceFlow(
            device_code=data["device_code"],
            user_code=data["user_code"],
            verification_uri=data["verification_uri"],
            interval=int(data.get("interval", 5)),
            expires_at=self._clock() + int(data.get("expires_in", 900)),
        )
        return {
            "user_code": self._flow.user_code,
            "verification_uri": self._flow.verification_uri,
            "interval": self._flow.interval,
            "expires_in": int(data.get("expires_in", 900)),
        }

    def poll(self) -> str:
        """One poll of a running sign-in: ``pending``, ``connected``, ``expired`` or ``denied``."""
        flow = self._flow
        if flow is None:
            return "connected" if self.connected else "expired"
        if self._clock() > flow.expires_at:
            self._flow = None
            return "expired"
        status, data = self._post(
            "token",
            {
                "grant_type": DEVICE_GRANT,
                "client_id": self.client_id,
                "device_code": flow.device_code,
            },
        )
        if status == 200:
            self._store(data)
            self._flow = None
            return "connected"
        error = data.get("error")
        if error in ("authorization_pending", "slow_down"):
            return "pending"
        self._flow = None
        if error == "expired_token":
            return "expired"
        if error in ("access_denied", "authorization_declined"):
            return "denied"
        raise OneDriveError(_describe(data), 400)

    def _store(self, data: dict[str, Any]) -> None:
        previous = self._load() or {}
        self._save(
            {
                "refresh_token": data.get("refresh_token") or previous.get("refresh_token"),
                "access_token": data["access_token"],
                "expires_at": self._clock() + int(data.get("expires_in", 3600)) - 60,
                "account": previous.get("account"),
            }
        )

    # -- access tokens -----------------------------------------------------------

    def access_token(self) -> str:
        with self._lock:
            data = self._load()
            if not data:
                raise OneDriveError("not connected to Microsoft (Settings → Connect)", 400)
            if data.get("access_token") and data.get("expires_at", 0) > self._clock():
                return data["access_token"]
            status, answer = self._post(
                "token",
                {
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "refresh_token": data.get("refresh_token") or "",
                    "scope": SCOPES,
                },
            )
            if status != 200:
                raise OneDriveError(
                    f"sign-in expired, please connect again ({_describe(answer)})", 409
                )
            self._store(answer)
            return answer["access_token"]


def _describe(data: dict[str, Any]) -> str:
    text = data.get("error_description") or data.get("error") or "unknown error"
    return str(text).splitlines()[0]
