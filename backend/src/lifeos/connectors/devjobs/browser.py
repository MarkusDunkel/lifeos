"""Send GET requests through a real Microsoft Edge window, driven by Playwright.

devjobs.at sits behind Cloudflare, which sends non-browser clients (curl, Python) a
JavaScript challenge they cannot solve. A real Edge solves it on its own; its
clearance cookie is kept in a persistent profile so later runs usually pass without
any challenge. Headless Edge announces itself as "HeadlessChrome" and is stopped by the
challenge, so the window is a normal one, placed off-screen.

Needs the optional ``playwright`` package (``uv sync --extra pc``) and Edge, which ships
with Windows. The browser starts on the first request and stops with :meth:`close`.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

CHALLENGE_WAIT = 45.0  # seconds Edge gets to solve a Cloudflare challenge
NAV_TIMEOUT_MS = 60_000
_OFF_SCREEN = ["--window-position=-32000,-32000", "--window-size=1024,768"]


def default_profile() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "state")
    return Path(base) / "LifeOS" / "jobsearch" / "browser-profile"


def available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def _is_challenge(response: Any) -> bool:
    return response is not None and response.headers.get("cf-mitigated") == "challenge"


class BrowserTransport(httpx.BaseTransport):
    def __init__(
        self,
        profile: Path | None = None,
        channel: str = "msedge",
        launch: Callable[[], tuple[Any, Any]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.profile = profile or default_profile()
        self.channel = channel
        self._launch = launch or self._launch_edge
        self._clock = clock
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None

    def _launch_edge(self) -> tuple[Any, Any]:
        from playwright.sync_api import sync_playwright

        playwright = sync_playwright().start()
        try:
            self.profile.mkdir(parents=True, exist_ok=True)
            context = playwright.chromium.launch_persistent_context(
                str(self.profile),
                channel=self.channel,
                headless=False,
                args=_OFF_SCREEN,
            )
        except Exception:
            playwright.stop()
            raise
        return playwright, context

    def _page_for_requests(self) -> Any:
        if self._page is None:
            try:
                self._playwright, self._context = self._launch()
            except Exception as exc:
                raise httpx.ConnectError(f"cannot start Edge: {exc}") from exc
            pages = self._context.pages
            self._page = pages[0] if pages else self._context.new_page()
            self._page.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        return self._page

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            raise httpx.UnsupportedProtocol("BrowserTransport only sends GET requests")
        page = self._page_for_requests()
        url = str(request.url)
        try:
            response = page.goto(url, wait_until="domcontentloaded")
            if _is_challenge(response):
                self._wait_for_clearance(page)
                response = page.goto(url, wait_until="domcontentloaded")
            if response is None:
                raise httpx.ConnectError(f"Edge got no response for {url}", request=request)
            body = response.body()
        except httpx.HTTPError:
            raise
        except Exception as exc:  # Playwright timeouts and navigation errors
            raise httpx.ConnectError(f"Edge: {exc}", request=request) from exc
        headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower() in ("content-type", "cf-mitigated", "retry-after")
        }
        return httpx.Response(response.status, headers=headers, content=body, request=request)

    def _wait_for_clearance(self, page: Any) -> None:
        """Cloudflare reloads the page once Edge has solved the challenge and sets
        ``cf_clearance``; wait for that cookie (or give up and let the 403 through)."""
        deadline = self._clock() + CHALLENGE_WAIT
        while self._clock() < deadline:
            if any(c.get("name") == "cf_clearance" for c in self._context.cookies()):
                return
            page.wait_for_timeout(500)

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()
            self._playwright = self._context = self._page = None
