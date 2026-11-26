"""Burned tokens for the control room: the ledger of the server (:mod:`lifeos.core.tokens`)
plus the Claude Code sessions the PC reports, bucketed for a stacked bar chart.

The PC reports hourly totals per transcript file (``source``) and resends the hours it
re-read; a bucket replaces the earlier value, so reporting twice never counts twice.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from lifeos.core import tokens as ledger
from lifeos.core.tokens import AUTO, INTERACTIVE, MODES, SEMI
from lifeos.modules.control.catalog import Catalog
from lifeos.modules.control.store import now_iso, read_json, write_json

INTERACTIVE_FILE = "tokens-interactive.json"
# range -> (bucket length, number of buckets)
RANGES = {
    "24h": (timedelta(hours=1), 24),
    "7d": (timedelta(hours=6), 28),
    "30d": (timedelta(days=1), 30),
}
TOP = 12
OWN_LABELS = {
    "web": "Started in the app",
    "worker": "Worker (other)",
    "other": "Other",
}

_lock = threading.Lock()


class InteractiveUsage:
    """``{"<source>|<hour>": {at, tokens, model, project}}`` from the PC."""

    def __init__(self, state_dir: Path | None) -> None:
        self.path = state_dir / INTERACTIVE_FILE if state_dir else None

    def all(self) -> dict[str, dict]:
        return (read_json(self.path, {}) or {}).get("buckets", {})

    def upsert(self, buckets: list[dict]) -> dict:
        if self.path is None:
            raise RuntimeError("LIFEOS_STATE_DIR is not set")
        with _lock:
            data = read_json(self.path, {}) or {}
            stored = data.setdefault("buckets", {})
            for b in buckets:
                hour = _hour(b["hour"])
                stored[f"{b['source']}|{hour.isoformat()}"] = {
                    "at": hour.isoformat(),
                    "tokens": int(b["tokens"]),
                    "model": b.get("model", ""),
                    "project": b.get("project", ""),
                }
            data["reportedAt"] = now_iso()
            write_json(self.path, data)
            return {"buckets": len(buckets), "stored": len(stored)}


def _hour(value: str) -> datetime:
    at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def _start(now: datetime, step: timedelta, tz: ZoneInfo) -> datetime:
    """Start of the bucket ``now`` falls in, aligned to local hours and days."""
    local = now.astimezone(tz)
    if step >= timedelta(days=1):
        local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        hours = int(step.total_seconds() // 3600)
        local = local.replace(hour=local.hour - local.hour % hours, minute=0, second=0,
                              microsecond=0)  # fmt: skip
    return local.astimezone(UTC)


def _label(catalog: Catalog, process: str) -> str:
    if process in OWN_LABELS:
        return OWN_LABELS[process]
    match = catalog.process(process) or next(
        (p for p in catalog.processes if p.job == process), None
    )
    return match.label if match else process


def series(
    catalog: Catalog,
    state_dir: Path | None,
    tz: ZoneInfo,
    range_: str = "24h",
    now: datetime | None = None,
) -> dict:
    step, count = RANGES.get(range_, RANGES["24h"])
    now = now or datetime.now(UTC)
    first = _start(now, step, tz) - step * (count - 1)
    end = first + step * count
    buckets = [{"start": (first + step * i).isoformat(), **dict.fromkeys(MODES, 0)}
               for i in range(count)]  # fmt: skip
    top: dict[tuple[str, str], dict] = {}

    def add(at: datetime, mode: str, amount: int, key: str, label: str, detail: str) -> None:
        if not first <= at < end or mode not in MODES or amount <= 0:
            return
        index = int((at - first) / step)
        buckets[index][mode] += amount
        entry = top.setdefault(
            (key, mode), {"id": key, "label": label, "detail": detail, "mode": mode, "tokens": 0}
        )
        entry["tokens"] += amount

    path = state_dir / ledger.LEDGER_FILE if state_dir else None
    for line in ledger.read(path):
        try:
            at = datetime.fromisoformat(line["at"])
            amount = int(line.get("tokens") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        process, detail = str(line.get("process") or "other"), str(line.get("detail") or "")
        key = f"{process}/{detail}" if detail else process
        add(at, str(line.get("mode") or AUTO), amount, key, _label(catalog, process), detail)

    for entry in InteractiveUsage(state_dir).all().values():
        try:
            at = datetime.fromisoformat(entry["at"])
        except (KeyError, ValueError):
            continue
        project = entry.get("project") or "?"
        add(at, INTERACTIVE, int(entry.get("tokens") or 0), f"claude-code/{project}",
            "Claude Code", project)  # fmt: skip

    totals = {m: sum(b[m] for b in buckets) for m in MODES}
    reported = (
        (read_json(state_dir / INTERACTIVE_FILE, {}) or {}).get("reportedAt") if state_dir else None
    )
    return {
        "range": range_ if range_ in RANGES else "24h",
        "bucketSeconds": int(step.total_seconds()),
        "buckets": buckets,
        "totals": totals,
        "total": sum(totals.values()),
        "top": sorted(top.values(), key=lambda e: -e["tokens"])[:TOP],
        "interactiveReportedAt": reported,
        "modes": {
            AUTO: "Fully automatic: runs on the server, also at night",
            SEMI: "Semi-automatic: needs you, the PC on or a click of yours",
            INTERACTIVE: "Interactive: you work with Claude Code",
        },
    }
