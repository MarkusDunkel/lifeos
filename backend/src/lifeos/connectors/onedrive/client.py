"""Read-only Microsoft Graph client for the presentations folder."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from lifeos.connectors.onedrive.auth import OneDriveError

GRAPH = "https://graph.microsoft.com/v1.0"
SELECT = "id,name,cTag,webUrl,lastModifiedDateTime,size,folder,file"
EXTENSIONS = (".pptx",)


@dataclass
class DriveFile:
    id: str
    path: str  # relative to the mirrored folder, POSIX, including the file name
    ctag: str
    web_url: str
    modified: str
    size: int


class GraphApi(Protocol):
    def account(self) -> str | None: ...
    def list_files(self, folder: str) -> list[DriveFile]: ...
    def download(self, item_id: str) -> bytes: ...


class GraphClient:
    def __init__(self, token: Callable[[], str], transport: httpx.BaseTransport | None = None):
        self._token = token
        self._http = httpx.Client(timeout=60, transport=transport, follow_redirects=True)

    def _get(self, url: str) -> httpx.Response:
        if not url.startswith("https://"):
            url = GRAPH + url
        try:
            r = self._http.get(url, headers={"Authorization": f"Bearer {self._token()}"})
        except httpx.HTTPError as exc:
            raise OneDriveError(f"cannot reach OneDrive: {exc}") from exc
        if r.status_code == 401:
            raise OneDriveError("OneDrive rejected the sign-in, please connect again", 409)
        if r.status_code >= 400:
            raise OneDriveError(_error(r), 404 if r.status_code == 404 else 502)
        return r

    def account(self) -> str | None:
        owner = self._get("/me/drive?$select=owner").json().get("owner") or {}
        return (owner.get("user") or {}).get("displayName")

    def _children(self, url: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        next_url: str | None = url
        while next_url:
            page = self._get(next_url).json()
            out += page.get("value", [])
            next_url = page.get("@odata.nextLink")
        return out

    def list_files(self, folder: str) -> list[DriveFile]:
        """All presentations below ``folder`` (recursive)."""
        clean = folder.strip("/")
        try:
            items = self._children(f"/me/drive/root:/{quote(clean)}:/children?$select={SELECT}")
        except OneDriveError as exc:
            if exc.status == 404:
                raise OneDriveError(f"OneDrive folder '{clean}' not found", 404) from exc
            raise
        files: list[DriveFile] = []
        stack = [("", items)]
        while stack:
            prefix, entries = stack.pop()
            for item in entries:
                name = item["name"]
                rel = f"{prefix}{name}"
                if "folder" in item:
                    url = f"/me/drive/items/{item['id']}/children?$select={SELECT}"
                    stack.append((rel + "/", self._children(url)))
                elif "file" in item and name.lower().endswith(EXTENSIONS):
                    files.append(
                        DriveFile(
                            id=item["id"],
                            path=rel,
                            ctag=item.get("cTag", ""),
                            web_url=item.get("webUrl", ""),
                            modified=item.get("lastModifiedDateTime", ""),
                            size=int(item.get("size", 0)),
                        )
                    )
        return sorted(files, key=lambda f: f.path.casefold())

    def download(self, item_id: str) -> bytes:
        return self._get(f"/me/drive/items/{quote(item_id)}/content").content


def _error(r: httpx.Response) -> str:
    try:
        message = r.json().get("error", {}).get("message")
    except ValueError:
        message = None
    return f"OneDrive answered HTTP {r.status_code}" + (f": {message}" if message else "")
