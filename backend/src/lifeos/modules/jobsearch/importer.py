"""The job importer that runs on the user's PC: ``lifeos jobsearch import``.

devjobs.at blocks data-centre IPs, so postings are fetched from a home connection and
sent to the LifeOS server, which keeps them and evaluates them. The server remembers
which postings it has, so an interrupted import simply continues on the next run.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

from lifeos.connectors.devjobs.client import DevjobsClient, DevjobsError
from lifeos.connectors.devjobs.parse import SOURCE, ParseError
from lifeos.modules.jobsearch.model import Posting

BATCH = 25
RETRY_DELAYS = (5.0, 20.0, 60.0)
TRANSIENT_STATUS = {502, 503, 504}


class ImporterSettings(BaseSettings):
    """Only what the PC needs: where the server is and the import token."""

    model_config = SettingsConfigDict(env_prefix="LIFEOS_", extra="ignore")

    jobsearch_server_url: str | None = None
    jobsearch_import_token: str | None = None

    def missing(self) -> list[str]:
        return [
            name
            for name, value in (
                ("LIFEOS_JOBSEARCH_SERVER_URL", self.jobsearch_server_url),
                ("LIFEOS_JOBSEARCH_IMPORT_TOKEN", self.jobsearch_import_token),
            )
            if not value
        ]


def load_settings(env_file: Path | None) -> ImporterSettings:
    if env_file is not None:
        return ImporterSettings(_env_file=env_file, _env_file_encoding="utf-8")  # type: ignore[call-arg]
    return ImporterSettings()


class ServerError(Exception):
    pass


class ImportServer:
    """Client for ``/api/jobsearch/import/*`` on the LifeOS server."""

    def __init__(
        self,
        base_url: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/jobsearch/import",
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
            transport=transport,
        )
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, path: str, json: Any = None, retry: bool = True) -> dict[str, Any]:
        """Network trouble and a restarting server (502–504) are retried with growing
        pauses; anything else fails at once."""
        delays = RETRY_DELAYS if retry else ()
        for attempt in range(len(delays) + 1):
            try:
                response = self._http.request(method, path, json=json)
            except httpx.HTTPError as exc:
                problem: str | None = f"cannot reach the LifeOS server: {exc}"
            else:
                if response.status_code not in TRANSIENT_STATUS:
                    break
                problem = f"LifeOS server: HTTP {response.status_code}"
            if attempt == len(delays):
                raise ServerError(problem)
            self._sleep(delays[attempt])
        if response.status_code == 401:
            raise ServerError("the LifeOS server rejected the import token")
        if response.status_code >= 400:
            raise ServerError(f"LifeOS server: HTTP {response.status_code} {response.text[:200]}")
        return response.json()

    def status(self) -> dict[str, Any]:
        return self._call("GET", "/status", retry=False)  # autorun: offline means "not now"

    def diff(self, source: str, ids: list[str]) -> dict[str, Any]:
        return self._call("POST", "/diff", {"source": source, "ids": ids})

    def postings(self, postings: list[Posting]) -> dict[str, int]:
        payload = {"postings": [p.model_dump(mode="json") for p in postings]}
        return self._call("POST", "/postings", payload)

    def finish(self, run: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", "/finish", run)


@dataclass
class ImportResult:
    listed: int = 0
    new: int = 0  # postings sent to the server
    duplicates: int = 0  # of which the same job as an active posting
    reposts: int = 0  # of which a job that was offline before
    pending: int = 0  # unknown postings before this run
    gone: int = 0
    failed: int = 0
    complete: bool = True
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        text = f"{self.new} new posting(s) imported"
        if self.duplicates:
            text += f" ({self.duplicates} duplicate(s))"
        if self.pending > self.new:
            text += f", {self.pending - self.new} still to fetch"
        if self.gone:
            text += f", {self.gone} gone"
        if self.failed:
            text += f", {self.failed} failed"
        return text


def run_import(
    server: ImportServer,
    devjobs: DevjobsClient,
    limit: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> ImportResult:
    """Fetch the postings the server does not know yet and send them in batches."""
    started = datetime.now(UTC).isoformat(timespec="seconds")
    result = ImportResult()
    batch: list[Posting] = []

    def flush() -> None:
        if batch:
            answer = server.postings(batch)
            result.new += answer["stored"]
            result.duplicates += answer.get("duplicates", 0)
            result.reposts += answer.get("reposts", 0)
            batch.clear()

    devjobs.check_robots()
    ids = devjobs.sitemap_ids()
    result.listed = len(ids)
    diff = server.diff(SOURCE, ids)
    unknown: list[str] = diff["unknown"]
    result.pending, result.gone = len(unknown), diff.get("gone", 0)
    todo = unknown[:limit] if limit is not None else unknown
    result.complete = len(todo) == len(unknown)
    try:
        for done, posting_id in enumerate(todo, 1):
            try:
                batch.append(devjobs.posting(posting_id))
            except ParseError as exc:
                result.failed += 1
                result.errors.append(str(exc))
            except DevjobsError as exc:
                if exc.status != 404:  # 403 / network trouble: stop, retry next run
                    raise
                result.failed += 1  # removed between sitemap and fetch
            if len(batch) >= BATCH:
                flush()
            if progress:
                progress(done, len(todo))
    except (DevjobsError, KeyboardInterrupt) as exc:
        result.complete = False
        result.errors.append(f"interrupted: {exc or type(exc).__name__}")
        raise
    finally:
        try:
            flush()
        finally:
            server.finish(
                {
                    "source": SOURCE,
                    "started": started,
                    "listed": result.listed,
                    "new": result.new,
                    "duplicates": result.duplicates,
                    "reposts": result.reposts,
                    "failed": result.failed,
                    "complete": result.complete,
                    "errors": result.errors[-20:],
                }
            )
    return result
