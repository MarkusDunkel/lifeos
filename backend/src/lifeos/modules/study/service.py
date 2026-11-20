"""Study sessions over the question banks: what to ask next, recording answers, readiness."""

from __future__ import annotations

import random
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.core.extract.text import front_matter
from lifeos.core.vault import STATE, Vault
from lifeos.modules.study.bank import (
    SUBJECT_FILE,
    Card,
    Subject,
    load_subjects,
    material_files,
    read_text,
    sections,
)
from lifeos.modules.study.progress import (
    CORRECTION,
    PASSED,
    PROGRESS_FILE,
    RESULTS,
    Attempt,
    ProgressLog,
    card_states,
    priority,
    readiness,
    resolve_corrections,
    status,
)

REPEAT_AFTER = 4  # a missed card comes back after this many other questions
MAX_SESSION = 60
IDLE_END = timedelta(minutes=30)  # sessions without activity are ended (and committed)
MATERIAL_LIMIT = 6000
PENDING_HINT = (
    "The answer to this card is not saved yet. Call record_answer for it first; if the "
    "learner wants to move on without answering, call next_question with skip=true."
)
KNOWN_NOTE = "marked as known by the learner"

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
    skipped: list[str] = field(default_factory=list)
    current: str | None = None  # card handed out and not yet answered or skipped
    corrections: int = 0  # grades changed by the learner during this session
    ended: bool = False
    last_activity: datetime | None = None


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


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
        return [s.summary(now) | {"progress": self._readiness(s, now)} for s in self.subjects()]

    def progress(self, name: str) -> dict:
        subject = self.subject(name)
        now = self.clock()
        return subject.summary(now) | self._readiness(subject, now)

    def detail(self, name: str) -> dict:
        """Everything the study page shows for a subject: exams, question banks with every
        card and its learning status, learning texts and readiness. File paths are relative
        to ``ready/knowledge`` (the knowledge API edits them)."""
        subject = self.subject(name)
        now = self.clock()
        states = card_states([c.id for c in subject.cards], self._log(subject).read())
        banks = []
        for bank in subject.banks:
            cards = []
            for card in bank.cards:
                state = states[card.id]
                cards.append(
                    {
                        "id": card.id,
                        "title": card.title,
                        "fields": card.fields,
                        "status": status(state),
                        "box": state.box,
                        "seen": state.seen,
                        "wrong": state.wrong,
                        "partial": state.partial,
                        "known": state.known,
                        "lastAt": state.last_at.isoformat() if state.last_at else None,
                        "lastResult": state.last_result,
                    }
                )
            banks.append(bank.summary() | {"cards": cards})
        material = []
        for path in material_files(self.vault, subject):
            text = read_text(path)
            material.append(
                {
                    "file": f"{subject.knowledge_folder}/{path.name}",
                    "title": path.stem,
                    "topic": str(front_matter(text).get("thema") or ""),
                    "sections": [h for h, _ in sections(text) if h],
                }
            )
        return subject.summary(now) | {
            "code": subject.code,
            "subjectFile": f"{subject.knowledge_folder}/{SUBJECT_FILE}",
            "banks": banks,
            "material": material,
            "progress": self._readiness(subject, now),
        }

    # -- sessions ---------------------------------------------------------------------

    def start_session(self, name: str, count: int = 15, topic: str | None = None) -> dict:
        subject = self.subject(name)
        if not subject.cards:
            raise StudyError(f"'{subject.key}' has no question bank yet")
        count = max(1, min(int(count), MAX_SESSION))
        self.close_idle()
        now = self.clock()
        with self._lock:
            session = Session(uuid.uuid4().hex[:8], subject.key, count, topic or None, now)
            session.last_activity = now
            self.sessions[session.id] = session
        result = {
            "session_id": session.id,
            "subject": subject.summary(now),
            "progress": self._readiness(subject, now),
            "question": self._next(session, subject),
        }
        return result

    def next_question(self, session_id: str, skip: bool = False) -> dict:
        """The next card. A card handed out before must be answered (record) or skipped
        first; until then the same card is returned with ``pending``."""
        session = self._session(session_id)
        subject = self.subject(session.subject)
        with self._lock:
            pending = session.current
            if pending is not None and skip:
                session.skipped.append(pending)
                session.current = None
                pending = None
        question = self._next(session, subject)
        if pending is not None:
            question |= {"pending": True, "message": PENDING_HINT}
        return question

    def record(
        self, session_id: str, card_id: str, result: str, note: str = "", mode: str = "voice"
    ) -> dict:
        session = self._session(session_id)
        subject = self.subject(session.subject)
        card = self._card(subject, card_id)
        result = self._result(result)
        if result == "known" and not note.strip():
            note = KNOWN_NOTE
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
            if result not in PASSED and repeated <= 2:
                session.repeats.append((card.id, len(session.answers) + REPEAT_AFTER))
        return {"recorded": True, "card": card.id, "result": result} | self._counts(session)

    def correct(
        self,
        card_id: str,
        result: str,
        subject: str | None = None,
        session_id: str | None = None,
        note: str = "",
    ) -> dict:
        """Change the grade of the latest recorded answer to a card (the learner has the
        final word). Without an earlier answer only ``known`` is allowed, which is then
        recorded as a normal answer. Inside a session the session counts and repeats follow
        the new grade; without a session (web UI) the change is committed right away."""
        session = self._session(session_id) if session_id else None
        subj = self.subject(session.subject if session else (subject or ""))
        card = self._card(subj, card_id)
        result = self._result(result)
        if (
            session is not None
            and session.current == card.id
            and not any(a.card == card.id for a in session.answers)
        ):
            raise StudyError(
                "this card is not recorded yet: call record_answer for it "
                "(result=known if the learner says it is trivial)"
            )
        log = self._log(subj)
        # Effective answers: originals with earlier corrections applied (same ts).
        originals = sorted(
            resolve_corrections([a for a in log.read() if a.card == card.id]),
            key=lambda a: a.at,
        )
        previous: str | None = None
        if not originals:
            if result != "known":
                raise StudyError(f"'{card.id}' has no recorded answer yet; nothing to correct")
            if session is not None:
                return self.record(session.id, card.id, result, note) | {"corrected": True}
            log.append(
                Attempt(
                    ts=self.clock().isoformat(timespec="seconds"),
                    card=card.id,
                    result=result,
                    mode="web",
                    note=note.strip()[:500] or KNOWN_NOTE,
                )
            )
        else:
            original = originals[-1]
            previous = original.result
            if previous != result:
                log.append(
                    Attempt(
                        ts=self.clock().isoformat(timespec="seconds"),
                        card=card.id,
                        result=result,
                        mode=CORRECTION,
                        session=session.id if session else "",
                        note=(note.strip() or f"corrected by learner (was {previous})")[:500],
                        replaces=original.ts,
                    )
                )
                if session is not None:
                    self._apply_correction(session, card.id, original.ts, result)
        changed = previous != result
        if session is None and changed and self.committer is not None:
            verb = "marked known" if result == "known" else f"corrected to {result}"
            self.committer(
                [f"{subj.folder}/{PROGRESS_FILE}"],
                f"study: {subj.key} – {card.id} {verb}\n",
            )
        state = card_states([card.id], log.read())[card.id]
        out = {
            "corrected": changed,
            "card": card.id,
            "previous": previous,
            "result": result,
            "status": status(state),
            "box": state.box,
        }
        return out | (self._counts(session) if session is not None else {})

    def end_session(self, session_id: str, skip_pending: bool = False) -> dict:
        """End a session. Refuses while a handed-out card is neither answered nor skipped."""
        session = self._session(session_id)
        subject = self.subject(session.subject)
        with self._lock:
            pending = session.current
            if pending is not None and skip_pending:
                session.skipped.append(pending)
                session.current = None
                pending = None
        if pending is not None:
            card = subject.card(pending)
            return {
                "ended": False,
                "pending_card": {"id": pending, "title": card.title if card else pending},
                "message": (
                    "The answer to the last card is not saved. If the learner answered it, "
                    "call record_answer first, then end_session. If not, call end_session "
                    "with skip_pending=true."
                ),
            }
        return self._finish(session, subject)

    def close_idle(self) -> list[str]:
        """End sessions without activity for ``IDLE_END``, with the normal summary commit."""
        now = self.clock()
        with self._lock:
            idle = [
                s
                for s in self.sessions.values()
                if not s.ended and now - (s.last_activity or s.created) >= IDLE_END
            ]
        for session in idle:
            self._finish(session, self.subject(session.subject), automatic=True)
        return [s.id for s in idle]

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

    @staticmethod
    def _card(subject: Subject, card_id: str) -> Card:
        card = subject.card(card_id)
        if card is None:
            raise StudyError(f"unknown card '{card_id}' in {subject.key}")
        return card

    @staticmethod
    def _result(result: str) -> str:
        result = result.strip().lower()
        if result not in RESULTS:
            raise StudyError(f"result must be one of: {', '.join(RESULTS)}")
        return result

    @staticmethod
    def _counts(session: Session) -> dict:
        answered = len(session.answers)
        return {
            "answered": answered,
            "correct": sum(a.result == "correct" for a in session.answers),
            "known": sum(a.result == "known" for a in session.answers),
            "remaining": max(session.target - answered, 0) + len(session.repeats),
        }

    def _apply_correction(self, session: Session, card_id: str, ts: str, result: str) -> None:
        """Let the session counts and repeats follow a corrected grade."""
        with self._lock:
            session.corrections += 1
            for i, a in enumerate(session.answers):
                if a.card == card_id and a.ts == ts:
                    session.answers[i] = replace(a, result=result)
                    break
            else:
                return  # an answer from an earlier session: only the log changes
            queued = [r for r in session.repeats if r[0] == card_id]
            if result in PASSED:
                session.repeats = [r for r in session.repeats if r[0] != card_id]
            elif not queued and session.current != card_id:
                repeated = sum(1 for a in session.answers if a.card == card_id)
                if repeated <= 2:
                    session.repeats.append((card_id, len(session.answers) + REPEAT_AFTER))

    def _log(self, subject: Subject) -> ProgressLog:
        return ProgressLog(
            self.vault.resolve(f"{subject.folder}/{PROGRESS_FILE}"),
            self.vault.resolve(f"{STATE}/study/{subject.key}.lock"),
        )

    def _readiness(self, subject: Subject, now: datetime) -> dict:
        states = card_states([c.id for c in subject.cards], self._log(subject).read())
        return readiness(
            states,
            subject.cards,
            now,
            subject.next_exam(now),
        )

    def _session(self, session_id: str) -> Session:
        session = self.sessions.get(session_id.strip())
        if session is None or session.ended:
            raise StudyError("unknown or finished session; start a new one with start_session")
        session.last_activity = self.clock()
        return session

    def _finish(self, session: Session, subject: Subject, automatic: bool = False) -> dict:
        with self._lock:
            if session.ended:
                return self._summary(session, subject)
            session.ended = True
            self.sessions.pop(session.id, None)
        summary = self._summary(session, subject) | {"ended": True}
        if (session.answers or session.corrections) and self.committer is not None:
            self.committer(
                [f"{subject.folder}/{PROGRESS_FILE}"],
                self._commit_message(session, subject, automatic),
            )
        return summary

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
        question = {
            "done": False,
            "position": len(session.answers) + 1,
            "of": session.target,
            "repeat": session.asked.count(current) > 1,
            "seen_before": state.seen,
            "last_result": state.last_result,
            "card": card.as_dict(),
        }
        if session.answers:
            # The last saved answer (corrections applied), so "the last one" is unambiguous.
            last = session.answers[-1]
            last_card = subject.card(last.card)
            question["previous"] = {
                "card": last.card,
                "title": last_card.title if last_card else last.card,
                "result": last.result,
            }
        return question

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
        ranked = [(priority(states[c.id], now, c.exam), c.id) for c in cards]
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
            if a.result not in PASSED and card and card.title not in missed:
                missed.append(card.title)
        correct = sum(a.result == "correct" for a in answers)
        known = sum(a.result == "known" for a in answers)
        progress = self._readiness(subject, self.clock())
        known_text = f", {known} marked known" if known else ""
        return {
            "subject": subject.key,
            "answered": len(answers),
            "correct": correct,
            "known": known,
            "partial": sum(a.result == "partial" for a in answers),
            "wrong": sum(a.result == "wrong" for a in answers),
            "skipped": len(session.skipped),
            "corrections": session.corrections,
            "to_review": missed,
            "progress": progress,
            # Counted by the server from the saved answers; read it out word for word.
            "confirmation": (
                f"{plural(len(answers), 'answer')} saved, {correct} correct{known_text}. "
                f"Readiness {progress['readiness']} percent."
            ),
        }

    def _commit_message(self, session: Session, subject: Subject, automatic: bool) -> str:
        s = self._summary(session, subject)
        subject_line = (
            f"study: {subject.key} – {plural(s['answered'], 'answer')}, {s['correct']} correct"
            + (f", {s['known']} known" if s["known"] else "")
        )
        body = [
            f"Readiness: {s['progress']['readiness']}% "
            f"({s['progress']['secure']}/{s['progress']['cards']} cards secure)"
        ]
        if s["corrections"]:
            body.append(f"Grades corrected by the learner: {s['corrections']}")
        if s["to_review"]:
            body.append("To review:\n" + "\n".join(f"- {t}" for t in s["to_review"]))
        if automatic:
            minutes = int(IDLE_END.total_seconds() // 60)
            body.append(f"Session ended automatically after {minutes} minutes without activity.")
        return subject_line + "\n\n" + "\n\n".join(body) + "\n"
