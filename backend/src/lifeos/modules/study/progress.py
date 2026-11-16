"""Progress log and exam-aware repetition (Leitner boxes).

Every answer is one JSON line in ``<subject>/lernstand.jsonl`` (append-only, so the file
never conflicts in git). The state of a card is always computed from the log:

- box 0..4: correct moves one box up, partial stays, wrong drops to box 0
- a card is due again after 0 / 4 h / 1 day / 2 days / 4 days, but never later than half
  the time left until the exam, so everything is repeated shortly before it
- a card counts as *secure* in box >= 2 with a correct last answer
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from lifeos.core.locks import FileLock

PROGRESS_FILE = "lernstand.jsonl"
RESULTS = ("correct", "partial", "wrong")
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

    @property
    def at(self) -> datetime:
        return datetime.fromisoformat(self.ts)


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
    last_at: datetime | None = None
    last_result: str | None = None

    @property
    def secure(self) -> bool:
        return self.box >= SECURE_BOX and self.last_result == "correct"

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
    for attempt in sorted(attempts, key=lambda a: a.at):
        state = states.get(attempt.card)
        if state is None:
            continue  # card was removed from the bank
        state.seen += 1
        state.last_at = attempt.at
        state.last_result = attempt.result
        if attempt.result == "correct":
            state.correct += 1
            state.box = min(state.box + 1, MAX_BOX)
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


def readiness(
    states: dict[str, CardState],
    titles: dict[str, str],
    topics: dict[str, str],
    now: datetime,
    exam: datetime | None,
) -> dict:
    total = len(states)
    secure = [s for s in states.values() if s.secure]
    seen = [s for s in states.values() if s.seen]
    due = [s for s in states.values() if priority(s, now, exam) is not None]
    # Correct answers still needed until every card is secure (box 2, last answer correct).
    needed = sum(max(1, SECURE_BOX - s.box) for s in states.values() if not s.secure)
    minutes = round(needed * SECONDS_PER_ANSWER / 60)
    days_left = None
    per_day = None
    if exam is not None:
        days_left = max((exam - now).total_seconds() / 86400, 0)
        per_day = minutes if days_left < 1 else round(minutes / max(int(days_left), 1))
    weak = sorted(
        (s for s in states.values() if s.seen and not s.secure),
        key=lambda s: (-(s.wrong + 0.5 * s.partial), s.box),
    )[:8]
    by_topic: dict[str, dict] = {}
    for s in states.values():
        entry = by_topic.setdefault(topics[s.card], {"cards": 0, "secure": 0, "seen": 0})
        entry["cards"] += 1
        entry["secure"] += int(s.secure)
        entry["seen"] += int(bool(s.seen))
    return {
        "cards": total,
        "seen": len(seen),
        "secure": len(secure),
        "new": total - len(seen),
        "readiness": round(100 * len(secure) / total) if total else 0,
        "dueNow": len(due),
        "examInDays": round(days_left, 1) if days_left is not None else None,
        "minutesNeeded": minutes,
        "minutesPerDay": per_day,
        "weak": [
            {"id": s.card, "title": titles[s.card], "wrong": s.wrong, "partial": s.partial}
            for s in weak
        ],
        "topics": by_topic,
    }
