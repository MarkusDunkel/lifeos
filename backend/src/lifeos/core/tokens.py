"""The token ledger: every model call LifeOS makes, one JSON line in ``state/tokens.jsonl``.

Who burned the tokens comes from the context the call runs in (:func:`context`): the worker
sets the job for each run, the web app marks everything it starts as triggered by the owner.
The mode says how much of the owner it took:

- ``auto``: runs on the server on its own, also at night
- ``semi``: needs the owner, because the PC must be on or a click of theirs started it
- ``interactive``: the owner works with Claude Code on the PC (reported from there, see
  :mod:`lifeos.modules.control.claude_usage`)

Recording never fails a call: without a state folder (tests, the PC) nothing is written.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("lifeos.tokens")

AUTO, SEMI, INTERACTIVE = "auto", "semi", "interactive"
MODES = (AUTO, SEMI, INTERACTIVE)
LEDGER_FILE = "tokens.jsonl"


@dataclass(frozen=True)
class Run:
    process: str = "other"
    mode: str = AUTO
    detail: str = ""  # e.g. the agent that made the call


_run: ContextVar[Run | None] = ContextVar("lifeos_token_run", default=None)
_config: dict[str, object] = {"path": None, "default": Run()}
_lock = threading.Lock()


def configure(state_dir: Path | None, process: str = "other", mode: str = AUTO) -> None:
    """Where the ledger lives and whom calls outside any :func:`context` are booked to."""
    _config["path"] = state_dir / LEDGER_FILE if state_dir else None
    _config["default"] = Run(process, mode)


def ledger_path() -> Path | None:
    return _config["path"]  # type: ignore[return-value]


def current() -> Run:
    return _run.get() or _config["default"]  # type: ignore[return-value]


@contextmanager
def context(
    process: str | None = None, mode: str | None = None, detail: str | None = None
) -> Iterator[Run]:
    """Book the model calls inside to ``process``; unset fields keep the outer value."""
    outer = current()
    run = replace(
        outer,
        process=process or outer.process,
        mode=mode or outer.mode,
        detail=outer.detail if detail is None else detail,
    )
    token = _run.set(run)
    try:
        yield run
    finally:
        _run.reset(token)


def record(tokens: int, model: str, billing: str, usd: float = 0.0) -> None:
    """One model call; ``usd`` is what was billed (0 on the subscription)."""
    path = ledger_path()
    if path is None or tokens <= 0:
        return
    run = current()
    line = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "process": run.process,
        "mode": run.mode,
        "tokens": int(tokens),
        "model": model,
        "billing": billing,
    }
    if run.detail:
        line["detail"] = run.detail
    if usd:
        line["usd"] = round(usd, 6)
    try:
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            # one short line per append: web app and worker may write at the same time
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("token ledger not written: %s", exc)


def read(path: Path | None = None) -> list[dict]:
    path = path or ledger_path()
    if path is None or not path.is_file():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue  # a half-written last line
    return out
