"""BrowserTransport with a fake Playwright context (no real browser is started)."""

from __future__ import annotations

import httpx
import pytest

from lifeos.connectors.devjobs.browser import BrowserTransport
from lifeos.connectors.devjobs.client import DevjobsClient, DevjobsError


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"", challenge: bool = False) -> None:
        self.status = status
        self._body = body
        self.headers = {"content-type": "text/plain; charset=utf-8"}
        if challenge:
            self.headers["cf-mitigated"] = "challenge"

    def body(self) -> bytes:
        return self._body


class FakePage:
    def __init__(self, answers: list[FakeResponse | Exception], context: FakeContext) -> None:
        self.answers = answers
        self.context = context
        self.visited: list[str] = []
        self.waited = 0

    def set_default_navigation_timeout(self, ms: int) -> None:
        pass

    def goto(self, url: str, wait_until: str) -> FakeResponse:
        self.visited.append(url)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def wait_for_timeout(self, ms: float) -> None:
        self.waited += 1
        self.context.clock += ms / 1000
        if self.waited >= self.context.solve_after:
            self.context.jar = [{"name": "cf_clearance", "value": "x"}]


class FakeContext:
    def __init__(self, answers: list[FakeResponse | Exception], solve_after: int = 3) -> None:
        self.clock = 0.0
        self.solve_after = solve_after
        self.jar: list[dict[str, str]] = []
        self.page = FakePage(answers, self)
        self.pages = [self.page]
        self.closed = False

    def cookies(self) -> list[dict[str, str]]:
        return self.jar

    def close(self) -> None:
        self.closed = True


class FakePlaywright:
    stopped = False

    def stop(self) -> None:
        self.stopped = True


def transport(ctx: FakeContext) -> tuple[BrowserTransport, FakePlaywright]:
    pw = FakePlaywright()
    return BrowserTransport(launch=lambda: (pw, ctx), clock=lambda: ctx.clock), pw


def test_plain_page_is_returned_as_is() -> None:
    ctx = FakeContext([FakeResponse(200, b"hello")])
    t, pw = transport(ctx)
    with httpx.Client(transport=t) as http:
        r = http.get("https://devjobs.at/robots.txt")
        assert r.status_code == 200 and r.text == "hello"
    assert ctx.closed and pw.stopped


def test_challenge_is_waited_for_and_page_reloaded() -> None:
    ctx = FakeContext([FakeResponse(403, challenge=True), FakeResponse(200, b"<urlset/>")])
    t, _ = transport(ctx)
    with httpx.Client(transport=t) as http:
        assert http.get("https://devjobs.at/sitemap-job.xml").text == "<urlset/>"
    assert ctx.page.visited == ["https://devjobs.at/sitemap-job.xml"] * 2
    assert ctx.page.waited == 3


def test_unsolved_challenge_becomes_a_clear_403() -> None:
    ctx = FakeContext(
        [FakeResponse(403, challenge=True), FakeResponse(403, challenge=True)],
        solve_after=10**6,
    )
    t, _ = transport(ctx)
    client = DevjobsClient(t, sleep=lambda s: None)
    with pytest.raises(DevjobsError) as err:
        client.sitemap_ids()
    assert err.value.status == 403 and "Cloudflare challenge" in str(err.value)
    client.close()


def test_browser_errors_become_connect_errors() -> None:
    ctx = FakeContext([TimeoutError("navigation timeout")])
    t, _ = transport(ctx)
    with httpx.Client(transport=t) as http, pytest.raises(httpx.ConnectError):
        http.get("https://devjobs.at/")


def test_failed_launch_is_a_connect_error() -> None:
    def broken() -> tuple[object, object]:
        raise RuntimeError("Edge not found")

    with (
        httpx.Client(transport=BrowserTransport(launch=broken)) as http,
        pytest.raises(httpx.ConnectError, match="cannot start Edge"),
    ):
        http.get("https://devjobs.at/")
