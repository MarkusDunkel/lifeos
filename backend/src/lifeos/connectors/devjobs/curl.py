"""Send GET requests through the ``curl`` command line tool.

devjobs.at answers plain ``curl`` requests from a home connection, but refuses the
Python HTTP client. Only the honest ``User-Agent`` of the request is passed on; the
client never pretends to be a browser. If devjobs.at starts refusing these requests
too, the importer stops and reports it instead of retrying.
"""

from __future__ import annotations

import shutil
import subprocess

import httpx

TIMEOUT = 60
_STATUS_MARK = b"\n__LIFEOS_HTTP_STATUS__:"
# The autorun runs under pythonw (no console): without this flag Windows opens a
# console window for every curl call. 0 on other systems.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class CurlTransport(httpx.BaseTransport):
    def __init__(self, curl: str | None = None) -> None:
        found = curl or shutil.which("curl")
        if not found:
            raise RuntimeError("curl is not installed (it ships with Windows 10 and later)")
        self.curl = found

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            raise httpx.UnsupportedProtocol("CurlTransport only sends GET requests")
        cmd = [
            self.curl,
            "--silent",
            "--show-error",
            "--location",
            "--max-time",
            str(TIMEOUT),
            "--user-agent",
            request.headers.get("user-agent", ""),
            "--write-out",
            _STATUS_MARK.decode() + "%{http_code}",
            str(request.url),
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=TIMEOUT + 10,
                creationflags=_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise httpx.TimeoutException("curl timed out", request=request) from exc
        body, _, status = proc.stdout.rpartition(_STATUS_MARK)
        if proc.returncode != 0 or not status.strip().isdigit():
            message = proc.stderr.decode("utf-8", "replace").strip() or f"exit {proc.returncode}"
            raise httpx.ConnectError(f"curl: {message}", request=request)
        return httpx.Response(int(status), content=body, request=request)
