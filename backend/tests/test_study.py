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
from lifeos.modules.study.progress import Attempt, card_states
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
    assert p["topics"]["Vocabulary"] == {"cards": 3, "secure": 1, "seen": 2}


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


def test_rest_requires_login(anon) -> None:
    assert anon.get("/api/study/subjects").status_code == 401


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
