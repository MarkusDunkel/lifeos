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
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from lifeos.core import tokens
from lifeos.core.times import parse_iso as _parse

log = logging.getLogger("lifeos.worker")

OK_STATES = ("ok", "skipped")
HISTORY = 20  # runs kept per job for the control room's timeline
WORKER_FILE = "worker.json"  # the heartbeat, next to ``jobs.json``
PULSE_SECONDS = 30  # while a job runs, the heartbeat is renewed this often
_NEVER = datetime.min.replace(tzinfo=UTC)


class RunRequestSource(Protocol):
    """ "Run now" requests (``{job: requestedAt}``), see ``lifeos.modules.control.store``."""

    def all(self) -> dict[str, str]: ...
    def done(self, job: str) -> None: ...


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
        requests: RunRequestSource | None = None,
    ) -> None:
        self.jobs = jobs
        self.requests = requests
        self.state_file = state_file
        self.clock = clock
        self.state: dict[str, dict] = self._load()
        self._beat(None)  # a run a restart cut off is not running any more

    def tick(self) -> list[str]:
        """Run every job that is due now; returns the names of the jobs that ran."""
        ran = []
        requested = self.requests.all() if self.requests else {}
        for job in self.jobs:
            now = self.clock()
            entry = self.state.setdefault(job.name, {})
            asked = _parse(requested.get(job.name))
            on_request = asked is not None and asked > (_parse(entry.get("lastRun")) or _NEVER)
            if not on_request and not job.schedule.due(now, entry):
                continue
            result = self._run_alive(job, now, on_request)
            self._record(job, entry, now, result)
            # right away: a restart in the middle of a tick must not forget (and repeat) it
            self._save()
            if job.name in requested and self.requests:
                self.requests.done(job.name)
            ran.append(job.name)
        self._beat(None)
        return ran

    def _run_alive(self, job: Job, now: datetime, on_request: bool) -> JobResult:
        """Run the job while a side thread renews the heartbeat: the control room tells a
        long run (an AI agent working for half an hour) from a worker that is down."""
        self._beat(job.name, now)
        done = threading.Event()

        def pulse() -> None:
            while not done.wait(PULSE_SECONDS):
                self._beat(job.name, now)

        thread = threading.Thread(target=pulse, name="worker-pulse", daemon=True)
        thread.start()
        try:
            return self._run(job, on_request)
        finally:
            done.set()
            thread.join(timeout=5)

    def _beat(self, running: str | None, since: datetime | None = None) -> None:
        """``state/worker.json``: when the worker was last alive and what it runs now."""
        if not self.state_file:
            return
        beat: dict = {"at": self.clock().isoformat(timespec="seconds"), "running": None}
        if running and since:
            beat["running"] = {"job": running, "since": since.isoformat(timespec="seconds")}
        path = self.state_file.with_name(WORKER_FILE)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(beat), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:  # the heartbeat must never stop a job
            log.warning("worker heartbeat not written: %s", exc)

    def _run(self, job: Job, on_request: bool = False) -> JobResult:
        # tokens of a "run now" count as the owner's doing, scheduled runs as autonomous
        mode = tokens.SEMI if on_request else tokens.AUTO
        try:
            with tokens.context(job.name, mode, detail=""):
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
        history = entry.setdefault("history", [])
        history.append({"at": entry["lastRun"], "status": result.status,
                        "message": result.message[:200]})  # fmt: skip
        del history[:-HISTORY]

    def status(self) -> dict[str, dict]:
        return self._load()

    def _load(self) -> dict[str, dict]:
        if self.state_file and self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("ignoring unreadable %s", self.state_file)
            else:
                if isinstance(data, dict):
                    return {k: v for k, v in data.items() if isinstance(v, dict)}
                log.warning("ignoring %s: not a table of jobs", self.state_file)
        return {}

    def _save(self) -> None:
        if not self.state_file:
            return
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        tmp.replace(self.state_file)


def parse_time(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))
