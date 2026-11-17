"""``lifeos jobsearch autorun``: the daily import, started by a Windows scheduled task.

The task fires at logon, on wake from sleep and every hour. Each time this module
decides quietly whether there is anything to do:

1. another autorun is still running → stop
2. the user said "not today" or "in 1 hour" → stop until then
3. the server is unreachable → stop (the next trigger tries again)
4. the server already has a complete import from today → stop
5. otherwise ask with a notification: Import now / In 1 hour / Not today

The import itself is :func:`lifeos.modules.jobsearch.importer.run_import`; the result is
shown as a notification. If devjobs.at refuses the importer, it stops for the day and
says so instead of retrying.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta
from enum import StrEnum
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Protocol

from lifeos.connectors.devjobs.client import DevjobsClient, DevjobsError
from lifeos.modules.jobsearch.importer import (
    ImporterSettings,
    ImportServer,
    ServerError,
    run_import,
)

log = logging.getLogger("lifeos.jobsearch.autorun")

APP_NAME = "LifeOS"
SNOOZE = timedelta(hours=1)


def app_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "state")
    return Path(base) / "LifeOS" / "jobsearch"


class Choice(StrEnum):
    NOW = "now"
    LATER = "later"
    SKIP = "skip"


class Notifier(Protocol):
    def ask(self, title: str, text: str) -> Choice | None: ...
    def info(self, title: str, text: str) -> None: ...


# -- notifications --------------------------------------------------------------------


class ToastNotifier:
    """Windows notifications with buttons (needs the optional ``win11toast`` package)."""

    def __init__(self) -> None:
        import win11toast  # noqa: F401  (fail early if missing)

    def ask(self, title: str, text: str) -> Choice | None:
        from win11toast import toast

        buttons = [
            {"activationType": "foreground", "arguments": c.value, "content": label}
            for c, label in (
                (Choice.NOW, "Import now"),
                (Choice.LATER, "In 1 hour"),
                (Choice.SKIP, "Not today"),
            )
        ]
        result = toast(title, text, buttons=buttons, scenario="reminder", on_click=lambda r: r)
        if isinstance(result, dict):
            argument = str(result.get("arguments", "")).rsplit(":", 1)[-1]
            with contextlib.suppress(ValueError):
                return Choice(argument)
        return None  # dismissed, timed out or failed

    def info(self, title: str, text: str) -> None:
        from win11toast import notify

        notify(title, text)


class MessageBoxNotifier:
    """Fallback without extra packages: a plain Windows message box."""

    def ask(self, title: str, text: str) -> Choice | None:
        answer = _message_box(
            title,
            f"{text}\n\nYes = import now\nNo = not today\nCancel = ask again in 1 hour",
            0x3 | 0x20 | 0x40000,  # yes/no/cancel, question icon, topmost
        )
        return {6: Choice.NOW, 7: Choice.SKIP, 2: Choice.LATER}.get(answer)

    def info(self, title: str, text: str) -> None:
        _message_box(title, text, 0x40 | 0x40000)  # information icon, topmost


def _message_box(title: str, text: str, flags: int) -> int:
    import ctypes

    return ctypes.windll.user32.MessageBoxW(None, text, title, flags)  # type: ignore[attr-defined]


def default_notifier() -> Notifier:
    try:
        return ToastNotifier()
    except Exception:  # package missing or no notification support
        return MessageBoxNotifier()


# -- local state ----------------------------------------------------------------------


class LocalState:
    """``snoozeUntil`` and ``skippedOn`` on this PC (the server does not need them)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save(self, **changes: Any) -> None:
        data = self.load() | changes
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(self.path)


@contextlib.contextmanager
def single_instance(path: Path) -> Iterator[bool]:
    """Yields True if this process holds the lock, False if another autorun does."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")  # noqa: SIM115 (kept open while locked)
    try:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        handle.close()  # releases the lock


# -- the decision -----------------------------------------------------------------------


def autorun(
    settings: ImporterSettings,
    notifier: Notifier,
    base: Path,
    now: Callable[[], datetime] = datetime.now,
    make_server: Callable[[str, str], ImportServer] = ImportServer,
    make_devjobs: Callable[[], DevjobsClient] = DevjobsClient,
) -> str:
    """Run one autorun round; returns what happened (for the log and tests)."""
    if settings.missing():
        log.warning("not set up: %s", ", ".join(settings.missing()))
        return "not-configured"
    with single_instance(base / "autorun.lock") as mine:
        if not mine:
            return "already-running"
        state = LocalState(base / "state.json")
        current = state.load()
        today = now().date()
        if current.get("skippedOn") == today.isoformat():
            return "skipped-today"
        snooze = current.get("snoozeUntil")
        if snooze and datetime.fromisoformat(snooze) > now():
            return "snoozed"

        server = make_server(settings.jobsearch_server_url, settings.jobsearch_import_token)
        try:
            try:
                if server.status().get("doneToday"):
                    return "done-today"
            except ServerError as exc:
                log.info("server not reachable: %s", exc)
                return "offline"

            choice = notifier.ask("Job search", "New job postings have not been imported today.")
            if choice in (None, Choice.LATER):
                state.save(snoozeUntil=(now() + SNOOZE).isoformat(timespec="seconds"))
                return "later"
            if choice == Choice.SKIP:
                state.save(skippedOn=today.isoformat())
                return "skipped"
            return _import(server, make_devjobs, notifier, state, today, now)
        finally:
            server.close()


def _import(
    server: ImportServer,
    make_devjobs: Callable[[], DevjobsClient],
    notifier: Notifier,
    state: LocalState,
    today: date,
    now: Callable[[], datetime],
) -> str:
    devjobs = make_devjobs()
    try:
        result = run_import(server, devjobs)
    except DevjobsError as exc:
        if exc.status in (403, 409):  # refused or no longer allowed: do not insist today
            state.save(skippedOn=today.isoformat())
            notifier.info("Job search stopped", f"devjobs.at refused the import: {exc}")
            log.warning("devjobs.at refused the import: %s", exc)
            return "blocked"
        return _failed(notifier, state, now, exc)
    except ServerError as exc:
        return _failed(notifier, state, now, exc)
    finally:
        devjobs.close()
    log.info("import done: %s", result.summary())
    notifier.info("Job search", result.summary().capitalize() + ".")
    return "imported"


def _failed(
    notifier: Notifier, state: LocalState, now: Callable[[], datetime], exc: Exception
) -> str:
    state.save(snoozeUntil=(now() + SNOOZE).isoformat(timespec="seconds"))
    notifier.info("Job search interrupted", f"{exc}\nIt continues in about an hour.")
    log.warning("import failed: %s", exc)
    return "failed"


def setup_logging(base: Path) -> None:
    """``pythonw`` has no console: log to a small rotating file next to the state."""
    base.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(base / "autorun.log", maxBytes=256_000, backupCount=2)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
