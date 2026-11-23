"""Planned blocks: storage, free time, validation and the write-back to Google.

The plan lives in ``.lifeos/planning/plan.json`` (machine state). Each block
also exists as an event in the LifeOS target calendar, marked with
``lifeos_kind=plan`` and ``lifeos_id=<block id>``. Google is where the owner
looks, so what they change there wins: a plan event moved by hand moves the
block, a deleted one marks the block ``removed``.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from lifeos.connectors.google.client import CalendarApi
from lifeos.connectors.google.sync import PLAN
from lifeos.core.locks import FileLock
from lifeos.core.vault import STATE, Vault, write_atomic

ROOT = f"{STATE}/planning"
PLAN_FILE = f"{ROOT}/plan.json"
LOCK_FILE = f"{ROOT}/plan.lock"
DAY_START = time(6, 0)
DAY_END = time(23, 0)
PLANNED = "planned"
DONE = "done"
PARTIAL = "partial"
SKIPPED = "skipped"
REMOVED = "removed"  # deleted in Google by the owner
STATUSES = (PLANNED, DONE, PARTIAL, SKIPPED)

# Google event colours (colorId) per category; unknown categories get a stable pick.
PALETTE = ["9", "10", "5", "6", "3", "7", "2", "11", "4", "1"]
KNOWN_COLORS = {"pause": "8"}


@dataclass
class Block:
    id: str
    date: str  # YYYY-MM-DD
    start: str  # HH:MM
    end: str  # HH:MM
    category: str
    goal: str
    title: str
    reason: str = ""
    status: str = PLANNED
    minutes_done: int | None = None  # for partial
    gid: str | None = None  # Google event id
    created: str = ""

    def span(self, tz: ZoneInfo) -> tuple[datetime, datetime]:
        d = date.fromisoformat(self.date)
        return (
            datetime.combine(d, time.fromisoformat(self.start), tz),
            datetime.combine(d, time.fromisoformat(self.end), tz),
        )

    @property
    def minutes(self) -> int:
        s, e = time.fromisoformat(self.start), time.fromisoformat(self.end)
        return (e.hour * 60 + e.minute) - (s.hour * 60 + s.minute)

    def done_minutes(self) -> int:
        """Minutes that count towards the goal (unconfirmed past blocks count as done)."""
        if self.status in (SKIPPED, REMOVED):
            return 0
        if self.status == PARTIAL:
            return self.minutes_done if self.minutes_done is not None else self.minutes // 2
        return self.minutes


def new_id() -> str:
    return "b" + secrets.token_hex(5)


def color_for(category: str) -> str:
    if category in KNOWN_COLORS:
        return KNOWN_COLORS[category]
    digest = int(hashlib.sha1(category.encode()).hexdigest(), 16)
    return PALETTE[digest % len(PALETTE)]


def event_body(block: Block, tz: ZoneInfo) -> dict[str, Any]:
    start, end = block.span(tz)
    lines = [block.reason] if block.reason else []
    if block.goal:
        lines.append(f"Goal: {block.goal}")
    lines.append("Planned by LifeOS – move or delete it here, the planner adapts.")
    return {
        "summary": block.title,
        "description": "\n".join(lines),
        "start": {"dateTime": start.isoformat(), "timeZone": str(tz)},
        "end": {"dateTime": end.isoformat(), "timeZone": str(tz)},
        "colorId": color_for(block.category),
        "extendedProperties": {"private": {"lifeos_kind": PLAN, "lifeos_id": block.id}},
    }


def _parse_dt(value: str, tz: ZoneInfo) -> datetime:
    if len(value) == 10:  # all-day
        return datetime.combine(date.fromisoformat(value), time(0), tz)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(tz)


@dataclass
class Busy:
    start: datetime
    end: datetime
    title: str
    source: str


def busy_times(store: dict[str, Any], tz: ZoneInfo) -> tuple[list[Busy], list[dict[str, Any]]]:
    """Timed busy events (inputs and hand-made target events) and all-day events."""
    busy: list[Busy] = []
    all_day: list[dict[str, Any]] = []
    for e in store.get("events", []) + store.get("manual", []):
        if e.get("all_day"):
            all_day.append({"title": e["title"], "start": e["start"], "end": e["end"]})
            continue
        if not e.get("busy", True):
            continue
        busy.append(
            Busy(_parse_dt(e["start"], tz), _parse_dt(e["end"], tz), e["title"], e["calendar"])
        )
    busy.sort(key=lambda b: b.start)
    return busy, all_day


def round_up(moment: datetime, minutes: int = 15) -> datetime:
    extra = (-moment.minute) % minutes
    rounded = moment.replace(second=0, microsecond=0) + timedelta(minutes=extra)
    return rounded if rounded >= moment else rounded + timedelta(minutes=minutes)


def free_windows(
    day: date, busy: list[Busy], tz: ZoneInfo, not_before: datetime | None = None
) -> list[tuple[datetime, datetime]]:
    start = datetime.combine(day, DAY_START, tz)
    end = datetime.combine(day, DAY_END, tz)
    if not_before is not None:
        start = max(start, not_before)
    windows: list[tuple[datetime, datetime]] = []
    cursor = start
    for b in busy:
        if b.end <= cursor or b.start >= end:
            continue
        if b.start > cursor:
            windows.append((cursor, min(b.start, end)))
        cursor = max(cursor, b.end)
    if cursor < end:
        windows.append((cursor, end))
    return [(s, e) for s, e in windows if (e - s) >= timedelta(minutes=15)]


def _overlaps(a: tuple[datetime, datetime], b: tuple[datetime, datetime]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def validate(
    blocks: list[Block],
    busy: list[Busy],
    frozen: list[Block],
    tz: ZoneInfo,
    freeze_until: datetime,
    horizon_end: date,
) -> tuple[list[Block], list[str]]:
    """Blocks that may be written, and a message per rejected block."""
    ok: list[Block] = []
    errors: list[str] = []
    taken = [b.span(tz) for b in frozen]
    for block in sorted(blocks, key=lambda b: (b.date, b.start)):
        label = f"{block.date} {block.start}–{block.end} {block.title!r}"
        try:
            span = block.span(tz)
        except ValueError:
            errors.append(f"{label}: invalid date or time")
            continue
        if span[1] <= span[0]:
            errors.append(f"{label}: ends before it starts")
        elif span[0] < freeze_until:
            errors.append(f"{label}: starts before {freeze_until:%Y-%m-%d %H:%M} (already past)")
        elif span[0].date() > horizon_end:
            errors.append(f"{label}: after the planning horizon")
        elif clash := next((b for b in busy if _overlaps(span, (b.start, b.end))), None):
            errors.append(f"{label}: overlaps the appointment {clash.title!r}")
        elif any(_overlaps(span, t) for t in taken):
            errors.append(f"{label}: overlaps another block")
        else:
            ok.append(block)
            taken.append(span)
    return ok, errors


@dataclass
class ApplyReport:
    created: int = 0
    moved: int = 0
    deleted: int = 0
    errors: list[str] = field(default_factory=list)


class PlanStore:
    def __init__(self, vault: Vault, timezone: str) -> None:
        self.vault = vault
        self.tz = ZoneInfo(timezone)
        self._lock = threading.Lock()

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self._lock, FileLock(self.vault.resolve(LOCK_FILE)):
            yield

    def load(self) -> dict[str, Any]:
        path = self.vault.resolve(PLAN_FILE)
        if not path.is_file():
            return {"blocks": [], "updated": None, "summary": ""}
        return json.loads(path.read_text(encoding="utf-8"))

    def blocks(self) -> list[Block]:
        return [Block(**b) for b in self.load().get("blocks", [])]

    def save(self, blocks: list[Block], **extra: Any) -> None:
        data = self.load() | extra
        data["blocks"] = [asdict(b) for b in sorted(blocks, key=lambda b: (b.date, b.start))]
        write_atomic(self.vault.resolve(PLAN_FILE), json.dumps(data, indent=1, ensure_ascii=False))

    def set_status(self, block_id: str, status: str, minutes: int | None = None) -> Block:
        with self.locked():
            blocks = self.blocks()
            block = next((b for b in blocks if b.id == block_id), None)
            if block is None:
                raise KeyError(block_id)
            block.status = status
            block.minutes_done = minutes if status == PARTIAL else None
            self.save(blocks)
            return block

    def reconcile(self, google_plans: list[dict[str, Any]], window: dict[str, str]) -> list[str]:
        """Adopt what the owner changed in Google: moved events move their block, deleted
        events (inside the synced window) mark it removed. Returns one line per change."""
        if not window:
            return []
        w_start, w_end = _parse_dt(window["start"], self.tz), _parse_dt(window["end"], self.tz)
        by_id = {e["lifeos_id"]: e for e in google_plans if e.get("lifeos_id")}
        changes: list[str] = []
        with self.locked():
            blocks = self.blocks()
            for block in blocks:
                if not block.gid or block.status == REMOVED:
                    continue
                start, _ = block.span(self.tz)
                if not (w_start <= start < w_end):
                    continue
                event = by_id.get(block.id)
                if event is None:
                    block.status = REMOVED
                    changes.append(f"removed by you: {block.date} {block.start} {block.title}")
                    continue
                s, e = _parse_dt(event["start"], self.tz), _parse_dt(event["end"], self.tz)
                if (s.date().isoformat(), f"{s:%H:%M}", f"{e:%H:%M}") != (
                    block.date,
                    block.start,
                    block.end,
                ):
                    changes.append(
                        f"moved by you: {block.title} {block.date} {block.start} → "
                        f"{s:%Y-%m-%d %H:%M}"
                    )
                    block.date, block.start, block.end = (
                        s.date().isoformat(),
                        f"{s:%H:%M}",
                        f"{e:%H:%M}",
                    )
                if event.get("title") and event["title"] != block.title:
                    block.title = event["title"]
            if changes:
                self.save(blocks)
        return changes

    def apply(
        self,
        api: CalendarApi,
        target: str,
        keep: list[Block],
        wanted: list[Block],
        **extra: Any,
    ) -> ApplyReport:
        """Make Google and the store hold ``keep`` (untouched) plus ``wanted`` (the new plan for
        the open part of the horizon); blocks of the old plan missing in ``wanted`` go."""
        report = ApplyReport()
        old = {b.id: b for b in self.blocks()}
        keep_ids = {b.id for b in keep}
        wanted_ids = {b.id for b in wanted}
        for block in wanted:
            before = old.get(block.id)
            body = event_body(block, self.tz)
            try:
                if before is not None and before.gid:
                    block.gid = before.gid
                    if (before.date, before.start, before.end, before.title, before.category) != (
                        block.date,
                        block.start,
                        block.end,
                        block.title,
                        block.category,
                    ):
                        api.patch_event(target, before.gid, body)
                        report.moved += 1
                else:
                    block.gid = api.insert_event(target, body)["id"]
                    report.created += 1
            except Exception as exc:  # keep going; the next run retries
                report.errors.append(f"{block.title}: {exc}")
        for bid, block in old.items():
            if bid in keep_ids or bid in wanted_ids:
                continue
            if block.gid and block.status != REMOVED:
                try:
                    api.delete_event(target, block.gid)
                    report.deleted += 1
                except Exception as exc:
                    report.errors.append(f"delete {block.title}: {exc}")
        with self.locked():
            # check-ins made while the planner was thinking win
            current = {b.id: b for b in self.blocks()}
            final = keep + [b for b in wanted if b.gid]
            for block in final:
                if (now := current.get(block.id)) is not None:
                    block.status, block.minutes_done = now.status, now.minutes_done
            self.save(final, **extra)
        return report
