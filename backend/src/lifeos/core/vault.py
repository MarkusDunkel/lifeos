"""Safe access to the vault directory.

Every path that comes from a request is resolved through :class:`Vault`. It
rejects absolute paths, ``..`` segments, drive letters and symlinks, and can
confine a path to a *zone* (a sub-directory a module is allowed to touch).
"""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# Directories inside the vault, relative to its root.
READY = "ready"
KNOWLEDGE = "ready/knowledge"
NOTES = "ready/notes"
WORKSPACES = "ready/workspaces"
INBOX = "inbox"
FEEDBACK = ".md4h/feedback"
STATE = ".lifeos"


class VaultPathError(ValueError):
    """A requested path is invalid or escapes its allowed zone."""


class RevisionConflict(Exception):
    """The file changed on disk since the client loaded it."""

    def __init__(self, current: str | None):
        super().__init__("file changed on disk")
        self.current = current


@dataclass(frozen=True)
class Vault:
    root: Path

    @classmethod
    def open(cls, root: Path) -> Vault:
        resolved = root.resolve(strict=True)
        if not resolved.is_dir():
            raise VaultPathError(f"vault root is not a directory: {root}")
        return cls(resolved)

    def zone(self, zone: str) -> Path:
        return self.resolve(zone)

    def resolve(self, rel: str, *, zone: str | None = None) -> Path:
        """Resolve ``rel`` (POSIX-style, relative) inside ``zone`` or the vault root."""
        base = self.root if zone is None else self.root.joinpath(*PurePosixPath(zone).parts)
        parts = _split_relative(rel)
        candidate = base.joinpath(*parts)

        # Walk every component below the root and refuse symlinks/junctions.
        current = self.root
        for part in candidate.relative_to(self.root).parts:
            current = current / part
            if current.is_symlink() or _is_junction(current):
                raise VaultPathError(f"symlinks are not allowed: {rel}")

        if not candidate.resolve().is_relative_to(base.resolve()):
            raise VaultPathError(f"path escapes its zone: {rel}")
        return candidate

    def relative(self, path: Path, *, zone: str | None = None) -> str:
        base = self.root if zone is None else self.resolve(zone)
        return path.relative_to(base).as_posix()


def _split_relative(rel: str) -> tuple[str, ...]:
    rel = rel.replace("\\", "/").strip()
    if rel in ("", "."):
        return ()
    if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        raise VaultPathError(f"absolute paths are not allowed: {rel}")
    parts = PurePosixPath(rel).parts
    for part in parts:
        if part in ("..", ".") or ":" in part or "\x00" in part:
            raise VaultPathError(f"invalid path segment in: {rel}")
    return parts


def _is_junction(path: Path) -> bool:
    is_junction = getattr(os.path, "isjunction", None)
    return bool(is_junction and is_junction(path))


def revision_of(path: Path) -> str | None:
    """Opaque revision token for optimistic locking (content hash)."""
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def write_atomic(path: Path, data: bytes | str) -> None:
    """Write via a temp file in the same directory, then atomically replace."""
    payload = data.encode("utf-8") if isinstance(data, str) else data
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with open(tmp, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_checked(path: Path, data: str, expected_revision: str | None) -> str:
    """Write ``data`` if the file still has ``expected_revision``.

    ``expected_revision=None`` means "create": it fails if the file exists.
    Returns the new revision.
    """
    with _write_lock:
        current = revision_of(path)
        if current != expected_revision:
            raise RevisionConflict(current)
        write_atomic(path, data)
        new = revision_of(path)
    assert new is not None
    return new


_write_lock = threading.Lock()
