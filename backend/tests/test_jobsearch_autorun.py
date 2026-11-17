from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from lifeos.connectors.devjobs.client import DevjobsError
from lifeos.modules.jobsearch import autorun as autorun_module
from lifeos.modules.jobsearch.autorun import Choice, LocalState, autorun, single_instance
from lifeos.modules.jobsearch.autostart import task_xml
from lifeos.modules.jobsearch.importer import ImporterSettings, ImportResult, ServerError

SETTINGS = ImporterSettings(
    jobsearch_server_url="https://lifeos.example.org", jobsearch_import_token="t" * 40
)
NOON = datetime(2026, 11, 17, 12, 0)


class FakeNotifier:
    def __init__(self, choice: Choice | None = Choice.NOW) -> None:
        self.choice = choice
        self.asked = 0
        self.infos: list[tuple[str, str]] = []

    def ask(self, title: str, text: str) -> Choice | None:
        self.asked += 1
        return self.choice

    def info(self, title: str, text: str) -> None:
        self.infos.append((title, text))


class FakeServer:
    def __init__(self, done_today: bool = False, reachable: bool = True) -> None:
        self.done_today = done_today
        self.reachable = reachable
        self.closed = False

    def status(self) -> dict[str, Any]:
        if not self.reachable:
            raise ServerError("offline")
        return {"doneToday": self.done_today}

    def close(self) -> None:
        self.closed = True


class FakeDevjobs:
    def close(self) -> None:
        pass


def run(
    tmp_path: Path,
    notifier: FakeNotifier,
    server: FakeServer | None = None,
    now: datetime = NOON,
    settings: ImporterSettings = SETTINGS,
) -> str:
    server = server or FakeServer()
    return autorun(
        settings,
        notifier,
        tmp_path,
        now=lambda: now,
        make_server=lambda url, token: server,  # type: ignore[arg-type,return-value]
        make_devjobs=FakeDevjobs,  # type: ignore[arg-type]
    )


@pytest.fixture
def imports(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []

    def fake_import(server, devjobs, limit=None, progress=None) -> ImportResult:
        calls.append(1)
        return ImportResult(listed=10, new=3, pending=3)

    monkeypatch.setattr(autorun_module, "run_import", fake_import)
    return calls


def test_asks_and_imports_when_not_done_today(tmp_path: Path, imports: list[int]) -> None:
    notifier = FakeNotifier(Choice.NOW)
    assert run(tmp_path, notifier) == "imported"
    assert imports == [1] and notifier.asked == 1
    assert "3 new posting(s) imported" in notifier.infos[-1][1]


def test_stays_quiet_when_server_has_todays_import(tmp_path: Path, imports: list[int]) -> None:
    notifier = FakeNotifier()
    assert run(tmp_path, notifier, FakeServer(done_today=True)) == "done-today"
    assert notifier.asked == 0 and imports == []


def test_stays_quiet_when_offline(tmp_path: Path, imports: list[int]) -> None:
    notifier = FakeNotifier()
    assert run(tmp_path, notifier, FakeServer(reachable=False)) == "offline"
    assert notifier.asked == 0 and not notifier.infos


def test_later_snoozes_for_an_hour(tmp_path: Path, imports: list[int]) -> None:
    notifier = FakeNotifier(Choice.LATER)
    assert run(tmp_path, notifier) == "later"
    assert run(tmp_path, notifier, now=NOON + timedelta(minutes=30)) == "snoozed"
    notifier.choice = Choice.NOW
    assert run(tmp_path, notifier, now=NOON + timedelta(minutes=61)) == "imported"
    assert notifier.asked == 2


def test_dismissed_notification_counts_as_later(tmp_path: Path, imports: list[int]) -> None:
    assert run(tmp_path, FakeNotifier(None)) == "later"
    assert LocalState(tmp_path / "state.json").load()["snoozeUntil"]


def test_not_today_lasts_until_tomorrow(tmp_path: Path, imports: list[int]) -> None:
    notifier = FakeNotifier(Choice.SKIP)
    assert run(tmp_path, notifier) == "skipped"
    assert run(tmp_path, notifier, now=NOON + timedelta(hours=8)) == "skipped-today"
    notifier.choice = Choice.NOW
    assert run(tmp_path, notifier, now=NOON + timedelta(days=1)) == "imported"


def test_refusal_by_devjobs_stops_for_the_day(tmp_path: Path, monkeypatch) -> None:
    def refused(*args, **kwargs):
        raise DevjobsError("HTTP 403", 403)

    monkeypatch.setattr(autorun_module, "run_import", refused)
    notifier = FakeNotifier(Choice.NOW)
    assert run(tmp_path, notifier) == "blocked"
    assert notifier.infos[-1][0] == "Job search stopped"
    assert LocalState(tmp_path / "state.json").load()["skippedOn"] == date(2026, 11, 17).isoformat()


def test_network_trouble_retries_in_an_hour(tmp_path: Path, monkeypatch) -> None:
    def broken(*args, **kwargs):
        raise ServerError("cannot reach the LifeOS server")

    monkeypatch.setattr(autorun_module, "run_import", broken)
    assert run(tmp_path, FakeNotifier(Choice.NOW)) == "failed"
    assert LocalState(tmp_path / "state.json").load()["snoozeUntil"]


def test_not_configured(tmp_path: Path) -> None:
    assert run(tmp_path, FakeNotifier(), settings=ImporterSettings()) == "not-configured"


def test_second_instance_does_not_run(tmp_path: Path, imports: list[int]) -> None:
    with single_instance(tmp_path / "autorun.lock") as mine:
        assert mine
        assert run(tmp_path, FakeNotifier()) == "already-running"
    assert run(tmp_path, FakeNotifier()) == "imported"


def test_task_xml_has_the_three_triggers_and_no_admin() -> None:
    xml = task_xml(
        Path("C:/app/.venv/Scripts/pythonw.exe"),
        Path("C:/deploy/.env"),
        Path("C:/app/backend"),
        "PC\\someone",
        date(2026, 11, 17),
    )
    assert "<LogonTrigger>" in xml and "<EventTrigger>" in xml
    assert "<Interval>PT1H</Interval>" in xml
    assert "Power-Troubleshooter" in xml and "LeastPrivilege" in xml
    assert f'jobsearch autorun --env-file "{Path("C:/deploy/.env")}"' in xml
    assert "pythonw.exe" in xml and "<MultipleInstancesPolicy>IgnoreNew" in xml
