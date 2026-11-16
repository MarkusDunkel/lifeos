"""Study sessions over the question banks: what to ask next, recording answers, readiness."""

from __future__ import annotations

import random
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.core.vault import STATE, Vault
from lifeos.modules.study.bank import (
    Subject,
    load_subjects,
    material_files,
    read_text,
    sections,
)
from lifeos.modules.study.progress import (
    PROGRESS_FILE,
    RESULTS,
    Attempt,
    ProgressLog,
    card_states,
    priority,
    readiness,
)

REPEAT_AFTER = 4  # a missed card comes back after this many other questions
MAX_SESSION = 60
SESSION_TTL = timedelta(hours=12)
MATERIAL_LIMIT = 6000

Committer = Callable[[list[str], str], None]


class StudyError(ValueError):
    pass


@dataclass
class Session:
    id: str
    subject: str  # subject key
    target: int
    topic: str | None
    created: datetime
    asked: list[str] = field(default_factory=list)
    answers: list[Attempt] = field(default_factory=list)
    repeats: list[tuple[str, int]] = field(default_factory=list)  # (card, ask after N answers)
    current: str | None = None
    ended: bool = False


class StudyService:
    def __init__(
        self,
        vault: Vault,
        timezone: str = "UTC",
        committer: Committer | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        rng: random.Random | None = None,
    ) -> None:
        self.vault = vault
        self.tz = ZoneInfo(timezone)
        self.committer = committer
        self.clock = clock
        self.rng = rng or random.Random()
        self.sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    # -- subjects -------------------------------------------------------------------

    def subjects(self) -> list[Subject]:
        return load_subjects(self.vault, self.tz)

    def subject(self, name: str) -> Subject:
        wanted = name.strip().lower()
        subjects = self.subjects()
        for s in subjects:
            if wanted in (s.key.lower(), s.name.lower(), s.code.lower()):
                return s
        matches = [
            s for s in subjects if wanted and (wanted in s.key.lower() or wanted in s.name.lower())
        ]
        if len(matches) == 1:
            return matches[0]
        known = ", ".join(s.key for s in subjects) or "none"
        raise StudyError(f"unknown subject '{name}' (known: {known})")

    def overview(self) -> list[dict]:
        now = self.clock()
        return [s.summary() | {"progress": self._readiness(s, now)} for s in self.subjects()]

    def progress(self, name: str) -> dict:
        subject = self.subject(name)
        return subject.summary() | self._readiness(subject, self.clock())

    # -- sessions ---------------------------------------------------------------------

    def start_session(self, name: str, count: int = 15, topic: str | None = None) -> dict:
        subject = self.subject(name)
        if not subject.cards:
            raise StudyError(f"'{subject.key}' has no question bank yet")
        count = max(1, min(int(count), MAX_SESSION))
        now = self.clock()
        with self._lock:
            self._expire(now)
            session = Session(uuid.uuid4().hex[:8], subject.key, count, topic or None, now)
            self.sessions[session.id] = session
        result = {
            "session_id": session.id,
            "subject": subject.summary(),
            "progress": self._readiness(subject, now),
            "question": self._next(session, subject),
        }
        return result

    def next_question(self, session_id: str) -> dict:
        session = self._session(session_id)
        return self._next(session, self.subject(session.subject))

    def record(
        self, session_id: str, card_id: str, result: str, note: str = "", mode: str = "voice"
    ) -> dict:
        session = self._session(session_id)
        subject = self.subject(session.subject)
        card = subject.card(card_id)
        if card is None:
            raise StudyError(f"unknown card '{card_id}' in {subject.key}")
        result = result.strip().lower()
        if result not in RESULTS:
            raise StudyError(f"result must be one of: {', '.join(RESULTS)}")
        attempt = Attempt(
            ts=self.clock().isoformat(timespec="seconds"),
            card=card.id,
            result=result,
            mode=mode,
            session=session.id,
            note=note.strip()[:500],
        )
        self._log(subject).append(attempt)
        with self._lock:
            session.answers.append(attempt)
            if session.current == card.id:
                session.current = None
            repeated = sum(1 for a in session.answers if a.card == card.id)
            if result != "correct" and repeated <= 2:
                session.repeats.append((card.id, len(session.answers) + REPEAT_AFTER))
        answered = len(session.answers)
        return {
            "recorded": True,
            "card": card.id,
            "result": result,
            "answered": answered,
            "correct": sum(a.result == "correct" for a in session.answers),
            "remaining": max(session.target - answered, 0) + len(session.repeats),
        }

    def end_session(self, session_id: str) -> dict:
        session = self._session(session_id)
        subject = self.subject(session.subject)
        with self._lock:
            session.ended = True
            self.sessions.pop(session.id, None)
        summary = self._summary(session, subject)
        if session.answers and self.committer is not None:
            self.committer(
                [f"{subject.folder}/{PROGRESS_FILE}"], self._commit_message(session, subject)
            )
        return summary

    # -- material -------------------------------------------------------------------

    def material(self, name: str, topic: str | None = None) -> dict:
        subject = self.subject(name)
        parts: list[tuple[str, str, str]] = []  # (file, heading, body)
        for path in material_files(self.vault, subject):
            for heading, body in sections(read_text(path)):
                parts.append((path.stem, heading, body))
        if not topic:
            return {
                "subject": subject.key,
                "sections": [f"{f} › {h}" if h else f for f, h, _ in parts],
                "hint": "call read_material again with a topic or section name",
            }
        wanted = topic.strip().lower()
        hits = [p for p in parts if wanted in p[1].lower() or wanted in p[0].lower()]
        if not hits:
            hits = [p for p in parts if wanted in p[2].lower()]
        text = "\n\n".join(f"## {h}\n{b}" if h else b for _, h, b in hits)
        cards = [
            c.as_dict()
            for c in subject.cards
            if wanted in c.topic.lower() or wanted in c.fields.get("Gruppe", "").lower()
        ]
        if not text and cards:
            text = "\n".join(
                f"- {c['title']}: {c.get('Definition') or c.get('Funktion') or ''}" for c in cards
            )
        if not text:
            raise StudyError(f"nothing found for '{topic}' in {subject.key}")
        truncated = len(text) > MATERIAL_LIMIT
        return {
            "subject": subject.key,
            "topic": topic,
            "text": text[:MATERIAL_LIMIT],
            "truncated": truncated,
        }

    # -- internals ------------------------------------------------------------------

    def _log(self, subject: Subject) -> ProgressLog:
        return ProgressLog(
            self.vault.resolve(f"{subject.folder}/{PROGRESS_FILE}"),
            self.vault.resolve(f"{STATE}/study/{subject.key}.lock"),
        )

    def _readiness(self, subject: Subject, now: datetime) -> dict:
        states = card_states([c.id for c in subject.cards], self._log(subject).read())
        return readiness(
            states,
            {c.id: c.title for c in subject.cards},
            {c.id: c.topic for c in subject.cards},
            now,
            subject.exam,
        )

    def _session(self, session_id: str) -> Session:
        session = self.sessions.get(session_id.strip())
        if session is None or session.ended:
            raise StudyError("unknown or finished session; start a new one with start_session")
        return session

    def _expire(self, now: datetime) -> None:
        for sid in [s.id for s in self.sessions.values() if now - s.created > SESSION_TTL]:
            self.sessions.pop(sid, None)

    def _next(self, session: Session, subject: Subject) -> dict:
        with self._lock:
            if session.current is None:
                session.current = self._pick(session, subject)
                if session.current is not None:
                    session.asked.append(session.current)
            current = session.current
        if current is None:
            return {"done": True, "summary": self._summary(session, subject)}
        card = subject.card(current)
        assert card is not None
        states = card_states([current], [a for a in self._log(subject).read() if a.card == current])
        state = states[current]
        return {
            "done": False,
            "position": len(session.answers) + 1,
            "of": session.target,
            "repeat": session.asked.count(current) > 1,
            "seen_before": state.seen,
            "last_result": state.last_result,
            "card": card.as_dict(),
        }

    def _pick(self, session: Session, subject: Subject) -> str | None:
        answered = len(session.answers)
        for i, (card, after) in enumerate(session.repeats):
            if answered >= after:
                session.repeats.pop(i)
                return card
        if answered >= session.target:
            if session.repeats and answered < session.target + 2 * REPEAT_AFTER:
                return session.repeats.pop(0)[0]
            return None

        topic = (session.topic or "").lower()
        cards = [
            c
            for c in subject.cards
            if c.id not in session.asked
            and (
                not topic or topic in c.topic.lower() or topic in c.fields.get("Gruppe", "").lower()
            )
        ]
        if not cards:
            return None
        now = self.clock()
        states = card_states([c.id for c in cards], self._log(subject).read())
        ranked = [(priority(states[c.id], now, subject.exam), c.id) for c in cards]
        due = [(p, cid) for p, cid in ranked if p is not None]
        if due:
            best = min(p[0] for p, _ in due)
            bucket = [cid for p, cid in due if p[0] == best]
            return self.rng.choice(bucket)
        # Nothing due: extra practice with the least secure, longest unseen cards.
        far = datetime.max.replace(tzinfo=UTC)
        extra = sorted(cards, key=lambda c: (states[c.id].box, states[c.id].last_at or far))
        return extra[0].id

    def _summary(self, session: Session, subject: Subject) -> dict:
        answers = session.answers
        missed = []
        for a in answers:
            card = subject.card(a.card)
            if a.result != "correct" and card and card.title not in missed:
                missed.append(card.title)
        return {
            "subject": subject.key,
            "answered": len(answers),
            "correct": sum(a.result == "correct" for a in answers),
            "partial": sum(a.result == "partial" for a in answers),
            "wrong": sum(a.result == "wrong" for a in answers),
            "to_review": missed,
            "progress": self._readiness(subject, self.clock()),
        }

    def _commit_message(self, session: Session, subject: Subject) -> str:
        s = self._summary(session, subject)
        subject_line = f"study: {subject.key} – {s['answered']} answers, {s['correct']} correct"
        body = [
            f"Readiness: {s['progress']['readiness']}% "
            f"({s['progress']['secure']}/{s['progress']['cards']} cards secure)"
        ]
        if s["to_review"]:
            body.append("To review:\n" + "\n".join(f"- {t}" for t in s["to_review"]))
        return subject_line + "\n\n" + "\n\n".join(body) + "\n"
