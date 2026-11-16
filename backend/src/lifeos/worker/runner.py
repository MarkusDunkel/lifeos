"""Background worker: ``lifeos worker``.

Runs periodic jobs next to the web server, from the same image. The first job is the
vault git sync; connector syncs and agents will follow.
"""

from __future__ import annotations

import logging
import signal
import threading

from lifeos.core.config import Settings, get_settings
from lifeos.core.git_sync import git_sync_for

log = logging.getLogger("lifeos.worker")


def run_worker(settings: Settings | None = None, stop: threading.Event | None = None) -> None:
    settings = settings or get_settings()
    stop = stop or threading.Event()
    interval = settings.git_sync_interval

    def _stop(signum, _frame) -> None:
        log.info("received signal %s, stopping", signum)
        stop.set()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

    if interval <= 0:
        log.info("git sync disabled (LIFEOS_GIT_SYNC_INTERVAL=0); worker is idle")
        stop.wait()
        return

    sync = git_sync_for(settings)
    log.info("git sync every %ss for %s", interval, settings.vault_root)
    while not stop.is_set():
        result = sync.run()
        level = logging.INFO if result.status in ("ok", "skipped") else logging.WARNING
        log.log(level, "git sync %s: %s", result.status, result.message)
        stop.wait(interval)
