"""Planning API: goals, the day and week view, check-ins, journal, wishes, re-plan."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from lifeos.core.vault import KNOWLEDGE, revision_of, write_checked
from lifeos.modules.planning import goals as goals_mod
from lifeos.modules.planning.journal import Entry
from lifeos.modules.planning.plan import STATUSES, Block, busy_times
from lifeos.modules.planning.planner import Planner, week_start

Committer = Callable[[list[str], str], None]


class GoalIn(BaseModel):
    name: str = Field(min_length=1)
    group: str = ""  # name of the parent goal; "" = top level
    amount: float | None = Field(default=None, ge=0)  # None = auto
    unit: Literal["hours", "sessions", "percent"] = "hours"
    priority: str = "mittel"
    note: str = ""


class GoalsIn(BaseModel):
    goals: list[GoalIn]
    rules: str = ""
    revision: str | None = None


class StatusIn(BaseModel):
    status: Literal["planned", "done", "partial", "skipped"]
    minutes: int | None = Field(default=None, ge=0, le=24 * 60)
    note: str = ""


class BlockEdit(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=8000)


class JournalIn(BaseModel):
    mood: int | None = Field(default=None, ge=1, le=5)
    energy: int | None = Field(default=None, ge=1, le=5)
    sleep: float | None = Field(default=None, ge=0, le=24)
    pain: int | None = Field(default=None, ge=0, le=10)
    pain_where: str = ""
    good: str = ""
    bad: str = ""
    notes: str = ""


class WishIn(BaseModel):
    day: str
    text: str = Field(min_length=1, max_length=2000)


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(400, f"invalid date: {value}") from exc


def build_router(planner: Planner, committer: Committer | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/planning", tags=["planning"])
    journal = planner.journal

    def commit(paths: list[str], message: str) -> None:
        if committer:
            committer([planner.vault.relative(p) for p in paths], message)

    # -- goals -----------------------------------------------------------------------------

    @router.get("/goals")
    def get_goals() -> dict[str, Any]:
        path = planner.goals_path
        _, rules = planner.goals()
        hours, exams = planner.auto_study()
        return {
            "goals": planner.targets(),
            "rules": rules,
            "revision": revision_of(path),
            "file": planner.vault.relative(path, zone=KNOWLEDGE),
            "autoStudy": {"hours": hours, "exams": exams},
        }

    @router.put("/goals")
    def put_goals(body: GoalsIn) -> dict[str, Any]:
        path = planner.goals_path
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        names = [g.name.strip() for g in body.goals]
        if len(set(names)) != len(names):
            raise HTTPException(400, "every goal needs its own name")
        goals = [
            goals_mod.Goal(
                name=g.name.strip(),
                amount=g.amount,
                unit=g.unit,
                priority=g.priority.strip() or "mittel",
                note=g.note.strip(),
                group=g.group.strip(),
            )
            for g in body.goals
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        write_checked(path, goals_mod.render(text, goals, body.rules), body.revision)
        commit([path], "planning: goals updated\n")
        planner.enqueue("goals changed")
        return get_goals()

    # -- days --------------------------------------------------------------------------------

    def _day_view(d: date, blocks: list[Block], store: dict[str, Any]) -> dict[str, Any]:
        busy, all_day = busy_times(store, planner.tz)
        iso = d.isoformat()
        day_start = datetime.combine(d, time(0), planner.tz)
        day_end = day_start + timedelta(days=1)
        return {
            "date": iso,
            "blocks": [asdict(b) | {"minutes": b.minutes} for b in blocks if b.date == iso],
            "appointments": [
                {
                    "start": f"{max(b.start, day_start):%H:%M}",
                    "end": "24:00" if b.end >= day_end else f"{b.end:%H:%M}",
                    "title": b.title,
                    "calendar": b.source,
                }
                for b in busy
                if b.start < day_end and b.end > day_start
            ],
            "allDay": [a["title"] for a in all_day if a["start"] <= iso < a["end"]],
        }

    @router.get("/day/{day}")
    def get_day(day: str) -> dict[str, Any]:
        d = _day(day)
        view = _day_view(d, planner.plans.blocks(), planner.calendar.store())
        return view | {
            "journal": asdict(journal.read(d.isoformat())),
            "wishes": [w for w in journal.wishes() if w["day"] == d.isoformat()],
        }

    @router.get("/week")
    def get_week(start: str | None = None) -> dict[str, Any]:
        first = week_start(_day(start)) if start else week_start(planner.now().date())
        blocks = planner.plans.blocks()
        store = planner.calendar.store()
        return {
            "start": first.isoformat(),
            "days": [_day_view(first + timedelta(days=n), blocks, store) for n in range(7)],
            "goals": planner.targets(first),
        }

    @router.patch("/blocks/{block_id}")
    def edit_block(block_id: str, body: BlockEdit) -> dict[str, Any]:
        """The owner's own title and description; written to Google right away."""
        try:
            block = planner.plans.edit(block_id, body.title, body.description)
        except KeyError as exc:
            raise HTTPException(404, f"no block {block_id}") from exc
        if block.gid and planner.calendar.api is not None:
            target = planner.calendar.config()["target"]
            if target:
                planner.calendar.api().patch_event(
                    target, block.gid, {"summary": block.title, "description": block.reason}
                )
        return asdict(block) | {"minutes": block.minutes}

    @router.post("/blocks/{block_id}/status")
    def set_status(block_id: str, body: StatusIn) -> dict[str, Any]:
        if body.status not in STATUSES:
            raise HTTPException(400, "unknown status")
        try:
            block = planner.plans.set_status(block_id, body.status, body.minutes)
        except KeyError as exc:
            raise HTTPException(404, f"no block {block_id}") from exc
        journal.log_checkin(
            {
                "block": block.id,
                "date": block.date,
                "start": block.start,
                "end": block.end,
                "goal": block.goal,
                "category": block.category,
                "title": block.title,
                "status": block.status,
                "minutes": block.done_minutes(),
                "note": body.note,
            },
            planner.now(),
        )
        return asdict(block) | {"minutes": block.minutes}

    # -- journal -----------------------------------------------------------------------------

    @router.put("/journal/{day}")
    def put_journal(day: str, body: JournalIn) -> dict[str, Any]:
        d = _day(day)
        entry = journal.write(Entry(date=d.isoformat(), **body.model_dump()))
        commit([journal.entry_path(d.isoformat())], f"journal: {d.isoformat()}\n")
        return asdict(entry)

    @router.get("/journal")
    def list_journal(days: int = 60) -> list[dict[str, Any]]:
        until = planner.now().date()
        return journal.entries(until - timedelta(days=max(1, min(days, 400))), until)

    # -- wishes and runs ------------------------------------------------------------------------

    @router.post("/wishes")
    def add_wish(body: WishIn) -> dict[str, Any]:
        wish = journal.add_wish(_day(body.day).isoformat(), body.text, planner.now())
        planner.enqueue("wish")
        return wish

    @router.delete("/wishes/{wish_id}")
    def delete_wish(wish_id: str) -> dict[str, str]:
        journal.delete_wish(wish_id)
        return {"status": "deleted"}

    @router.post("/replan")
    def replan() -> dict[str, Any]:
        planner.enqueue("plan again")
        return status()

    @router.get("/status")
    def status() -> dict[str, Any]:
        data = planner.plans.load()
        return {
            "queue": planner.queue(),
            "runs": planner.runs(5),
            "summary": data.get("summary", ""),
            "updated": data.get("updated"),
            "rejected": data.get("rejected", []),
            "changes": data.get("changes", []),
            "now": planner.now().isoformat(timespec="minutes"),
            "running": planner.running(),
            **planner.limits(),
        }

    return router
