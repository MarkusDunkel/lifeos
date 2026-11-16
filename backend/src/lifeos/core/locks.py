"""Advisory file locks shared between the web and worker processes (POSIX).

On Windows (development only) the lock is a no-op; runs are still serialised per
process by the callers' own thread locks.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


class LockBusy(RuntimeError):
    pass


class FileLock:
    def __init__(self, path: Path, blocking: bool = True) -> None:
        self.path = path
        self.blocking = blocking
        self._fd: int | None = None

    def __enter__(self) -> FileLock:
        if fcntl is None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        flags = fcntl.LOCK_EX if self.blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(fd, flags)
        except BlockingIOError as exc:
            os.close(fd)
            raise LockBusy(str(self.path)) from exc
        self._fd = fd
        return self

    def __exit__(self, *exc: object) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)  # type: ignore[union-attr]
            os.close(self._fd)
            self._fd = None
