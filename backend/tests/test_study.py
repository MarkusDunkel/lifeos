from __future__ import annotations

import json
import random
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lifeos.core.config import Settings
from lifeos.core.git_sync import GitSync
from lifeos.core.vault import Vault
from lifeos.main import create_app
from lifeos.modules.study.bank import parse_cards
from lifeos.modules.study.progress import Attempt, card_states, priority
from lifeos.modules.study.service import StudyError, StudyService
from lifeos.worker.jobs import commit_study_leftovers

EXAM = "2026-11-21T12:30"

SUBJECT = f"""---
fach: Sample Language Course
kürzel: SL
prüfung: {EXAM}
prüfungsformat: vocab test
sprache: en
---

# Sample Language Course
"""

VOCAB = """---
thema: Vocabulary
typ: prüfen
---

# Vocabulary – Prüfen

**SL-V-01** – alpha
- Definition: the first letter
- Lückensatz: … comes before beta.
- Muss-Punkte: alpha

**SL-V-02** – beta
- Definition: the second letter
- Muss-Punkte: beta

## Block B

**SL-V-03** – gamma
- Definition: the third letter
- Muss-Punkte: gamma
"""

PHRASES = """---
thema: Phrases
---

**SL-P-01** – How about …?
- Funktion: to suggest an idea
- Gruppe: Suggesting

**SL-P-02** – May I jump in here?
- Funktion: to interrupt politely
- Gruppe: Interrupting
"""

TUTORIAL = """---
typ: tutorial
---

# Sample

## 1. Letters
alpha, beta, gamma.

## 2. Interrupting
- May I jump in here?
"""


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    folder = root / "ready" / "knowledge" / "Studium" / "Sample"
    folder.mkdir(parents=True)
    (folder / "_Fach.md").write_text(SUBJECT, encoding="utf-8")
    (folder / "Vocabulary - Prüfen.md").write_text(VOCAB, encoding="utf-8")
    (folder / "Phrases - Prüfen.md").write_text(PHRASES, encoding="utf-8")
    (folder / "Sample.md").write_text(TUTORIAL, encoding="utf-8")
    return root


def service(root: Path, clock: Clock, committed: list | None = None) -> StudyService:
    committer = (
        (lambda paths, msg: committed.append((paths, msg))) if committed is not None else None
    )
    return StudyService(
        Vault.open(root), "Europe/Vienna", committer=committer, clock=clock, rng=random.Random(1)
    )


def log_lines(root: Path) -> list[dict]:
    path = root / "ready/knowledge/Studium/Sample/lernstand.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# -- bank -------------------------------------------------------------------------


def test_parse_cards_reads_ids_titles_and_fields() -> None:
    cards = parse_cards(VOCAB, "Vocabulary")
    assert [c.id for c in cards] == ["SL-V-01", "SL-V-02", "SL-V-03"]
    assert cards[0].title == "alpha"
    assert cards[0].fields["Lückensatz"] == "… comes before beta."
    assert cards[2].fields == {"Definition": "the third letter", "Muss-Punkte": "gamma"}


def test_wrapped_titles_and_field_values() -> None:
    text = """**SL-Q-01** – What is the difference between a short and
a long question?
- Muss-Punkte: length · wrapping
  over two lines
- Musterantwort: one line

**SL-Q-02** – Short?
- Muss-Punkte: yes
"""
    first, second = parse_cards(text, "Questions")
    assert first.title == "What is the difference between a short and a long question?"
    assert first.fields["Muss-Punkte"] == "length · wrapping over two lines"
    assert first.fields["Musterantwort"] == "one line"
    assert second.title == "Short?"


def test_subjects_with_exam_and_topics(vault_root: Path) -> None:
    study = service(vault_root, Clock(datetime(2026, 11, 16, 12, tzinfo=UTC)))
    (subject,) = study.subjects()
    assert subject.key == "Sample"
    assert subject.name == "Sample Language Course"
    assert subject.exam == datetime(2026, 11, 21, 11, 30, tzinfo=UTC)
    assert len(subject.cards) == 5
    assert study.subject("sample language").key == "Sample"
    assert study.subject("SL").key == "Sample"
    with pytest.raises(StudyError):
        study.subject("history")


def own_exam(root: Path, when: str = "2026-12-15T09:00") -> None:
    """Give the phrases bank its own exam date."""
    path = root / "ready/knowledge/Studium/Sample/Phrases - Prüfen.md"
    text = path.read_text(encoding="utf-8")
    extra = f"thema: Phrases\nprüfung: {when}\nprüfungsformat: oral\n"
    path.write_text(text.replace("thema: Phrases\n", extra), encoding="utf-8")


def test_bank_exam_overrides_the_subject_exam(vault_root: Path) -> None:
    own_exam(vault_root)
    now = datetime(2026, 11, 16, 12, tzinfo=UTC)
    study = service(vault_root, Clock(now))
    (subject,) = study.subjects()
    banks = {b.topic: b for b in subject.banks}
    vocab, phrases = banks["Vocabulary"], banks["Phrases"]
    assert vocab.exam == datetime(2026, 11, 21, 11, 30, tzinfo=UTC)
    assert not vocab.own_exam
    assert phrases.exam == datetime(2026, 12, 15, 8, tzinfo=UTC)
    assert phrases.own_exam
    assert phrases.exam_format == "oral"
    assert subject.card("SL-P-01").exam == phrases.exam
    exams = subject.exams(now)
    assert [(e["topics"], e["format"]) for e in exams] == [
        (["Vocabulary"], "vocab test"),
        (["Phrases"], "oral"),
    ]
    assert subject.next_exam(now) == vocab.exam
    # After the first exam the next one is the phrases exam.
    later = datetime(2026, 11, 22, tzinfo=UTC)
    assert subject.next_exam(later) == phrases.exam
    assert subject.exams(later)[0]["past"] is True


def test_readiness_per_topic_uses_the_bank_exam(vault_root: Path) -> None:
    own_exam(vault_root)
    study = service(vault_root, Clock(datetime(2026, 11, 22, 12, tzinfo=UTC)))
    p = study.progress("Sample")
    assert p["nextExam"] == "2026-12-15T09:00:00+01:00"
    assert p["exam"] == "2026-12-15T09:00:00+01:00"
    assert p["topics"]["Vocabulary"]["minutesPerDay"] is None  # exam is over
    assert p["topics"]["Phrases"]["examInDays"] == pytest.approx(22.9, abs=0.1)
    assert p["minutesPerDay"] == p["topics"]["Phrases"]["minutesPerDay"] == 1


def test_interval_cap_uses_the_bank_exam(vault_root: Path) -> None:
    own_exam(vault_root, "2026-11-17T12:00")
    t = datetime(2026, 11, 16, 12, tzinfo=UTC)
    study = service(vault_root, Clock(t))
    (subject,) = study.subjects()
    state = card_states(["SL-P-01"], [attempt("SL-P-01", "correct", t)])["SL-P-01"]
    state.box = 3
    # The phrases exam is ~23 h away: due after half of that, not after 2 days.
    later = t + timedelta(hours=12)
    assert priority(state, later, subject.card("SL-P-01").exam) is not None
    assert priority(state, later, subject.card("SL-V-01").exam) is None


def test_pick_prefers_due_cards_of_the_sooner_exam(vault_root: Path) -> None:
    own_exam(vault_root, "2026-11-17T12:00")
    t = datetime(2026, 11, 16, 12, tzinfo=UTC)
    log = vault_root / "ready/knowledge/Studium/Sample/lernstand.jsonl"
    lines = [attempt(cid, "correct", t - timedelta(days=1)) for cid in ("SL-V-01", "SL-P-01")]
    lines += [attempt(cid, "correct", t) for cid in ("SL-V-01", "SL-P-01")]
    log.write_text("".join(json.dumps(a.__dict__) + "\n" for a in lines), encoding="utf-8")
    # The three new cards come first; then the only due card is SL-P-01 (its exam is
    # close, so its interval is capped), SL-V-01 waits for its normal interval.
    study = service(vault_root, Clock(t + timedelta(hours=12)))
    sid = study.start_session("Sample", count=4)["session_id"]
    asked = []
    for _ in range(4):
        q = study.next_question(sid)
        if q["done"]:
            break
        asked.append(q["card"]["id"])
        study.record(sid, q["card"]["id"], "correct")
    assert asked[3] == "SL-P-01"


# -- repetition -------------------------------------------------------------------


def attempt(card: str, result: str, at: datetime) -> Attempt:
    return Attempt(ts=at.isoformat(), card=card, result=result)


def test_boxes_move_up_on_correct_and_drop_on_wrong() -> None:
    t = datetime(2026, 11, 16, tzinfo=UTC)
    states = card_states(
        ["a"],
        [attempt("a", "correct", t), attempt("a", "correct", t + timedelta(hours=5))],
    )
    assert states["a"].box == 2
    assert states["a"].secure
    states = card_states(
        ["a"],
        [
            attempt("a", "correct", t),
            attempt("a", "partial", t + timedelta(hours=1)),
            attempt("a", "wrong", t + timedelta(hours=2)),
        ],
    )
    assert states["a"].box == 0
    assert not states["a"].secure


def test_interval_is_capped_to_half_the_time_before_the_exam() -> None:
    t = datetime(2026, 11, 20, 12, tzinfo=UTC)
    exam = t + timedelta(hours=10)
    states = card_states(["a"], [attempt("a", "correct", t)] * 1)
    states["a"].box = 3  # normally due after 2 days
    assert states["a"].due_at(exam) == t + timedelta(hours=5)
    assert states["a"].due_at(None) == t + timedelta(days=2)


# -- sessions ---------------------------------------------------------------------


def test_session_asks_new_cards_records_and_repeats_misses(vault_root: Path) -> None:
    clock = Clock(datetime(2026, 11, 16, 12, tzinfo=UTC))
    committed: list = []
    study = service(vault_root, clock, committed)

    started = study.start_session("Sample", count=5)
    sid = started["session_id"]
    asked = []
    question = started["question"]
    first = question["card"]["id"]
    # Asking again without an answer returns the same card (voice retries).
    assert study.next_question(sid)["card"]["id"] == first
    for _ in range(5):
        card = question["card"]["id"]
        asked.append(card)
        result = "wrong" if card == first else "correct"
        study.record(sid, card, result, note="mixed up" if result == "wrong" else "")
        clock.now += timedelta(seconds=30)
        question = study.next_question(sid)
        if question["done"]:
            break
    assert sorted(asked) == sorted({c for c in asked})  # five different cards
    # The missed card comes back after REPEAT_AFTER other questions.
    assert question["done"] is False
    assert question["card"]["id"] == first
    assert question["repeat"] is True
    study.record(sid, first, "correct")
    assert study.next_question(sid)["done"] is True

    summary = study.end_session(sid)
    assert summary["answered"] == 6
    assert summary["wrong"] == 1
    assert len(log_lines(vault_root)) == 6
    ((paths, message),) = committed
    assert paths == ["ready/knowledge/Studium/Sample/lernstand.jsonl"]
    assert message.startswith("study: Sample – 6 answers, 5 correct")
    assert "To review:" in message
    with pytest.raises(StudyError):
        study.next_question(sid)


def test_next_session_starts_with_missed_cards(vault_root: Path) -> None:
    clock = Clock(datetime(2026, 11, 16, 12, tzinfo=UTC))
    study = service(vault_root, clock)
    sid = study.start_session("Sample", count=5)["session_id"]
    for _ in range(5):
        q = study.next_question(sid)
        if q["done"]:
            break
        card = q["card"]["id"]
        study.record(sid, card, "partial" if card == "SL-V-02" else "correct")
    study.end_session(sid)

    clock.now += timedelta(minutes=10)
    again = study.start_session("Sample", count=3)
    assert again["question"]["card"]["id"] == "SL-V-02"
    assert again["question"]["last_result"] == "partial"


def test_topic_filter_and_invalid_input(vault_root: Path) -> None:
    study = service(vault_root, Clock(datetime(2026, 11, 16, 12, tzinfo=UTC)))
    started = study.start_session("Sample", count=5, topic="interrupting")
    assert started["question"]["card"]["id"] == "SL-P-02"
    sid = started["session_id"]
    with pytest.raises(StudyError):
        study.record(sid, "SL-P-02", "great")
    with pytest.raises(StudyError):
        study.record(sid, "XX-1", "correct")


def test_readiness(vault_root: Path) -> None:
    clock = Clock(datetime(2026, 11, 16, 12, tzinfo=UTC))
    study = service(vault_root, clock)
    log = vault_root / "ready/knowledge/Studium/Sample/lernstand.jsonl"
    t0 = clock.now - timedelta(days=2)
    lines = [
        attempt("SL-V-01", "correct", t0),
        attempt("SL-V-01", "correct", t0 + timedelta(days=1)),
        attempt("SL-V-02", "wrong", t0),
    ]
    log.write_text("".join(json.dumps(a.__dict__) + "\n" for a in lines), encoding="utf-8")

    p = study.progress("Sample")

    assert (p["cards"], p["seen"], p["secure"], p["new"]) == (5, 2, 1, 3)
    assert p["readiness"] == 20
    assert p["weak"][0]["id"] == "SL-V-02"
    assert p["examInDays"] == pytest.approx(5.0, abs=0.1)
    assert p["minutesNeeded"] == 3  # 2+2+2+2 correct answers needed × 20 s
    vocab = p["topics"]["Vocabulary"]
    assert (vocab["cards"], vocab["secure"], vocab["seen"]) == (3, 1, 2)
    assert vocab["exam"] == "2026-11-21T12:30:00+01:00"
    assert vocab["minutesPerDay"] == 1  # 4 answers × 20 s over 5 days, rounded up
    assert p["nextExam"] == "2026-11-21T12:30:00+01:00"


def test_material_sections_and_topic(vault_root: Path) -> None:
    study = service(vault_root, Clock(datetime(2026, 11, 16, tzinfo=UTC)))
    listing = study.material("Sample")
    assert "Sample › 2. Interrupting" in listing["sections"]
    assert "jump in" in study.material("Sample", "interrupting")["text"]
    with pytest.raises(StudyError):
        study.material("Sample", "astronomy")


# -- REST and git -----------------------------------------------------------------


def test_rest_progress(vault_root: Path) -> None:
    settings = Settings(vault_root=vault_root, session_secret="x" * 40, auth_disabled=True)
    client = TestClient(create_app(settings))
    subjects = client.get("/api/study/subjects").json()
    assert subjects[0]["key"] == "Sample"
    assert subjects[0]["progress"]["cards"] == 5
    assert client.get("/api/study/Sample/progress").json()["new"] == 5
    assert client.get("/api/study/nothing/progress").status_code == 404


def test_rest_detail_with_card_status_and_material(vault_root: Path) -> None:
    log = vault_root / "ready/knowledge/Studium/Sample/lernstand.jsonl"
    t = datetime(2026, 11, 15, tzinfo=UTC)
    lines = [
        attempt("SL-V-01", "correct", t),
        attempt("SL-V-01", "correct", t + timedelta(hours=5)),
        attempt("SL-V-02", "wrong", t),
        attempt("SL-P-01", "correct", t),
    ]
    log.write_text("".join(json.dumps(a.__dict__) + "\n" for a in lines), encoding="utf-8")
    settings = Settings(vault_root=vault_root, session_secret="x" * 40, auth_disabled=True)
    client = TestClient(create_app(settings))

    d = client.get("/api/study/subjects/Sample").json()

    assert d["subjectFile"] == "Studium/Sample/_Fach.md"
    assert d["exams"][0]["topics"] == ["Phrases", "Vocabulary"]
    banks = {b["topic"]: b for b in d["banks"]}
    assert banks["Vocabulary"]["file"] == "Studium/Sample/Vocabulary - Prüfen.md"
    status = {c["id"]: c["status"] for b in d["banks"] for c in b["cards"]}
    assert status == {
        "SL-P-01": "learning",
        "SL-P-02": "new",
        "SL-V-01": "secure",
        "SL-V-02": "wrong",
        "SL-V-03": "new",
    }
    first = banks["Vocabulary"]["cards"][0]
    assert first["fields"]["Lückensatz"] == "… comes before beta."
    assert first["lastAt"].startswith("2026-11-15T05:00")
    (material,) = d["material"]
    assert material["file"] == "Studium/Sample/Sample.md"
    assert material["sections"] == ["1. Letters", "2. Interrupting"]
    assert d["progress"]["secure"] == 1
    # The knowledge API reads and writes the same paths.
    doc = client.get("/api/knowledge/file", params={"path": material["file"]}).json()
    assert "## 1. Letters" in doc["content"]
    assert client.get("/api/study/subjects/nothing").status_code == 404


def test_rest_requires_login(anon) -> None:
    assert anon.get("/api/study/subjects").status_code == 401
    assert anon.get("/api/study/subjects/Sample").status_code == 401


def git(cwd: Path, *args: str) -> str:
    ident = ["-c", "user.name=Test", "-c", "user.email=test@example.org"]
    return subprocess.run(
        ["git", *ident, *args], cwd=cwd, capture_output=True, encoding="utf-8", check=True
    ).stdout


def test_worker_commits_unfinished_sessions_as_study(vault_root: Path) -> None:
    git(vault_root, "init", "-q", "-b", "main")
    git(vault_root, "add", "--all")
    git(vault_root, "commit", "-q", "-m", "init")
    study = service(vault_root, Clock(datetime(2026, 11, 16, 12, tzinfo=UTC)))
    sid = study.start_session("Sample", count=2)["session_id"]
    for _ in range(2):
        q = study.next_question(sid)
        study.record(sid, q["card"]["id"], "correct")
    (vault_root / "ready" / "knowledge" / "other.md").write_text("x\n", encoding="utf-8")

    committed = commit_study_leftovers(GitSync(vault_root))

    assert committed == ["ready/knowledge/Studium/Sample/lernstand.jsonl"]
    assert git(vault_root, "log", "-1", "--format=%s").strip() == (
        "study: Sample – 2 answers (session not ended)"
    )
    assert "other.md" in git(vault_root, "status", "--porcelain")
    assert commit_study_leftovers(GitSync(vault_root)) == []


# -- saving guarantees ------------------------------------------------------------


def test_no_new_card_before_the_answer_is_saved(vault_root: Path) -> None:
    study = service(vault_root, Clock(datetime(2026, 11, 16, 12, tzinfo=UTC)))
    started = study.start_session("Sample", count=3)
    sid, first = started["session_id"], started["question"]["card"]["id"]

    again = study.next_question(sid)

    assert again["card"]["id"] == first
    assert again["pending"] is True
    assert "record_answer" in again["message"]
    study.record(sid, first, "correct")
    following = study.next_question(sid)
    assert following["card"]["id"] != first
    assert "pending" not in following


def test_skip_moves_on_without_grading(vault_root: Path) -> None:
    study = service(vault_root, Clock(datetime(2026, 11, 16, 12, tzinfo=UTC)))
    started = study.start_session("Sample", count=3)
    sid, first = started["session_id"], started["question"]["card"]["id"]

    skipped = study.next_question(sid, skip=True)

    assert skipped["card"]["id"] != first
    assert "pending" not in skipped
    log = vault_root / "ready/knowledge/Studium/Sample/lernstand.jsonl"
    assert not log.exists()
    study.record(sid, skipped["card"]["id"], "correct")
    summary = study.end_session(sid)
    assert summary["skipped"] == 1
    assert summary["answered"] == 1


def test_end_session_refuses_while_the_last_card_is_unsaved(vault_root: Path) -> None:
    committed: list = []
    study = service(vault_root, Clock(datetime(2026, 11, 16, 12, tzinfo=UTC)), committed)
    started = study.start_session("Sample", count=3)
    sid, first = started["session_id"], started["question"]["card"]["id"]
    study.record(sid, first, "correct")
    last = study.next_question(sid)["card"]["id"]

    refused = study.end_session(sid)

    assert refused["ended"] is False
    assert refused["pending_card"]["id"] == last
    assert committed == []
    study.record(sid, last, "wrong")
    summary = study.end_session(sid)
    assert summary["ended"] is True
    assert summary["confirmation"].startswith("2 answers saved, 1 correct.")
    assert len(log_lines(vault_root)) == 2
    assert len(committed) == 1


def test_end_session_can_drop_an_unanswered_last_card(vault_root: Path) -> None:
    committed: list = []
    study = service(vault_root, Clock(datetime(2026, 11, 16, 12, tzinfo=UTC)), committed)
    started = study.start_session("Sample", count=3)
    sid = started["session_id"]
    study.record(sid, started["question"]["card"]["id"], "correct")
    study.next_question(sid)

    summary = study.end_session(sid, skip_pending=True)

    assert summary["ended"] is True
    assert summary["confirmation"].startswith("1 answer saved, 1 correct.")
    ((_, message),) = committed
    assert message.startswith("study: Sample – 1 answer, 1 correct")


def test_idle_sessions_are_ended_and_committed(vault_root: Path) -> None:
    clock = Clock(datetime(2026, 11, 16, 12, tzinfo=UTC))
    committed: list = []
    study = service(vault_root, clock, committed)
    started = study.start_session("Sample", count=5)
    sid = started["session_id"]
    study.record(sid, started["question"]["card"]["id"], "partial")
    empty = study.start_session("Sample", count=5)["session_id"]  # nothing answered

    clock.now += timedelta(minutes=29)
    assert study.close_idle() == []
    clock.now += timedelta(minutes=1)
    assert sorted(study.close_idle()) == sorted([sid, empty])

    ((paths, message),) = committed  # only the session with answers is committed
    assert message.startswith("study: Sample – 1 answer, 0 correct")
    assert "ended automatically after 30 minutes" in message
    with pytest.raises(StudyError):
        study.next_question(sid)


def test_activity_keeps_a_session_open(vault_root: Path) -> None:
    clock = Clock(datetime(2026, 11, 16, 12, tzinfo=UTC))
    study = service(vault_root, clock)
    started = study.start_session("Sample", count=5)
    sid = started["session_id"]
    clock.now += timedelta(minutes=25)
    study.record(sid, started["question"]["card"]["id"], "correct")
    clock.now += timedelta(minutes=25)
    assert study.close_idle() == []
