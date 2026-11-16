"""Keep the vault in sync with its private git remote.

One run: commit local changes, ``git pull --rebase``, push. The vault design keeps
conflicts rare (every file has a single writer: the user or the app). If a conflict
happens anyway, the rebase is aborted, nothing is overwritten and the status reports
``conflict`` until a human resolves it.

Writers that know what they changed (connector jobs, agents) commit their own paths with
a descriptive message via :meth:`GitSync.commit_paths` before the run; the run then only
collects what is left under ``lifeos: auto-sync``.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from lifeos.core.locks import FileLock

if TYPE_CHECKING:
    from lifeos.core.config import Settings

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


def file_list(paths: list[str]) -> str:
    """Bullet list for commit bodies, capped at ``MAX_LISTED_FILES`` entries."""
    listed = "\n".join(f"- {p}" for p in paths[:MAX_LISTED_FILES])
    if len(paths) > MAX_LISTED_FILES:
        listed += f"\n- … and {len(paths) - MAX_LISTED_FILES} more"
    return listed


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
        with self._locked():
            try:
                result = self._run()
            except GitError as exc:
                result = SyncResult("error", str(exc))
            self._write_status(result)
            return result

    def commit_paths(
        self, paths: list[str], message: str | Callable[[list[str]], str]
    ) -> list[str]:
        """Commit only the changes below ``paths`` (relative to the vault) with ``message``.

        ``message`` may be a function of the changed files. Other pending changes stay
        uncommitted for the next run. Returns the committed files; nothing is committed
        when there are no changes or no git repository.
        """
        if not (self.root / ".git").exists():
            return []
        with self._locked():
            if self._operation_in_progress():
                raise GitError("a merge or rebase is in progress in the vault")
            # A path that neither exists nor is tracked would make `git add` fail.
            paths = [p for p in paths if (self.root / p).exists() or self._git("ls-files", "--", p)]
            if not paths:
                return []
            self._git("add", "--all", "--", *paths)
            changed = self._staged(*paths)
            if changed:
                text = message(changed) if callable(message) else message
                self._git(
                    *self._identity(), "commit", "--quiet", "--no-verify", "-m", text,
                    "--", *paths,
                )  # fmt: skip
            return changed

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
        changed = self._staged()
        if changed:
            message = f"lifeos: auto-sync ({len(changed)} file(s))\n\n{file_list(changed)}\n"
            self._git(*self._identity(), "commit", "--quiet", "--no-verify", "-m", message)
        return changed

    # -- helpers --------------------------------------------------------------

    def _staged(self, *paths: str) -> list[str]:
        args = ["diff", "--cached", "--name-only"] + (["--", *paths] if paths else [])
        return [line for line in self._git(*args).splitlines() if line]

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

    @contextlib.contextmanager
    def _locked(self):
        """Serialise git operations across threads and processes (web and worker)."""
        with self._lock:
            if self.status_file:
                with FileLock(self.status_file.with_suffix(".lock")):
                    yield
            else:
                yield


def git_sync_for(settings: Settings) -> GitSync:
    """The vault sync used by the worker and the API; status lives in the state dir."""
    status_file = settings.state_dir / "git-sync.json" if settings.state_dir else None
    return GitSync(settings.vault_root, status_file)
