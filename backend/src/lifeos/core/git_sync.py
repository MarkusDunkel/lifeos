"""Keep the vault in sync with its private git remote.

One run: commit local changes, ``git pull --rebase``, push. The vault design keeps
conflicts rare (every file has a single writer: the user or the app). If a conflict
happens anyway, the rebase is aborted, nothing is overwritten and the status reports
``conflict`` until a human resolves it.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lifeos.core.config import Settings

try:  # POSIX only; on Windows (development) runs are serialised per process
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

GIT_TIMEOUT = 120
MAX_LISTED_FILES = 20


class GitError(RuntimeError):
    pass


@dataclass
class SyncResult:
    status: str  # ok | skipped | conflict | error
    message: str = ""
    committed: list[str] = field(default_factory=list)
    pulled: int = 0
    pushed: int = 0
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))


class GitSync:
    def __init__(
        self,
        root: Path,
        status_file: Path | None = None,
        remote: str = "origin",
    ) -> None:
        self.root = root
        self.status_file = status_file
        self.remote = remote
        self._lock = threading.Lock()

    # -- public ---------------------------------------------------------------

    def run(self) -> SyncResult:
        with self._lock, self._file_lock():
            try:
                result = self._run()
            except GitError as exc:
                result = SyncResult("error", str(exc))
            self._write_status(result)
            return result

    def status(self) -> dict:
        if self.status_file and self.status_file.exists():
            try:
                return json.loads(self.status_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return {"status": "never", "message": "no sync has run yet"}

    # -- steps ------------------------------------------------------------------

    def _run(self) -> SyncResult:
        if not (self.root / ".git").exists():
            return SyncResult("skipped", "vault is not a git repository")
        if self._remote_url() is None:
            return SyncResult("skipped", f"no remote '{self.remote}' configured")
        if self._operation_in_progress():
            return SyncResult(
                "conflict", "a merge or rebase is in progress in the vault; resolve it by hand"
            )

        branch = self._git("rev-parse", "--abbrev-ref", "HEAD").strip()
        if branch == "HEAD":
            return SyncResult("error", "vault is in detached HEAD state")

        committed = self._commit_all()

        self._git("fetch", "--quiet", self.remote, branch)
        upstream = f"{self.remote}/{branch}"
        behind = int(self._git("rev-list", "--count", f"HEAD..{upstream}").strip() or 0)
        if behind:
            proc = self._git_raw("rebase", "--quiet", upstream)
            if proc.returncode != 0:
                self._git_raw("rebase", "--abort")
                return SyncResult(
                    "conflict",
                    "remote changes conflict with local changes; rebase aborted, nothing pushed",
                    committed=committed,
                )

        ahead = int(self._git("rev-list", "--count", f"{upstream}..HEAD").strip() or 0)
        if ahead:
            self._git("push", "--quiet", self.remote, f"HEAD:refs/heads/{branch}")

        parts = []
        if committed:
            parts.append(f"committed {len(committed)} file(s)")
        if behind:
            parts.append(f"pulled {behind} commit(s)")
        if ahead:
            parts.append(f"pushed {ahead} commit(s)")
        return SyncResult(
            "ok",
            ", ".join(parts) or "up to date",
            committed=committed,
            pulled=behind,
            pushed=ahead,
        )

    def _commit_all(self) -> list[str]:
        self._git("add", "--all")
        changed = [
            line for line in self._git("diff", "--cached", "--name-only").splitlines() if line
        ]
        if not changed:
            return []
        listed = "\n".join(f"- {name}" for name in changed[:MAX_LISTED_FILES])
        if len(changed) > MAX_LISTED_FILES:
            listed += f"\n- … and {len(changed) - MAX_LISTED_FILES} more"
        message = f"lifeos: auto-sync ({len(changed)} file(s))\n\n{listed}\n"
        self._git(*self._identity(), "commit", "--quiet", "--no-verify", "-m", message)
        return changed

    # -- helpers --------------------------------------------------------------

    def _identity(self) -> list[str]:
        """Fallback author when the vault has no git identity configured."""
        has_name = self._git_raw("config", "user.name").stdout.strip()
        has_mail = self._git_raw("config", "user.email").stdout.strip()
        args: list[str] = []
        if not has_name:
            args += ["-c", "user.name=LifeOS"]
        if not has_mail:
            args += ["-c", "user.email=lifeos@localhost"]
        return args

    def _remote_url(self) -> str | None:
        proc = self._git_raw("remote", "get-url", self.remote)
        return proc.stdout.strip() if proc.returncode == 0 else None

    def _operation_in_progress(self) -> bool:
        git_dir = self.root / ".git"
        return any(
            (git_dir / name).exists() for name in ("rebase-merge", "rebase-apply", "MERGE_HEAD")
        )

    def _git_raw(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ | {"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
        try:
            return subprocess.run(
                ["git", *args],
                cwd=self.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=GIT_TIMEOUT,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git {args[0]} timed out") from exc

    def _git(self, *args: str) -> str:
        proc = self._git_raw(*args)
        if proc.returncode != 0:
            cmd = next(a for a in args if not a.startswith("-") and "=" not in a)
            lines = [ln.strip() for ln in (proc.stderr or proc.stdout).splitlines() if ln.strip()]
            raise GitError(f"git {cmd} failed: {lines[0] if lines else proc.returncode}")
        return proc.stdout

    def _write_status(self, result: SyncResult) -> None:
        if not self.status_file:
            return
        data = asdict(result)
        data["committed"] = data["committed"][:MAX_LISTED_FILES]
        if result.status in ("ok", "skipped"):
            data["lastSuccess"] = result.at
        else:
            data["lastSuccess"] = self.status().get("lastSuccess")
        self.status_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.status_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.status_file)

    def _file_lock(self):
        """Serialise runs across processes (worker and web share the state dir)."""
        if fcntl is None or not self.status_file:
            return contextlib.nullcontext()
        return _FcntlLock(self.status_file.with_suffix(".lock"))


def git_sync_for(settings: Settings) -> GitSync:
    """The vault sync used by the worker and the API; status lives in the state dir."""
    status_file = settings.state_dir / "git-sync.json" if settings.state_dir else None
    return GitSync(settings.vault_root, status_file)


class _FcntlLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def __enter__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self._fd, fcntl.LOCK_EX)  # type: ignore[union-attr]

    def __exit__(self, *exc: object) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)  # type: ignore[union-attr]
            os.close(self._fd)
            self._fd = None
