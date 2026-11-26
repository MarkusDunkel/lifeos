"""State files of the control room (all in the state folder, shared by web and worker):

- ``pc-jobs.json``: what the PC tasks reported, in the shape of ``jobs.json``
- ``job-requests.json``: "run now" requests for worker jobs, picked up by the worker
- ``control-report.json``: the latest AI diagnosis
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lifeos.core.locks import FileLock
from lifeos.core.vault import write_atomic

HISTORY = 20
PC_JOBS_FILE = "pc-jobs.json"
REQUESTS_FILE = "job-requests.json"
REPORT_FILE = "control-report.json"

WORKER_FILE = "worker.json"  # the worker's heartbeat, see lifeos.worker.scheduler

_lock = threading.Lock()


@contextmanager
def _exclusive(path: Path) -> Iterator[None]:
    """Read-change-write of a state file: the web app and the worker are two processes."""
    with _lock, FileLock(path.with_suffix(".lock")):
        yield


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def read_json(path: Path | None, default: Any = None) -> Any:
    if path is None or not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_table(path: Path | None, kind: type) -> dict[str, Any]:
    """A ``{key: value}`` state file; anything that is not a ``kind`` value is left out."""
    data = read_json(path, {})
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, kind)}


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False))


def add_history(entry: dict, at: str, status: str, message: str = "") -> None:
    """Keep the last runs (newest last) for the mini timeline."""
    history = entry.setdefault("history", [])
    history.append({"at": at, "status": status, "message": message[:200]})
    del history[:-HISTORY]


class PcJobs:
    """Reports of the PC tasks. ``ok`` counts as success; ``skipped`` (e.g. the user said
    "later") keeps the run visible without counting as success."""

    def __init__(self, state_dir: Path | None) -> None:
        self.path = state_dir / PC_JOBS_FILE if state_dir else None

    def all(self) -> dict[str, dict]:
        return read_table(self.path, dict)

    def record(
        self,
        process: str,
        status: str,
        message: str,
        duration: float | None,
        app: dict | None = None,
    ) -> dict:
        """``app``: ``{version, update, ok}`` of the PC's copy of the app (newer PCs)."""
        if self.path is None:
            raise RuntimeError("LIFEOS_STATE_DIR is not set")
        with _exclusive(self.path):
            data = self.all()
            entry = data.setdefault(process, {})
            at = now_iso()
            entry.update(lastRun=at, status=status, message=message)
            if duration is not None:
                entry["duration"] = round(duration, 1)
            if app is not None:
                entry["app"] = app
            else:
                entry.pop("app", None)  # an older PC copy: no stale commit from before
            if status == "ok":
                entry["lastSuccess"] = at
            add_history(entry, at, status, message)
            write_json(self.path, data)
            return entry


class RunRequests:
    """``{job: requestedAt}``; the worker runs a job whose request is newer than its last run."""

    def __init__(self, state_dir: Path | None) -> None:
        self.path = state_dir / REQUESTS_FILE if state_dir else None

    def all(self) -> dict[str, str]:
        return read_table(self.path, str)

    def request(self, job: str) -> str:
        if self.path is None:
            raise RuntimeError("LIFEOS_STATE_DIR is not set")
        with _exclusive(self.path):
            data = self.all()
            data[job] = now_iso()
            write_json(self.path, data)
            return data[job]

    def done(self, job: str) -> None:
        if self.path is None:
            return
        with _exclusive(self.path):
            data = self.all()
            if data.pop(job, None) is not None:
                write_json(self.path, data)
