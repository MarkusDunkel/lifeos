"""PC side: tell the control room on the server how a PC task ended.

Reads ``LIFEOS_CONTROL_SERVER_URL`` (falls back to ``LIFEOS_JOBSEARCH_SERVER_URL``) and
``LIFEOS_CONTROL_REPORT_TOKEN`` from the task's env file. A report never fails the task:
when the server is unreachable the run is only missing on the page.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("lifeos.control")


class ReporterSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LIFEOS_", extra="ignore")

    control_server_url: str | None = None
    jobsearch_server_url: str | None = None
    control_report_token: str | None = None

    @property
    def url(self) -> str | None:
        return self.control_server_url or self.jobsearch_server_url


def load_settings(env_file: Path | None) -> ReporterSettings:
    if env_file is not None:
        return ReporterSettings(_env_file=env_file, _env_file_encoding="utf-8")  # type: ignore[call-arg]
    return ReporterSettings()


def send(
    process: str,
    status: str,
    message: str,
    duration: float | None,
    settings: ReporterSettings,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    if not settings.url or not settings.control_report_token:
        log.info("control report skipped: LIFEOS_CONTROL_* not set")
        return False
    try:
        with httpx.Client(timeout=15, transport=transport) as http:
            r = http.post(
                settings.url.rstrip("/") + f"/api/control/report/{process}",
                json={"status": status, "message": message[:2000], "duration": duration},
                headers={"Authorization": f"Bearer {settings.control_report_token}"},
            )
        if r.status_code != 200:
            log.warning("control report refused: HTTP %s", r.status_code)
            return False
        return True
    except httpx.HTTPError as exc:
        log.warning("control report failed: %s", exc)
        return False


class Run:
    """Outcome of a reported run; the task sets ``status`` and ``message``."""

    def __init__(self) -> None:
        self.status = "error"
        self.message = "stopped unexpectedly"


@contextmanager
def reported(process: str, env_file: Path | None) -> Iterator[Run]:
    """``with reported("teams-files", env) as run: ...`` – reports even when it raises."""
    run = Run()
    started = time.monotonic()
    try:
        yield run
    except BaseException as exc:
        run.status, run.message = "error", f"{type(exc).__name__}: {exc}"
        raise
    finally:
        send(process, run.status, run.message, time.monotonic() - started, load_settings(env_file))
