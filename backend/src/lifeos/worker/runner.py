"""Background worker: ``lifeos worker``.

Runs scheduled jobs next to the web server, from the same image:

- ``moodle-sync`` daily at ``LIFEOS_MOODLE_SYNC_AT``, committed as ``moodle: …``
- ``git-sync`` every ``LIFEOS_GIT_SYNC_INTERVAL`` seconds

Job state lives in ``state/jobs.json`` (see :mod:`lifeos.worker.scheduler`).
"""

from __future__ import annotations

import logging
import signal
import threading
from pathlib import Path

from lifeos.core.config import Settings, get_settings
from lifeos.core.git_sync import git_sync_for
from lifeos.worker.jobs import build_jobs
from lifeos.worker.scheduler import Scheduler

log = logging.getLogger("lifeos.worker")

TICK_SECONDS = 30


def jobs_state_file(settings: Settings) -> Path | None:
    return settings.state_dir / "jobs.json" if settings.state_dir else None


def run_worker(
    settings: Settings | None = None,
    stop: threading.Event | None = None,
    tick: float = TICK_SECONDS,
) -> None:
    settings = settings or get_settings()
    stop = stop or threading.Event()

    def _stop(signum, _frame) -> None:
        log.info("received signal %s, stopping", signum)
        stop.set()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

    jobs = build_jobs(settings, git_sync_for(settings))
    if not jobs:
        log.info("no jobs configured (LIFEOS_MOODLE_SYNC_AT, LIFEOS_GIT_SYNC_INTERVAL); idle")
        stop.wait()
        return

    for job in jobs:
        log.info("job %s: %s", job.name, job.schedule)
    scheduler = Scheduler(jobs, jobs_state_file(settings))
    while not stop.is_set():
        scheduler.tick()
        stop.wait(tick)
