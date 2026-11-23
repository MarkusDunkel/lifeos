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
import json
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
        return {"target": front.get("target") or None, "inputs": inputs}

    def save_config(self, target: str | None, inputs: list[dict[str, str]]) -> None:
        path = self.config_path
        body = CONFIG_BODY
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            end = text.find("\n---\n", 4) if text.startswith("---\n") else -1
            body = text[end + 5 :] if end >= 0 else text
        front = {"target": target or "", "inputs": inputs}
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
        }
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
