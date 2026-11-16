"""A small job scheduler for the worker.

Two kinds of schedule: every N seconds, or daily at a local time. The last run of every
job is stored in ``state/jobs.json``, so a restart neither repeats a daily job nor
forgets one: a daily job missed while the server was down runs as soon as the worker is
back. A failed daily run is retried after ``retry_after`` seconds, at most
``max_attempts`` times per day.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("lifeos.worker")

OK_STATES = ("ok", "skipped")


@dataclass
class JobResult:
    status: str  # ok | skipped | error | conflict
    message: str = ""


@dataclass
class Every:
    seconds: int

    def due(self, now: datetime, state: dict) -> bool:
        last = _parse(state.get("lastRun"))
        return last is None or (now - last).total_seconds() >= self.seconds


@dataclass
class Daily:
    at: time
    tz: ZoneInfo
    retry_after: int = 3600
    max_attempts: int = 3

    def slot(self, now: datetime) -> datetime:
        """Today's scheduled time (in UTC) for ``now``."""
        local = now.astimezone(self.tz)
        return datetime.combine(local.date(), self.at, tzinfo=self.tz).astimezone(UTC)

    def due(self, now: datetime, state: dict) -> bool:
        slot = self.slot(now)
        if now < slot:
            return False
        success = _parse(state.get("lastSuccess"))
        if success is not None and success >= slot:
            return False
        last = _parse(state.get("lastRun"))
        if last is None or last < slot:
            return True  # first attempt for today's slot
        if state.get("attempts", 0) >= self.max_attempts:
            return False
        return (now - last).total_seconds() >= self.retry_after


@dataclass
class Job:
    name: str
    schedule: Every | Daily
    run: Callable[[], JobResult]


class Scheduler:
    def __init__(
        self,
        jobs: list[Job],
        state_file: Path | None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.jobs = jobs
        self.state_file = state_file
        self.clock = clock
        self.state: dict[str, dict] = self._load()

    def tick(self) -> list[str]:
        """Run every job that is due now; returns the names of the jobs that ran."""
        ran = []
        for job in self.jobs:
            now = self.clock()
            entry = self.state.setdefault(job.name, {})
            if not job.schedule.due(now, entry):
                continue
            result = self._run(job)
            self._record(job, entry, now, result)
            ran.append(job.name)
        if ran:
            self._save()
        return ran

    def _run(self, job: Job) -> JobResult:
        try:
            result = job.run()
        except Exception as exc:  # a job must never stop the worker
            log.exception("job %s failed", job.name)
            result = JobResult("error", f"{type(exc).__name__}: {exc}")
        level = logging.INFO if result.status in OK_STATES else logging.WARNING
        log.log(level, "%s %s: %s", job.name, result.status, result.message)
        return result

    def _record(self, job: Job, entry: dict, now: datetime, result: JobResult) -> None:
        if isinstance(job.schedule, Daily):
            slot = job.schedule.slot(now)
            previous = _parse(entry.get("lastRun"))
            first_today = previous is None or previous < slot
            entry["attempts"] = 1 if first_today else entry.get("attempts", 0) + 1
        entry["lastRun"] = now.isoformat(timespec="seconds")
        entry["status"] = result.status
        entry["message"] = result.message
        if result.status in OK_STATES:
            entry["lastSuccess"] = entry["lastRun"]

    def status(self) -> dict[str, dict]:
        return self._load()

    def _load(self) -> dict[str, dict]:
        if self.state_file and self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("ignoring unreadable %s", self.state_file)
        return {}

    def _save(self) -> None:
        if not self.state_file:
            return
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        tmp.replace(self.state_file)


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def parse_time(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))
