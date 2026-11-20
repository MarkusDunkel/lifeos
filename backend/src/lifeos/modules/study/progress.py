"""Progress log and exam-aware repetition (Leitner boxes).

Every answer is one JSON line in ``<subject>/lernstand.jsonl`` (append-only, so the file
never conflicts in git). The state of a card is always computed from the log:

- box 0..4: correct moves one box up, partial stays, wrong drops to box 0
- known (the learner marks a card as trivial / already known) moves it straight to box 4
- a card is due again after 0 / 4 h / 1 day / 2 days / 4 days, but never later than half
  the time left until the exam, so everything is repeated shortly before it
- a card counts as *secure* in box >= 2 with a correct (or known) last answer

A grade is never edited in place. A correction is a new line with ``mode="correction"``
and ``replaces`` set to the timestamp of the original attempt (same card); the latest
correction takes the original's place, at the original's time.

The exam date comes from the card's question bank (or the subject), so topics with
different exams are paced separately.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from lifeos.core.locks import FileLock
from lifeos.modules.study.bank import Card

PROGRESS_FILE = "lernstand.jsonl"
RESULTS = ("correct", "partial", "wrong", "known")
PASSED = ("correct", "known")  # results that count as a right answer
CORRECTION = "correction"
INTERVALS = [
    timedelta(0),
    timedelta(hours=4),
    timedelta(days=1),
    timedelta(days=2),
    timedelta(days=4),
]
MAX_BOX = len(INTERVALS) - 1
SECURE_BOX = 2
SECONDS_PER_ANSWER = 20


@dataclass
class Attempt:
    ts: str  # ISO 8601 with offset
    card: str
    result: str
    mode: str = "voice"
    session: str = ""
    note: str = ""
    replaces: str = ""  # ts of the corrected attempt (corrections only)

    @property
    def at(self) -> datetime:
        return datetime.fromisoformat(self.ts)

    @property
    def is_correction(self) -> bool:
        return bool(self.replaces)


def resolve_corrections(attempts: list[Attempt]) -> list[Attempt]:
    """The effective attempts: every corrected original replaced by its latest correction,
    keeping the original's timestamp. Corrections without an original are dropped."""
    latest: dict[tuple[str, str], Attempt] = {}
    for a in sorted((a for a in attempts if a.is_correction), key=lambda a: a.at):
        latest[(a.card, a.replaces)] = a
    result = []
    for a in attempts:
        if a.is_correction:
            continue
        fix = latest.get((a.card, a.ts))
        if fix is not None:
            a = replace(a, result=fix.result, note=fix.note or a.note)
        result.append(a)
    return result


class ProgressLog:
    def __init__(self, path: Path, lock_path: Path) -> None:
        self.path = path
        self.lock_path = lock_path

    def append(self, attempt: Attempt) -> None:
        if attempt.result not in RESULTS:
            raise ValueError(f"result must be one of {', '.join(RESULTS)}")
        line = json.dumps(asdict(attempt), ensure_ascii=False) + "\n"
        with FileLock(self.lock_path), self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line)

    def read(self) -> list[Attempt]:
        if not self.path.is_file():
            return []
        attempts = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                attempts.append(
                    Attempt(**{k: data[k] for k in data if k in Attempt.__annotations__})
                )
            except (ValueError, TypeError):
                continue  # a broken line must not break learning
        return attempts


@dataclass
class CardState:
    card: str
    box: int = 0
    seen: int = 0
    correct: int = 0
    partial: int = 0
    wrong: int = 0
    known: int = 0
    last_at: datetime | None = None
    last_result: str | None = None

    @property
    def secure(self) -> bool:
        return self.box >= SECURE_BOX and self.last_result in PASSED

    def due_at(self, exam: datetime | None) -> datetime | None:
        """When the card should be asked again; ``None`` for a card never seen."""
        if self.last_at is None:
            return None
        interval = INTERVALS[self.box]
        if exam is not None and exam > self.last_at:
            interval = min(interval, (exam - self.last_at) / 2)
        return self.last_at + interval


def card_states(card_ids: list[str], attempts: list[Attempt]) -> dict[str, CardState]:
    states = {cid: CardState(cid) for cid in card_ids}
    for attempt in sorted(resolve_corrections(attempts), key=lambda a: a.at):
        state = states.get(attempt.card)
        if state is None:
            continue  # card was removed from the bank
        state.seen += 1
        state.last_at = attempt.at
        state.last_result = attempt.result
        if attempt.result == "correct":
            state.correct += 1
            state.box = min(state.box + 1, MAX_BOX)
        elif attempt.result == "known":
            state.known += 1
            state.box = MAX_BOX
        elif attempt.result == "partial":
            state.partial += 1
        else:
            state.wrong += 1
            state.box = 0
    return states


def priority(state: CardState, now: datetime, exam: datetime | None) -> tuple[int, float] | None:
    """Sort key for due cards (lower first); ``None`` if the card is not due."""
    if state.last_at is None:
        return (2, 0.0)  # new
    due = state.due_at(exam)
    if due is not None and due > now:
        return None
    overdue = -(now - due).total_seconds() if due else 0.0
    if state.last_result == "wrong":
        return (0, overdue)
    if state.last_result == "partial":
        return (1, overdue)
    return (3, overdue)


def status(state: CardState) -> str:
    """Learning status of a card: new, wrong, partial, learning or secure."""
    if not state.seen:
        return "new"
    if state.secure:
        return "secure"
    if state.last_result in ("wrong", "partial"):
        return state.last_result
    return "learning"  # answered correctly, but not often enough yet


def _minutes_needed(states: list[CardState]) -> float:
    # Correct answers still needed until every card is secure (box 2, last answer correct).
    needed = sum(max(1, SECURE_BOX - s.box) for s in states if not s.secure)
    return needed * SECONDS_PER_ANSWER / 60


def _per_day(minutes: float, exam: datetime | None, now: datetime) -> float | None:
    """Minutes per day until an upcoming exam; ``None`` without one or after it."""
    if exam is None or exam <= now:
        return None
    days_left = (exam - now).total_seconds() / 86400
    return minutes if days_left < 1 else minutes / int(days_left)


def _days(exam: datetime | None, now: datetime) -> float | None:
    if exam is None:
        return None
    return round(max((exam - now).total_seconds() / 86400, 0), 1)


def readiness(
    states: dict[str, CardState], cards: list[Card], now: datetime, next_exam: datetime | None
) -> dict:
    """Readiness of a subject, overall (towards ``next_exam``) and per topic (towards the
    exam of the topic's bank)."""
    by_id = {c.id: c for c in cards}
    total = len(states)
    secure = [s for s in states.values() if s.secure]
    seen = [s for s in states.values() if s.seen]
    due = [s for s in states.values() if priority(s, now, by_id[s.card].exam) is not None]
    weak = sorted(
        (s for s in states.values() if s.seen and not s.secure),
        key=lambda s: (-(s.wrong + 0.5 * s.partial), s.box),
    )[:8]

    grouped: dict[str, list[CardState]] = {}
    for s in states.values():
        grouped.setdefault(by_id[s.card].topic, []).append(s)
    by_topic: dict[str, dict] = {}
    per_day_total: float | None = None
    for topic, group in grouped.items():
        exam = by_id[group[0].card].exam
        minutes = _minutes_needed(group)
        per_day = _per_day(minutes, exam, now)
        if per_day is not None:
            per_day_total = (per_day_total or 0) + per_day
        by_topic[topic] = {
            "cards": len(group),
            "secure": sum(s.secure for s in group),
            "seen": sum(bool(s.seen) for s in group),
            "dueNow": sum(priority(s, now, exam) is not None for s in group),
            "exam": exam.isoformat() if exam else None,
            "examInDays": _days(exam, now),
            "minutesNeeded": round(minutes),
            "minutesPerDay": math.ceil(per_day) if per_day is not None else None,
        }
    return {
        "cards": total,
        "seen": len(seen),
        "secure": len(secure),
        "new": total - len(seen),
        "readiness": round(100 * len(secure) / total) if total else 0,
        "dueNow": len(due),
        "nextExam": next_exam.isoformat() if next_exam else None,
        "examInDays": _days(next_exam, now),
        "minutesNeeded": round(_minutes_needed(list(states.values()))),
        "minutesPerDay": (math.ceil(per_day_total) if per_day_total is not None else None),
        "weak": [
            {"id": s.card, "title": by_id[s.card].title, "wrong": s.wrong, "partial": s.partial}
            for s in weak
        ],
        "topics": by_topic,
    }
