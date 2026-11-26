from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from lifeos.connectors.moodle.client import MoodleError
from lifeos.connectors.moodle.sync import SyncReport
from lifeos.core.git_sync import GitSync
from lifeos.worker.jobs import moodle_commit_message, moodle_job
from lifeos.worker.scheduler import Daily, Every, Job, JobResult, Scheduler, parse_time

VIENNA = ZoneInfo("Europe/Vienna")
ID = ["-c", "user.name=Test", "-c", "user.email=test@example.org"]


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """A time in Vienna (winter, UTC+1), returned in UTC."""
    return datetime(2026, 11, day, hour, minute, tzinfo=VIENNA).astimezone(UTC)


class Counter:
    def __init__(self, *results: JobResult) -> None:
        self.calls = 0
        self.results = list(results) or [JobResult("ok")]

    def __call__(self) -> JobResult:
        self.calls += 1
        return self.results[min(self.calls - 1, len(self.results) - 1)]


def daily_at_five(run, clock: Clock, state_file: Path) -> Scheduler:
    job = Job("moodle-sync", Daily(parse_time("05:00"), VIENNA), run)
    return Scheduler([job], state_file, clock=clock)


# -- scheduler ------------------------------------------------------------------


def test_daily_job_runs_once_per_day_at_local_time(tmp_path: Path) -> None:
    clock, run = Clock(at(16, 4, 59)), Counter()
    sched = daily_at_five(run, clock, tmp_path / "jobs.json")

    assert sched.tick() == []  # 04:59 Vienna: not yet
    clock.advance(minutes=1)
    assert sched.tick() == ["moodle-sync"]  # 05:00 Vienna = 04:00 UTC
    clock.advance(hours=10)
    assert sched.tick() == []  # same day: done
    clock.now = at(17, 5, 0)
    assert sched.tick() == ["moodle-sync"]  # next day
    assert run.calls == 2


def test_daily_job_catches_up_after_downtime(tmp_path: Path) -> None:
    clock, run = Clock(at(16, 9, 30)), Counter()  # worker was down at 05:00
    sched = daily_at_five(run, clock, tmp_path / "jobs.json")
    assert sched.tick() == ["moodle-sync"]
    assert run.calls == 1


def test_restart_does_not_repeat_a_daily_job(tmp_path: Path) -> None:
    clock, run = Clock(at(16, 5, 0)), Counter()
    daily_at_five(run, clock, tmp_path / "jobs.json").tick()
    clock.advance(minutes=30)

    restarted = daily_at_five(run, clock, tmp_path / "jobs.json")

    assert restarted.tick() == []
    assert run.calls == 1


def test_failed_daily_job_retries_hourly_at_most_three_times(tmp_path: Path) -> None:
    clock, run = Clock(at(16, 5, 0)), Counter(JobResult("error", "Moodle down"))
    sched = daily_at_five(run, clock, tmp_path / "jobs.json")

    assert sched.tick() == ["moodle-sync"]
    clock.advance(minutes=30)
    assert sched.tick() == []  # retry only after an hour
    clock.advance(minutes=30)
    assert sched.tick() == ["moodle-sync"]
    clock.advance(hours=1)
    assert sched.tick() == ["moodle-sync"]
    clock.advance(hours=1)
    assert sched.tick() == []  # three attempts used
    assert sched.state["moodle-sync"]["status"] == "error"
    clock.now = at(17, 5, 0)
    assert sched.tick() == ["moodle-sync"]  # fresh attempts the next day
    assert sched.state["moodle-sync"]["attempts"] == 1


def test_retry_succeeds_and_stops(tmp_path: Path) -> None:
    clock = Clock(at(16, 5, 0))
    run = Counter(JobResult("error", "down"), JobResult("ok", "3 new"))
    sched = daily_at_five(run, clock, tmp_path / "jobs.json")
    sched.tick()
    clock.advance(hours=1)
    sched.tick()
    clock.advance(hours=1)
    assert sched.tick() == []
    assert sched.state["moodle-sync"]["status"] == "ok"
    assert sched.state["moodle-sync"]["message"] == "3 new"


def test_interval_job_and_crashing_job(tmp_path: Path) -> None:
    clock = Clock(at(16, 12, 0))
    run = Counter()

    def boom() -> JobResult:
        raise RuntimeError("bug")

    sched = Scheduler(
        [Job("boom", Every(60), boom), Job("git-sync", Every(300), run)],
        tmp_path / "jobs.json",
        clock=clock,
    )
    assert sched.tick() == ["boom", "git-sync"]
    assert sched.state["boom"]["status"] == "error"
    assert "bug" in sched.state["boom"]["message"]
    clock.advance(seconds=299)
    assert sched.tick() == ["boom"]
    clock.advance(seconds=1)
    assert sched.tick() == ["git-sync"]
    assert run.calls == 2


# -- moodle job -----------------------------------------------------------------


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *ID, *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def vault(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    root = tmp_path / "vault"
    git(tmp_path, "init", "-q", "-b", "main", str(root))
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.org")
    (root / "ready" / "knowledge").mkdir(parents=True)
    (root / "ready" / "knowledge" / "README.md").write_text("hub\n", encoding="utf-8")
    git(root, "add", "--all")
    git(root, "commit", "-q", "-m", "init")
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "-q", "-u", "origin", "main")
    return root, remote


class FakeMoodle:
    """Stands in for MoodleSync: writes notes like a real sync and returns a report."""

    target_dir = "Moodle"

    def __init__(self, root: Path, report: SyncReport | Exception, files: dict[str, str]):
        self.root, self.report, self.files = root, report, files

    def sync(self) -> SyncReport:
        if isinstance(self.report, Exception):
            raise self.report
        for rel, text in self.files.items():
            path = self.root / "ready" / "knowledge" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return self.report


def test_moodle_job_commits_its_folder_with_its_own_message(vault) -> None:
    root, remote = vault
    report = SyncReport(
        created=["Moodle/Course A/01 Slides/Unit 5.md"],
        updated=["Moodle/Course B/Syllabus.md"],
        unchanged=7,
    )
    files = {"Moodle/Course A/01 Slides/Unit 5.md": "new\n", "Moodle/Course B/Syllabus.md": "v2\n"}
    (root / "ready" / "knowledge" / "my-note.md").write_text("mine\n", encoding="utf-8")
    sync = GitSync(root)

    result = moodle_job(FakeMoodle(root, report, files), [], sync)()

    assert result.status == "ok"
    assert result.message.startswith("1 new, 1 updated, 0 removed, 7 unchanged")
    subjects = git(root, "log", "--format=%s").splitlines()
    # The Moodle commit holds only Moodle files; the user's note follows as auto-sync.
    assert subjects[:2] == ["lifeos: auto-sync (1 file(s))", "moodle: 1 new, 1 updated"]
    moodle_files = git(root, "show", "--name-only", "--format=", "HEAD~1").split("\n")[:-1]
    assert moodle_files == [
        "ready/knowledge/Moodle/Course A/01 Slides/Unit 5.md",
        "ready/knowledge/Moodle/Course B/Syllabus.md",
    ]
    body = git(root, "log", "-1", "--format=%b", "HEAD~1")
    assert "Courses: Course A, Course B" in body
    assert "- Moodle/Course A/01 Slides/Unit 5.md" in body
    assert git(remote, "log", "-1", "--format=%s").strip() == "lifeos: auto-sync (1 file(s))"


def test_moodle_job_without_changes_makes_no_commit(vault) -> None:
    root, _ = vault
    head = git(root, "rev-parse", "HEAD")
    result = moodle_job(FakeMoodle(root, SyncReport(unchanged=12), {}), [], GitSync(root))()
    assert result.status == "ok"
    assert git(root, "rev-parse", "HEAD") == head


def test_moodle_job_reports_errors_and_missing_setup(vault) -> None:
    root, _ = vault
    failing = FakeMoodle(root, MoodleError("token expired", 401), {})
    assert moodle_job(failing, [], GitSync(root))().status == "error"
    skipped = moodle_job(failing, ["LIFEOS_MOODLE_URL"], GitSync(root))()
    assert skipped.status == "skipped"


def test_commit_message_for_overview_only_changes() -> None:
    message = moodle_commit_message(SyncReport(unchanged=3), ["ready/knowledge/Moodle/A/A.md"])
    subject, body = message.split("\n\n", 1)
    assert subject == "moodle: course overviews updated"
    assert "- ready/knowledge/Moodle/A/A.md" in body


def test_every_job_is_saved_right_away_with_a_heartbeat(tmp_path: Path) -> None:
    import json

    clock = Clock(at(16, 12, 0))
    state = tmp_path / "jobs.json"
    seen: dict = {}

    def second() -> JobResult:
        # a restart here must not forget the first job, and the control room sees this run
        seen["jobs"] = json.loads(state.read_text(encoding="utf-8"))
        seen["beat"] = json.loads((tmp_path / "worker.json").read_text(encoding="utf-8"))
        return JobResult("ok")

    sched = Scheduler(
        [Job("first", Every(60), Counter()), Job("second", Every(60), second)], state, clock=clock
    )
    assert json.loads((tmp_path / "worker.json").read_text())["running"] is None
    assert sched.tick() == ["first", "second"]
    assert seen["jobs"]["first"]["status"] == "ok" and "second" not in seen["jobs"]
    assert seen["beat"]["running"]["job"] == "second"
    assert json.loads((tmp_path / "worker.json").read_text())["running"] is None


def test_unreadable_state_does_not_stop_the_worker(tmp_path: Path) -> None:
    state = tmp_path / "jobs.json"
    state.write_text('{"x": {"lastRun": "soon", "lastSuccess": 7}, "y": "oops"}', encoding="utf-8")
    run = Counter()
    sched = Scheduler(
        [Job("x", Every(60), run), Job("y", Every(60), run)], state, clock=Clock(at(16, 12, 0))
    )
    assert sched.tick() == ["x", "y"]
    assert run.calls == 2
