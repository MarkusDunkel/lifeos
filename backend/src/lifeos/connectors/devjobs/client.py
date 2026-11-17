"""Polite, read-only client for devjobs.at.

Only what ``robots.txt`` allows is fetched: the job sitemap and single posting pages
(``/job/<id>``), never the search pages. Between requests the client waits at least
one second or twice the last response time, the rule devjobs.at states for its own
crawler. devjobs.at blocks data-centre IPs, so this runs on the user's PC; requests go
through ``curl`` (see :mod:`lifeos.connectors.devjobs.curl`) with an honest User-Agent.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib import robotparser

import httpx

from lifeos.connectors.devjobs.curl import CurlTransport
from lifeos.connectors.devjobs.parse import BASE_URL, parse_posting, posting_url, sitemap_ids
from lifeos.modules.jobsearch.model import Posting

USER_AGENT = "LifeOS-jobsearch/0.1 (personal job search)"
SITEMAP = f"{BASE_URL}/sitemap-job.xml"
MIN_DELAY = 1.0
RETRY_DELAYS = (10.0, 30.0, 90.0)
TRANSIENT_STATUS = {502, 503, 504}


class DevjobsError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class DevjobsClient:
    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = httpx.Client(
            timeout=60,
            transport=transport if transport is not None else CurlTransport(),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        self._sleep = sleep
        self._clock = clock
        self._next_allowed = 0.0
        self._robots: robotparser.RobotFileParser | None = None

    def close(self) -> None:
        self._http.close()

    def check_robots(self) -> None:
        """Fail early if robots.txt no longer allows the sitemap or posting pages."""
        robots = robotparser.RobotFileParser()
        robots.parse(self._get(f"{BASE_URL}/robots.txt").splitlines())
        self._robots = robots
        for url in (SITEMAP, posting_url("0" * 32)):
            if not robots.can_fetch(USER_AGENT, url):
                raise DevjobsError(f"robots.txt of devjobs.at disallows {url}", 409)

    def sitemap_ids(self) -> list[str]:
        ids = sitemap_ids(self._get(SITEMAP))
        if not ids:
            raise DevjobsError("the devjobs.at sitemap lists no postings")
        return ids

    def posting(self, posting_id: str) -> Posting:
        return parse_posting(posting_id, self._get(posting_url(posting_id)))

    def _get(self, url: str) -> str:
        """Network trouble and 502–504 are retried after growing pauses; a refusal
        (403) or rate limit (429) is never retried."""
        for attempt in range(len(RETRY_DELAYS) + 1):
            wait = self._next_allowed - self._clock()
            if wait > 0:
                self._sleep(wait)
            started = self._clock()
            try:
                response = self._http.get(url)
            except httpx.HTTPError as exc:
                problem = DevjobsError(f"cannot reach devjobs.at: {exc}")
            else:
                if response.status_code not in TRANSIENT_STATUS:
                    break
                problem = DevjobsError(f"devjobs.at answered HTTP {response.status_code}")
            finally:
                elapsed = self._clock() - started
                self._next_allowed = self._clock() + max(MIN_DELAY, 2 * elapsed)
            if attempt == len(RETRY_DELAYS):
                raise problem
            self._sleep(RETRY_DELAYS[attempt])
        if response.status_code == 403:
            raise DevjobsError(
                "devjobs.at refused the request (HTTP 403); it blocks data-centre IPs, "
                "run the import on a home connection",
                403,
            )
        if response.status_code in (404, 410):  # removed since the sitemap was read
            raise DevjobsError(f"not found: {url}", 404)
        if response.status_code >= 400:
            raise DevjobsError(f"devjobs.at answered HTTP {response.status_code} for {url}")
        return response.text
