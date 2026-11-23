"""Collect the input calendars and mirror them into the LifeOS target calendar.

* Which calendars are inputs and which one is the target lives in the vault
  (``ready/knowledge/<planning_dir>/Calendars.md``, front matter).
* Every run fetches a rolling window of events from each input calendar and
  makes the target contain a *mirror* of each one. Mirrors and planned blocks
  carry ``extendedProperties.private.lifeos_kind``; events in the target without
  that marker were added by hand and are never touched (they count as fixed).
* The collected events go to ``.lifeos/calendar/events.json`` for the planner.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from lifeos.connectors.google.auth import GoogleError
from lifeos.connectors.google.client import CalendarApi
from lifeos.core.extract.text import dump_front, front_matter
from lifeos.core.locks import FileLock
from lifeos.core.vault import KNOWLEDGE, STATE, Vault, write_atomic

CONFIG_FILE = "Calendars.md"
EVENTS_FILE = f"{STATE}/calendar/events.json"
REPORT_FILE = f"{STATE}/calendar/last-sync.json"
LOCK_FILE = f"{STATE}/calendar/sync.lock"
TARGET_NAME = "LifeOS"
DAYS_BACK = 7
DAYS_AHEAD = 35
MIRROR = "mirror"
PLAN = "plan"
MIRROR_COLOR = "8"  # graphite: appointments from elsewhere
THREAD = "thread"
THREAD_COLOR = "7"  # peacock
THREAD_DAYS = 8  # today and the next seven days get a thread event
THREAD_KEEP_LINES = 12
# Wording of the thread events; ``thread:`` in Calendars.md overrides it (e.g. in German).
THREAD_DEFAULTS = {
    "title": "💬 Planner",
    "separator": "——— Planner ———",
    "hint": "Write above this line what the planner should know about this day.",
}


def plain_text(text: str) -> str:
    """Event descriptions edited on the web come back as HTML: turn them into plain lines."""
    if "<" not in text and "&" not in text:
        return text.strip()
    text = re.sub(r"(?i)<br\s*/?>|</?p(\s[^>]*)?>|</?div(\s[^>]*)?>|</li>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", html.unescape(text))
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def split_thread(description: str, separator: str) -> tuple[str, str]:
    """(owner's part above the separator line, planner's part below it)."""
    text = plain_text(description)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == separator.strip():
            return "\n".join(lines[:i]).strip(), "\n".join(lines[i + 1 :]).strip()
    return text, ""


def answer_lines(planner: str, texts: dict[str, str]) -> list[str]:
    """The planner's answers in a thread (without the empty-state hint)."""
    return [ln for ln in planner.splitlines() if ln.strip() and ln.strip() != texts["hint"]]


def join_thread(owner: str, planner_lines: list[str], texts: dict[str, str]) -> str:
    below = "\n".join(planner_lines) if planner_lines else texts["hint"]
    return (owner.strip() + "\n\n" if owner.strip() else "\n") + texts["separator"] + "\n" + below


def thread_body(day: date, texts: dict[str, str]) -> dict[str, Any]:
    return {
        "summary": texts["title"],
        "description": join_thread("", [], texts),
        "start": {"date": day.isoformat()},
        "end": {"date": (day + timedelta(days=1)).isoformat()},
        "transparency": "transparent",
        "colorId": THREAD_COLOR,
        "reminders": {"useDefault": False, "overrides": []},
        "extendedProperties": {
            "private": {"lifeos_kind": THREAD, "lifeos_id": f"thread-{day.isoformat()}"}
        },
    }


CONFIG_BODY = """
# Calendars

LifeOS reads the **inputs** and mirrors their events into the **target**
calendar, together with the blocks the planner adds. Change the selection on
the Plan page (Calendars). Events you add to the target by hand are kept and
treated as fixed appointments.
"""


@dataclass
class SyncReport:
    at: str = ""
    inputs: int = 0
    events: int = 0
    created: int = 0
    updated: int = 0
    deleted: int = 0
    manual: int = 0
    errors: list[str] = field(default_factory=list)


def event_window(today: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(today - timedelta(days=DAYS_BACK), datetime.min.time(), tz)
    end = datetime.combine(today + timedelta(days=DAYS_AHEAD), datetime.min.time(), tz)
    return start, end


def normalize(raw: dict[str, Any], calendar_id: str, label: str) -> dict[str, Any] | None:
    """A compact, comparable form of a Google event; ``None`` for what should be ignored."""
    if raw.get("status") == "cancelled":
        return None
    for attendee in raw.get("attendees") or []:
        if attendee.get("self") and attendee.get("responseStatus") == "declined":
            return None
    start, end = raw.get("start") or {}, raw.get("end") or {}
    all_day = "date" in start
    s = start.get("date") or start.get("dateTime")
    e = end.get("date") or end.get("dateTime")
    if not s or not e:
        return None
    private = (raw.get("extendedProperties") or {}).get("private") or {}
    return {
        "key": f"{calendar_id}|{raw['id']}",
        "id": raw["id"],
        "calendar_id": calendar_id,
        "calendar": label,
        "title": raw.get("summary") or "(busy)",
        "start": s,
        "end": e,
        "all_day": all_day,
        "busy": raw.get("transparency") != "transparent",
        "location": raw.get("location") or "",
        "description": raw.get("description") or "",
        "kind": private.get("lifeos_kind") or "",
        "lifeos_id": private.get("lifeos_id") or "",
        "hash": private.get("lifeos_hash") or "",
        "raw_start": start,
        "raw_end": end,
    }


def mirror_body(event: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "summary": event["title"],
        "location": event["location"],
        "description": f"Mirrored from “{event['calendar']}” by LifeOS – change it there.",
        "start": event["raw_start"],
        "end": event["raw_end"],
        "transparency": "opaque" if event["busy"] else "transparent",
        "colorId": MIRROR_COLOR,
    }
    fingerprint = hashlib.sha1(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]
    body["extendedProperties"] = {
        "private": {
            "lifeos_kind": MIRROR,
            "lifeos_id": mirror_id(event),
            "lifeos_hash": fingerprint,
        }
    }
    return body


def mirror_id(event: dict[str, Any]) -> str:
    return hashlib.sha1(event["key"].encode()).hexdigest()[:20]


class CalendarSync:
    def __init__(
        self,
        vault: Vault,
        api: Callable[[], CalendarApi] | None,
        timezone: str,
        planning_dir: str = "Planning",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.vault = vault
        self.api = api
        self.tz = ZoneInfo(timezone)
        self.planning_dir = planning_dir
        self._clock = clock
        self._lock = threading.Lock()

    # -- configuration in the vault -----------------------------------------------

    @property
    def config_path(self) -> Path:
        return self.vault.resolve(f"{self.planning_dir}/{CONFIG_FILE}", zone=KNOWLEDGE)

    def config(self) -> dict[str, Any]:
        path = self.config_path
        front = front_matter(path.read_text(encoding="utf-8")) if path.is_file() else {}
        inputs = [
            {"id": str(i["id"]), "label": str(i.get("label") or i["id"])}
            for i in front.get("inputs") or []
            if isinstance(i, dict) and i.get("id")
        ]
        thread = front.get("thread") if isinstance(front.get("thread"), dict) else {}
        return {
            "target": front.get("target") or None,
            "inputs": inputs,
            "thread": {k: str(thread.get(k) or v) for k, v in THREAD_DEFAULTS.items()},
        }

    def save_config(self, target: str | None, inputs: list[dict[str, str]]) -> None:
        """Write target and inputs; every other key of the front matter is kept."""
        path = self.config_path
        body = CONFIG_BODY
        front: dict[str, Any] = {}
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            front = front_matter(text)
            end = text.find("\n---\n", 4) if text.startswith("---\n") else -1
            body = text[end + 5 :] if end >= 0 else text
        front = front | {"target": target or "", "inputs": inputs}
        write_atomic(path, dump_front(front) + body)

    def set_inputs(self, inputs: list[dict[str, str]]) -> dict[str, Any]:
        cfg = self.config()
        cleaned = [
            {"id": i["id"], "label": (i.get("label") or i["id"]).strip()}
            for i in inputs
            if i.get("id") and i["id"] != cfg["target"]
        ]
        self.save_config(cfg["target"], cleaned)
        return self.config()

    # -- Google ---------------------------------------------------------------------

    def _client(self) -> CalendarApi:
        if self.api is None:
            raise GoogleError("Google Calendar is not set up", 400)
        return self.api()

    def calendars(self) -> list[dict[str, Any]]:
        cfg = self.config()
        chosen = {i["id"]: i["label"] for i in cfg["inputs"]}
        return [
            {
                "id": c["id"],
                "name": c.get("summaryOverride") or c.get("summary") or c["id"],
                "primary": bool(c.get("primary")),
                "color": c.get("backgroundColor"),
                "input": c["id"] in chosen,
                "label": chosen.get(c["id"]),
                "target": c["id"] == cfg["target"],
            }
            for c in self._client().calendars()
        ]

    def ensure_target(self, api: CalendarApi) -> str:
        cfg = self.config()
        known = {c["id"] for c in api.calendars()}
        if cfg["target"] and cfg["target"] in known:
            return cfg["target"]
        target = api.create_calendar(TARGET_NAME, str(self.tz))
        self.save_config(target, cfg["inputs"])
        return target

    def sync(self) -> SyncReport:
        # one sync at a time across the web app and the worker: overlapping runs would both
        # see a mirror missing and both create it
        with self._lock, FileLock(self.vault.resolve(LOCK_FILE)):
            report = SyncReport(at=datetime.fromtimestamp(self._clock(), self.tz).isoformat())
            try:
                self._sync(report)
            except GoogleError as exc:
                report.errors.append(str(exc))
        write_atomic(self.vault.resolve(REPORT_FILE), json.dumps(asdict(report), indent=2))
        return report

    def _sync(self, report: SyncReport) -> None:
        api = self._client()
        target = self.ensure_target(api)
        cfg = self.config()
        now = datetime.fromtimestamp(self._clock(), self.tz)
        start, end = event_window(now.date(), self.tz)
        window = (start.isoformat(), end.isoformat())

        events: list[dict[str, Any]] = []
        for cal in cfg["inputs"]:
            if cal["id"] == target:
                continue
            try:
                raw = api.events(cal["id"], *window)
            except GoogleError as exc:
                report.errors.append(f"{cal['label']}: {exc}")
                continue
            report.inputs += 1
            events += [e for r in raw if (e := normalize(r, cal["id"], cal["label"]))]
        report.events = len(events)

        in_target = [
            e for r in api.events(target, *window) if (e := normalize(r, target, TARGET_NAME))
        ]
        mirrors: dict[str, dict[str, Any]] = {}
        for e in in_target:
            if e["kind"] != MIRROR:
                continue
            if e["lifeos_id"] in mirrors:  # a copy, e.g. from an earlier overlapping sync
                api.delete_event(target, e["id"])
                report.deleted += 1
            else:
                mirrors[e["lifeos_id"]] = e
        manual = [e for e in in_target if not e["kind"]]
        plans = [e for e in in_target if e["kind"] == PLAN]
        report.manual = len(manual)
        threads = self._threads(api, target, in_target, now.date(), cfg["thread"])

        wanted: dict[str, dict[str, Any]] = {}
        for event in events:
            wanted[mirror_id(event)] = event
        for mid, event in wanted.items():
            body = mirror_body(event)
            have = mirrors.get(mid)
            if have is None:
                api.insert_event(target, body)
                report.created += 1
            elif _hash_of(have) != body["extendedProperties"]["private"]["lifeos_hash"]:
                api.patch_event(target, have["id"], body)
                report.updated += 1
        for mid, have in mirrors.items():
            if mid not in wanted:
                api.delete_event(target, have["id"])
                report.deleted += 1

        store = {
            "synced_at": report.at,
            "window": {"start": window[0], "end": window[1]},
            "target": target,
            "events": [_public(e) for e in events],
            "manual": [_public(e) for e in manual],
            "plans": [_public(e) for e in plans],
            "threads": threads,
        }
        write_atomic(
            self.vault.resolve(EVENTS_FILE), json.dumps(store, indent=1, ensure_ascii=False)
        )

    # -- day threads: one all-day event per day to talk to the planner ------------------------

    def _threads(
        self,
        api: CalendarApi,
        target: str,
        in_target: list[dict[str, Any]],
        today: date,
        texts: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Make sure today and the next days have their thread event; return every thread in
        the window with the owner's part and the planner's part of its description."""
        have: dict[str, dict[str, Any]] = {}
        for e in in_target:
            if e["kind"] != THREAD:
                continue
            if e["lifeos_id"] in have:  # a copy from an overlapping sync
                api.delete_event(target, e["id"])
            else:
                have[e["lifeos_id"]] = e
        for n in range(THREAD_DAYS):
            day = today + timedelta(days=n)
            tid = f"thread-{day.isoformat()}"
            if tid not in have:
                created = api.insert_event(target, thread_body(day, texts))
                have[tid] = {"id": created["id"], "lifeos_id": tid, "start": day.isoformat(),
                             "description": created.get("description", "")}  # fmt: skip
        out = []
        for tid, e in sorted(have.items()):
            owner, planner = split_thread(e.get("description") or "", texts["separator"])
            out.append({"date": tid.removeprefix("thread-"), "id": e["id"], "owner": owner,
                        "planner": planner})  # fmt: skip
        return out

    def answer_thread(self, day: str, lines: list[str]) -> bool:
        """Add the planner's ``lines`` to the thread of ``day`` (owner's part re-read right
        before writing, so text typed meanwhile is kept). False if there is no thread."""
        thread = next((t for t in self.store().get("threads", []) if t["date"] == day), None)
        if thread is None or not lines:
            return False
        api = self._client()
        target = self.config()["target"]
        texts = self.config()["thread"]
        fresh = api.get_event(target, thread["id"])
        owner, planner = split_thread(fresh.get("description") or "", texts["separator"])
        kept = (answer_lines(planner, texts) + lines)[-THREAD_KEEP_LINES:]
        api.patch_event(target, thread["id"], {"description": join_thread(owner, kept, texts)})
        self._remember_thread(day, owner, "\n".join(kept))
        return True

    def write_note(self, day: str, text: str, base: str | None) -> tuple[bool, str]:
        """The owner's note for ``day``, written from the app. ``base`` is the note as the app
        showed it: if the calendar holds something else by now (typed on the phone meanwhile),
        nothing is written. Returns (written, the note now in the calendar)."""
        thread = next((t for t in self.store().get("threads", []) if t["date"] == day), None)
        if thread is None:
            raise GoogleError(f"no planner entry for {day} (only today and the next days)", 404)
        api = self._client()
        target = self.config()["target"]
        texts = self.config()["thread"]
        fresh = api.get_event(target, thread["id"])
        owner, planner = split_thread(fresh.get("description") or "", texts["separator"])
        if base is not None and owner.strip() != base.strip():
            self._remember_thread(day, owner, planner)
            return False, owner
        kept = answer_lines(planner, texts)
        api.patch_event(target, thread["id"], {"description": join_thread(text, kept, texts)})
        self._remember_thread(day, text.strip(), "\n".join(kept))
        return True, text.strip()

    def _remember_thread(self, day: str, owner: str, planner: str) -> None:
        """Update one thread in the local copy, so the app shows it before the next sync."""
        with self._lock, FileLock(self.vault.resolve(LOCK_FILE)):
            store = self.store()
            for t in store.get("threads", []):
                if t["date"] == day:
                    t["owner"], t["planner"] = owner, planner
            write_atomic(
                self.vault.resolve(EVENTS_FILE), json.dumps(store, indent=1, ensure_ascii=False)
            )

    # -- read side ------------------------------------------------------------------

    def store(self) -> dict[str, Any]:
        path = self.vault.resolve(EVENTS_FILE)
        if not path.is_file():
            return {"synced_at": None, "events": [], "manual": [], "plans": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def last_report(self) -> dict[str, Any] | None:
        path = self.vault.resolve(REPORT_FILE)
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _hash_of(event: dict[str, Any]) -> str:
    return event.get("hash", "")


def _public(event: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in event.items() if not k.startswith("raw_")}
