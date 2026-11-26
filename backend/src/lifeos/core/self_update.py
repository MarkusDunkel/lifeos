"""PC side: bring the PC's copy of the app up to the deploy branch before a scheduled task.

The scheduled tasks on the PC (Teams files, job import, Claude Code usage) run from the PC's
own checkout of the app, which the server's deployments do not touch. Before each run the
task fetches the deploy branch (``LIFEOS_DEPLOY_BRANCH`` in its env file) and fast-forwards
– but only when the checkout is on that branch and has no uncommitted changes, so work in
progress is never overwritten. Otherwise the task runs on the code it has and says why in
its report. When ``pyproject.toml`` or ``uv.lock`` changed, ``uv sync`` (with the ``pc``
extra) runs first; if it fails, the checkout goes back to the previous commit.

After an update the task starts itself again (``LIFEOS_APP_UPDATE`` carries the result to
the new process), so the run already uses the new code.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

ENV_RESULT = "LIFEOS_APP_UPDATE"  # set for the restarted process: the update already ran
DEPENDENCY_FILES = ("backend/pyproject.toml", "backend/uv.lock")
# the PC extra (notifications, browser) belongs on the PC; --inexact never removes packages
UV_SYNC = ("sync", "--frozen", "--extra", "pc", "--inexact")
LOCK_FILE = "lifeos-update.lock"
LOCK_WAIT = 90  # seconds to wait for another task that is updating
LOCK_STALE = 15 * 60

# statuses: "updated" and "current" are fine; "off" means switched off in the env file;
# "skipped" (not on the branch, local changes or commits) and "failed" (fetch, uv sync)
# leave the code as it was and show up in the control room.
OK_STATUSES = ("updated", "current", "off")

Runner = Callable[[list[str], Path], tuple[int, str]]


@dataclass
class UpdateResult:
    status: str
    message: str
    version: str | None = None  # commit the task runs on (short)

    @property
    def ok(self) -> bool:
        return self.status in OK_STATUSES

    @property
    def changed(self) -> bool:
        return self.status == "updated"

    def to_env(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_env(cls, value: str) -> UpdateResult | None:
        try:
            data = json.loads(value)
            return cls(str(data["status"]), str(data.get("message", "")), data.get("version"))
        except (ValueError, KeyError, TypeError):
            return None


def app_root() -> Path:
    """The checkout this code runs from (``app/``; this file is backend/src/lifeos/core)."""
    return Path(__file__).resolve().parents[4]


def _run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _last_line(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""


def current_version(root: Path, run: Runner = _run) -> str | None:
    code, out = run(["git", "rev-parse", "--short", "HEAD"], root)
    return out.strip() if code == 0 and out.strip() else None


class _Lock:
    """Two tasks may start at the same time (e.g. at logon): one updates, the other waits."""

    def __init__(self, git_dir: Path) -> None:
        self.path = git_dir / LOCK_FILE
        self.held = False

    def __enter__(self) -> _Lock:
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                self.held = True
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > LOCK_STALE:
                        self.path.unlink(missing_ok=True)
                        continue
                except OSError:
                    continue
                if time.monotonic() > deadline:
                    return self  # go on without it; git's own locks still protect the repo
                time.sleep(2)
            except OSError:
                return self

    def __exit__(self, *exc: object) -> None:
        if self.held:
            self.path.unlink(missing_ok=True)


def update_app(
    root: Path,
    branch: str,
    run: Runner = _run,
    uv: str | None = None,
) -> UpdateResult:
    """Fast-forward ``root`` to ``origin/<branch>`` when it is safe; never raises."""
    code, git_dir = run(["git", "rev-parse", "--absolute-git-dir"], root)
    if code != 0:
        return UpdateResult("skipped", "the app folder is not a git checkout")
    with _Lock(Path(git_dir.strip())):
        return _update(root, branch, run, uv)


def _update(root: Path, branch: str, run: Runner, uv: str | None) -> UpdateResult:
    old = current_version(root, run)

    def skipped(message: str) -> UpdateResult:
        return UpdateResult("skipped", message, old)

    code, head = run(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], root)
    head = head.strip() if code == 0 else "a detached commit"
    if head != branch:
        return skipped(f"not updated: the app is on {head}, not on {branch}")
    code, dirty = run(["git", "status", "--porcelain", "--untracked-files=no"], root)
    if code != 0:
        return UpdateResult("failed", f"git status failed: {_last_line(dirty)}", old)
    if dirty.strip():
        return skipped("not updated: the app has uncommitted changes")
    code, out = run(["git", "fetch", "--quiet", "origin", branch], root)
    if code != 0:
        return UpdateResult("failed", f"git fetch failed: {_last_line(out)}", old)
    code, _ = run(["git", "merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD"], root)
    if code != 0:
        code, _ = run(["git", "merge-base", "--is-ancestor", "FETCH_HEAD", "HEAD"], root)
        if code == 0:
            return UpdateResult("current", f"ahead of origin/{branch}", old)
        return skipped(f"not updated: the app has local commits that are not on {branch}")
    code, new = run(["git", "rev-parse", "--short", "FETCH_HEAD"], root)
    new = new.strip()
    if code != 0 or not new or new == old:
        return UpdateResult("current", f"up to date with {branch}", old)
    code, changed = run(
        ["git", "diff", "--name-only", "HEAD", "FETCH_HEAD", "--", *DEPENDENCY_FILES], root
    )
    needs_sync = code != 0 or bool(changed.strip())
    code, out = run(["git", "merge", "--ff-only", "--quiet", "FETCH_HEAD"], root)
    if code != 0:
        return UpdateResult("failed", f"fast-forward failed: {_last_line(out)}", old)
    if needs_sync:
        uv = uv or shutil.which("uv")
        code, out = run([uv, *UV_SYNC], root / "backend") if uv else (1, "uv not found")
        if code != 0:
            run(["git", "reset", "--quiet", "--keep", old or "ORIG_HEAD"], root)
            return UpdateResult(
                "failed",
                f"stayed at {old}: uv sync for {new} failed: {_last_line(out)}",
                old,
            )
    deps = ", dependencies synced" if needs_sync else ""
    return UpdateResult("updated", f"updated {old} → {new}{deps}", new)
