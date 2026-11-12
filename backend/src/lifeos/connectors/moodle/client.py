"""Read-only client for the Moodle web service API (the one the Moodle app uses)."""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

SERVICE = "moodle_mobile_app"


class MoodleError(Exception):
    """Moodle rejected a request or is not usable (HTTP ``status`` for the API)."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class MoodleApi(Protocol):
    def site_info(self) -> dict[str, Any]: ...
    def courses(self, userid: int) -> list[dict[str, Any]]: ...
    def contents(self, courseid: int) -> list[dict[str, Any]]: ...
    def download(self, fileurl: str) -> bytes: ...


def _flatten(params: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Moodle's REST format: ``courseids[0]=1``, ``options[0][name]=x``."""
    out: dict[str, str] = {}
    for key, value in params.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            out.update(_flatten(value, name))
        elif isinstance(value, list | tuple):
            out.update(_flatten(dict(enumerate(value)), name))
        elif isinstance(value, bool):
            out[name] = "1" if value else "0"
        else:
            out[name] = str(value)
    return out


def normalise_url(url: str) -> str:
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc:
        raise MoodleError("LIFEOS_MOODLE_URL must be an https:// address", 400)
    return url


def request_token(
    base_url: str, username: str, password: str, transport: httpx.BaseTransport | None = None
) -> str:
    """Exchange username and password for a web service token (the password is not kept)."""
    with httpx.Client(timeout=30, transport=transport) as http:
        try:
            r = http.post(
                f"{normalise_url(base_url)}/login/token.php",
                data={"username": username, "password": password, "service": SERVICE},
            )
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MoodleError(f"cannot reach Moodle: {exc}") from exc
    if not data.get("token"):
        message = data.get("error") or "Moodle did not return a token"
        raise MoodleError(f"login failed: {message}", 400)
    return data["token"]


class MoodleClient:
    def __init__(self, base_url: str, token: str, transport: httpx.BaseTransport | None = None):
        self.base_url = normalise_url(base_url)
        self._host = urlsplit(self.base_url).netloc
        self._token = token
        self._http = httpx.Client(timeout=120, transport=transport, follow_redirects=True)

    def call(self, function: str, **params: Any) -> Any:
        form = {
            "wstoken": self._token,
            "wsfunction": function,
            "moodlewsrestformat": "json",
            **_flatten(params),
        }
        try:
            r = self._http.post(f"{self.base_url}/webservice/rest/server.php", data=form)
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MoodleError(f"cannot reach Moodle: {exc}") from exc
        if isinstance(data, dict) and data.get("exception"):
            code = data.get("errorcode", "")
            if code in ("invalidtoken", "accessexception"):
                raise MoodleError("Moodle rejected the access, please connect again", 409)
            raise MoodleError(f"{function}: {data.get('message') or code}")
        return data

    def site_info(self) -> dict[str, Any]:
        return self.call("core_webservice_get_site_info")

    def courses(self, userid: int) -> list[dict[str, Any]]:
        return self.call("core_enrol_get_users_courses", userid=userid)

    def contents(self, courseid: int) -> list[dict[str, Any]]:
        return self.call("core_course_get_contents", courseid=courseid)

    def download(self, fileurl: str) -> bytes:
        # The token must never be sent to another host.
        parts = urlsplit(fileurl)
        if parts.scheme != "https" or parts.netloc != self._host:
            raise MoodleError(f"refusing to download from another host: {parts.netloc}")
        try:
            r = self._http.get(fileurl, params={"token": self._token})
        except httpx.HTTPError as exc:
            raise MoodleError(f"download failed: {exc}") from exc
        if r.status_code >= 400:
            raise MoodleError(f"download failed (HTTP {r.status_code})")
        if r.headers.get("content-type", "").startswith("application/json"):
            # Moodle answers errors with JSON even on file URLs.
            try:
                message = r.json().get("error") or r.json().get("message")
            except ValueError:
                message = None
            if message:
                raise MoodleError(f"download failed: {message}")
        return r.content
