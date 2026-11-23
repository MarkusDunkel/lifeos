from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from test_calendar import FakeCalendar

from lifeos.agents.llm import LlmSettings, Usage
from lifeos.connectors.google.sync import CalendarSync
from lifeos.core.vault import Vault
from lifeos.modules.planning import goals as g
from lifeos.modules.planning.journal import Entry, Journal
from lifeos.modules.planning.plan import (
    DONE,
    PLAN_FILE,
    REMOVED,
    SKIPPED,
    Block,
    Busy,
    free_windows,
    validate,
)
from lifeos.modules.planning.planner import (
    AGENT,
    QUEUE_FILE,
    PlannedBlock,
    Planner,
    PlanResult,
    WishAnswer,
)

TZ = ZoneInfo("Europe/Vienna")
# Monday 2026-11-23, 19:00 in Vienna
NOW = datetime(2026, 11, 23, 19, 0, tzinfo=TZ).timestamp()

GOALS = """# Ziele

Einleitung bleibt.

## Kontingente

| Ziel | Kategorie | Menge | Einheit | Priorität | Notiz |
|---|---|---|---|---|---|
| Programmieren | programming | 8 | h/Woche | hoch | LifeOS |
| Uni | uni | auto | h/Woche | hoch | |
| Sport | sport | 6 | x/Woche | mittel | 45 min |

## Regeln

- Arbeit 8–17 Uhr
- Mittagspause 12–13

## Sonstiges

Bleibt auch.
"""

AGENT_FILE = f"""---
name: {AGENT}
title: Tagesplaner
runner: job
daily_limit: 3
---

## Regeln

1. Be kind.
"""


class FakeLlm:
    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = outputs
        self.prompts: list[str] = []

    def research(self, *a, **k):  # pragma: no cover - not used
        raise AssertionError

    def parse(self, system, prompt, schema, effort, images=None):
        self.prompts.append(prompt)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out, Usage(0, 0, 1000)


def block(day: str, start: str, end: str, title: str, goal: str = "Programmieren", **kw):
    return PlannedBlock(
        date=day, start=start, end=end, goal=goal, category="", title=title, reason="r", **kw
    )


@pytest.fixture
def vault(vault_root: Path) -> Vault:
    kb = vault_root / "ready" / "knowledge" / "Planning"
    kb.mkdir(parents=True)
    (kb / "Goals.md").write_text(GOALS, encoding="utf-8")
    (vault_root / "agents").mkdir()
    (vault_root / "agents" / f"{AGENT}.md").write_text(AGENT_FILE, encoding="utf-8")
    return Vault.open(vault_root)


@pytest.fixture
def fake() -> FakeCalendar:
    f = FakeCalendar()
    f.add("primary", "Dentist", "2026-11-24T09:00:00+01:00", "2026-11-24T10:00:00+01:00")
    return f


@pytest.fixture
def planner(vault: Vault, fake: FakeCalendar) -> Planner:
    clock = lambda: NOW  # noqa: E731
    cal = CalendarSync(vault, lambda: fake, "Europe/Vienna", clock=clock)
    cal.set_inputs([{"id": "primary", "label": "Privat"}])
    study = [
        {"name": "BWL", "progress": {"nextExam": "2026-12-01T08:00", "examInDays": 7.5,
                                      "minutesPerDay": 30, "readiness": 40}},
    ]  # fmt: skip
    return Planner(vault, "Europe/Vienna", cal, lambda: study, clock=clock)


def plan_events(fake: FakeCalendar, planner: Planner) -> list[dict[str, Any]]:
    target = planner.calendar.config()["target"]
    return [
        e
        for e in fake.items[target].values()
        if e.get("extendedProperties", {}).get("private", {}).get("lifeos_kind") == "plan"
    ]


# -- goals ----------------------------------------------------------------------------------


def test_goals_parse_and_render_roundtrip():
    goals, rules = g.parse(GOALS)
    assert [(x.name, x.amount, x.unit) for x in goals] == [
        ("Programmieren", 8.0, "hours"),
        ("Uni", None, "hours"),
        ("Sport", 6.0, "sessions"),
    ]
    assert "Mittagspause" in rules
    goals[0].amount = 10
    text = g.render(GOALS, goals, "- Nur noch eine Regel")
    assert "Einleitung bleibt." in text and "Bleibt auch." in text
    again, rules2 = g.parse(text)
    assert again[0].amount == 10 and again[1].auto and rules2 == "- Nur noch eine Regel"


def test_render_into_empty_file_uses_template():
    text = g.render("", [g.Goal("Lesen", 2, g.HOURS)], "- abends")
    goals, rules = g.parse(text)
    assert goals[0].name == "Lesen" and rules == "- abends"


def work_goals() -> list[g.Goal]:
    return [
        g.Goal("Sport", 6, g.SESSIONS),
        g.Goal("LifeOS", 20, g.PERCENT, group="Arbeit"),
        g.Goal("Arbeit", 30, g.HOURS),
        g.Goal("Uni", None, g.HOURS, group="Arbeit"),
        g.Goal("Bewerben", 60, g.PERCENT, group="Arbeit"),
        g.Goal("Portfolio", 20, g.PERCENT, group="Arbeit"),
    ]


def test_groups_split_percentages_of_what_fixed_members_leave():
    rows = {r["name"]: r for r in g.resolve(work_goals(), auto_hours=6)}
    assert rows["Arbeit"]["isGroup"] and rows["Arbeit"]["target"] == 30
    assert rows["Uni"]["target"] == 6  # auto
    # 24 h left for 20 / 60 / 20 %
    assert (rows["LifeOS"]["target"], rows["Bewerben"]["target"]) == (4.8, 14.4)
    assert rows["Arbeit"]["warnings"] == [] and rows["Sport"]["targetUnit"] == "sessions"
    order = [r["name"] for r in g.resolve(work_goals(), 6)]
    assert order == ["Sport", "Arbeit", "LifeOS", "Uni", "Bewerben", "Portfolio"]


def test_group_warnings_and_scaling():
    goals = work_goals()
    goals[4].amount = 100  # 140 %
    rows = {r["name"]: r for r in g.resolve(goals, auto_hours=40)}
    assert any("more than 30 h" in w for w in rows["Arbeit"]["warnings"])
    assert any("scaled down" in w for w in rows["Arbeit"]["warnings"])
    assert rows["LifeOS"]["target"] == 0  # nothing left after 40 h of study
    goals[4].amount = 10
    rows = {r["name"]: r for r in g.resolve(goals, auto_hours=0)}
    assert any("only 50 %" in w for w in rows["Arbeit"]["warnings"])


def test_groups_roundtrip_through_markdown():
    text = g.render(GOALS, work_goals(), "- r")
    assert "| Ziel | Gruppe | Menge | Einheit |" in text and "| LifeOS | Arbeit | 20 | % |" in text
    assert text.index("| Arbeit |") < text.index("| LifeOS |")  # members under their group
    again, _ = g.parse(text)
    assert {x.name: (x.group, x.unit) for x in again}["Uni"] == ("Arbeit", "hours")


def test_auto_study_hours_from_upcoming_exams():
    hours, exams = g.auto_study_hours(
        [
            {"name": "A", "progress": {"nextExam": "x", "examInDays": 5, "minutesPerDay": 40}},
            {"name": "B", "progress": {"nextExam": "y", "examInDays": -1, "minutesPerDay": 90}},
            {"name": "C", "progress": {}},
        ]
    )
    assert hours == 4.5  # 40 min × 7 = 280 min
    assert [e["subject"] for e in exams] == ["A"]


# -- free time and validation ----------------------------------------------------------------


def test_free_windows_skip_busy_and_short_gaps():
    d = date(2026, 11, 24)
    busy = [
        Busy(datetime(2026, 11, 24, 9, 0, tzinfo=TZ), datetime(2026, 11, 24, 10, 0, tzinfo=TZ), "a", "x"),  # noqa: E501
        Busy(datetime(2026, 11, 24, 10, 10, tzinfo=TZ), datetime(2026, 11, 24, 12, 0, tzinfo=TZ), "b", "x"),  # noqa: E501
    ]  # fmt: skip
    windows = [(f"{s:%H:%M}", f"{e:%H:%M}") for s, e in free_windows(d, busy, TZ)]
    assert windows == [("06:00", "09:00"), ("12:00", "23:00")]


def test_validate_rejects_clashes_past_and_overlaps():
    busy = [Busy(datetime(2026, 11, 24, 9, 0, tzinfo=TZ), datetime(2026, 11, 24, 10, 0, tzinfo=TZ), "Dentist", "x")]  # fmt: skip  # noqa: E501
    freeze = datetime(2026, 11, 23, 19, 0, tzinfo=TZ)
    blocks = [
        Block("1", "2026-11-24", "08:30", "09:30", "p", "P", "clash"),
        Block("2", "2026-11-23", "10:00", "11:00", "p", "P", "past"),
        Block("3", "2026-11-24", "10:00", "11:00", "p", "P", "ok"),
        Block("4", "2026-11-24", "10:30", "11:30", "p", "P", "overlap"),
        Block("5", "2026-11-24", "12:00", "11:00", "p", "P", "backwards"),
    ]
    ok, errors = validate(blocks, busy, [], TZ, freeze, date(2026, 11, 29))
    assert [b.title for b in ok] == ["ok"]
    assert len(errors) == 4 and any("Dentist" in e for e in errors)


# -- planner runs ----------------------------------------------------------------------------


def test_run_writes_valid_blocks_and_retries_rejected_ones(planner, fake, vault):
    wish = planner.journal.add_wish("2026-11-24", "Morgen erst ab 10", datetime.now(TZ))
    first = PlanResult(
        blocks=[block("2026-11-24", "08:30", "09:30", "Clash")],
        summary="first",
    )
    fixed = PlanResult(
        blocks=[
            block("2026-11-24", "10:00", "11:30", "LifeOS: Planer bauen"),
            block("2026-11-24", "17:00", "17:45", "Laufen", goal="Sport"),
        ],
        wishes=[WishAnswer(id=wish["id"], done=True, how="Start um 10")],
        summary="Dienstag ab 10.",
    )
    llm = FakeLlm([first, fixed])
    outcome = planner.run(lambda agent: llm, LlmSettings(), "wish")
    assert outcome.status == "ok", outcome.message
    assert "<rejected>" in llm.prompts[1] and "Dentist" in llm.prompts[1]
    assert "BWL: exam 2026-12-01" in llm.prompts[0] and "Morgen erst ab 10" in llm.prompts[0]
    assert "free: 06:00–09:00, 10:00–23:00" in llm.prompts[0]  # Tuesday around the dentist

    events = plan_events(fake, planner)
    assert sorted(e["summary"] for e in events) == ["Laufen", "LifeOS: Planer bauen"]
    blocks = planner.plans.blocks()
    assert {b.category for b in blocks} == {"programmieren", "sport"}
    assert all(b.gid for b in blocks)
    assert planner.journal.wishes()[0]["status"] == "done"
    assert planner.plans.load()["summary"] == "Dienstag ab 10."


def test_second_run_moves_kept_blocks_and_deletes_dropped_ones(planner, fake):
    llm = FakeLlm([
        PlanResult(blocks=[block("2026-11-24", "10:00", "11:00", "A"),
                           block("2026-11-25", "10:00", "11:00", "B")], summary="1"),
    ])  # fmt: skip
    planner.run(lambda agent: llm, LlmSettings())
    a, b = planner.plans.blocks()
    fake.calls.clear()
    llm2 = FakeLlm([
        PlanResult(blocks=[block("2026-11-24", "13:00", "14:00", "A", id=a.id)], summary="2"),
    ])  # fmt: skip
    planner.run(lambda agent: llm2, LlmSettings())
    assert fake.calls == ["patch A", "delete B"]
    [kept] = planner.plans.blocks()
    assert kept.id == a.id and kept.start == "13:00" and kept.gid == a.gid


def test_owner_changes_in_google_win(planner, fake):
    llm = FakeLlm([
        PlanResult(blocks=[block("2026-11-24", "10:00", "11:00", "A"),
                           block("2026-11-25", "10:00", "11:00", "B")], summary="1"),
    ])  # fmt: skip
    planner.run(lambda agent: llm, LlmSettings())
    a, b = planner.plans.blocks()
    target = planner.calendar.config()["target"]
    fake.items[target][a.gid]["start"] = {"dateTime": "2026-11-24T15:00:00+01:00"}
    fake.items[target][a.gid]["end"] = {"dateTime": "2026-11-24T16:00:00+01:00"}
    del fake.items[target][b.gid]

    llm2 = FakeLlm([PlanResult(blocks=[block("2026-11-24", "15:00", "16:00", "A", id=a.id)],
                               summary="2")])  # fmt: skip
    planner.run(lambda agent: llm2, LlmSettings())
    assert "moved by you: A" in llm2.prompts[0] and "removed by you" in llm2.prompts[0]
    by_id = {x.id: x for x in planner.plans.blocks()}
    assert by_id[a.id].start == "15:00" and by_id[b.id].status == REMOVED


def test_daily_limit_and_missing_agent(planner, vault):
    for _ in range(3):
        planner.run(lambda agent: FakeLlm([PlanResult(blocks=[], summary="")]), LlmSettings())
    assert planner.run(lambda agent: FakeLlm([]), LlmSettings()).status == "skipped"
    (vault.root / "agents" / f"{AGENT}.md").unlink()
    assert "no agents/" in planner.run(lambda agent: FakeLlm([]), LlmSettings()).message


def test_model_failure_keeps_the_queue(planner):
    planner.enqueue("wish")
    out = planner.run(lambda agent: FakeLlm([RuntimeError("boom")]), LlmSettings(), "wish")
    assert out.status == "error"
    assert [q["reason"] for q in planner.queue()] == ["wish"]


def test_targets_count_past_blocks(planner):
    planner.plans.save(
        [
            Block("1", "2026-11-23", "08:00", "10:00", "programming", "Programmieren", "a", status=DONE),  # noqa: E501
            Block("2", "2026-11-23", "10:00", "11:00", "programming", "Programmieren", "b", status=SKIPPED),  # noqa: E501
            Block("3", "2026-11-23", "17:00", "17:45", "sport", "Sport", "c"),
            Block("4", "2026-11-25", "08:00", "09:00", "programming", "Programmieren", "d"),
        ]
    )  # fmt: skip
    t = {x["name"]: x for x in planner.targets()}
    assert t["Programmieren"]["done"] == 2 and t["Programmieren"]["planned"] == 1
    assert t["Programmieren"]["remaining"] == 6
    assert t["Sport"]["done"] == 1 and t["Uni"]["target"] == 3.5  # 30 min × 7


def test_calendar_change_queues_a_replan(planner, fake):
    planner.calendar.sync()
    assert planner.notice_calendar() is False  # first look only remembers
    planner.run(lambda agent: FakeLlm([PlanResult(blocks=[], summary="")]), LlmSettings())
    assert planner.notice_calendar() is False
    fake.add("primary", "New meeting", "2026-11-26T14:00:00+01:00", "2026-11-26T15:00:00+01:00")
    planner.calendar.sync()
    assert planner.notice_calendar() is True
    assert planner.due_from_queue() is None  # just planned: calendar changes wait


# -- journal ----------------------------------------------------------------------------------


def test_journal_roundtrip_is_readable_markdown(vault):
    j = Journal(vault)
    j.write(Entry("2026-11-23", mood=4, energy=2, sleep=6.5, pain=3, pain_where="Knie",
                  good="Viel geschafft", bad="Zu spät los", notes=""))  # fmt: skip
    text = j.entry_path("2026-11-23").read_text(encoding="utf-8")
    assert "schmerz_ort: Knie" in text and "## Gut\n\nViel geschafft" in text
    assert "Montag, 23.11.2026" in text
    e = j.read("2026-11-23")
    assert (e.mood, e.sleep, e.pain_where, e.bad) == (4, 6.5, "Knie", "Zu spät los")


# -- API ----------------------------------------------------------------------------------------


def test_planning_api(client, settings):
    root = settings.vault_root
    (root / "ready" / "knowledge" / "Planning").mkdir(parents=True, exist_ok=True)
    r = client.get("/api/planning/goals").json()
    assert r["goals"] == [] and r["revision"] is None
    saved = client.put("/api/planning/goals", json={
        "goals": [{"name": "Sport", "amount": 6, "unit": "sessions"},
                  {"name": "Uni", "amount": None}],
        "rules": "- Arbeit 8–17", "revision": None,
    }).json()  # fmt: skip
    assert [x["name"] for x in saved["goals"]] == ["Sport", "Uni"]
    assert saved["goals"][1]["auto"] is True
    assert (
        client.put("/api/planning/goals", json={"goals": [], "revision": None}).status_code == 409
    )

    day = client.put("/api/planning/journal/2026-11-23", json={"mood": 4, "good": "ok"}).json()
    assert day["mood"] == 4
    assert client.put("/api/planning/journal/2026-11-23", json={"mood": 9}).status_code == 422

    wish = client.post("/api/planning/wishes", json={"day": "2026-11-24", "text": "frei"}).json()
    status = client.get("/api/planning/status").json()
    assert [q["reason"] for q in status["queue"]] == ["goals changed", "wish"]
    view = client.get("/api/planning/day/2026-11-24").json()
    assert view["wishes"][0]["id"] == wish["id"] and view["blocks"] == []
    assert len(client.get("/api/planning/week").json()["days"]) == 7


def test_checkin_updates_block_and_appends_log(client, settings):
    vault = Vault.open(settings.vault_root)
    from lifeos.modules.planning.plan import PlanStore

    PlanStore(vault, settings.timezone).save(
        [Block("b1", "2026-11-23", "08:00", "09:00", "sport", "Sport", "Laufen")]
    )
    r = client.post("/api/planning/blocks/b1/status", json={"status": "partial", "minutes": 20})
    assert r.json()["status"] == "partial" and r.json()["minutes_done"] == 20
    assert (
        client.post("/api/planning/blocks/nope/status", json={"status": "done"}).status_code == 404
    )
    logs = list((settings.vault_root / "ready/knowledge/Planning/Log").glob("*.jsonl"))
    line = json.loads(logs[0].read_text(encoding="utf-8").splitlines()[0])
    assert line["block"] == "b1" and line["minutes"] == 20
    stored = json.loads((settings.vault_root / PLAN_FILE).read_text(encoding="utf-8"))
    assert stored["blocks"][0]["status"] == "partial"
    assert not (settings.vault_root / QUEUE_FILE).exists()


def test_planner_sees_groups_and_never_plans_the_group_itself(planner, vault):
    path = vault.resolve("ready/knowledge/Planning/Goals.md")
    path.write_text(g.render("", work_goals(), "- r"), encoding="utf-8")
    llm = FakeLlm([
        PlanResult(blocks=[block("2026-11-24", "10:00", "11:00", "x", goal="Arbeit"),
                           block("2026-11-24", "11:00", "12:00", "y", goal="LifeOS")],
                   summary="s"),
    ])  # fmt: skip
    planner.run(lambda agent: llm, LlmSettings())
    assert "- GROUP Arbeit (priority mittel): 30 h/week" in llm.prompts[0]
    assert "  - LifeOS (priority mittel): " in llm.prompts[0]
    by_title = {b.title: b for b in planner.plans.blocks()}
    assert by_title["x"].goal == "" and by_title["y"].goal == "LifeOS"
    t = {x["name"]: x for x in planner.targets()}
    assert t["Arbeit"]["planned"] == 1  # members count for their group


def test_saving_twice_does_not_add_blank_lines():
    goals, rules = g.parse(GOALS)
    once = g.render(GOALS, goals, rules)
    assert g.render(once, goals, rules) == once
    assert "## Regeln\n\n- Arbeit" in once


def test_status_reports_the_daily_limit(planner):
    assert planner.limits() == {"runsToday": 0, "dailyLimit": 3, "limitReached": False}
    for _ in range(3):
        planner.run(lambda agent: FakeLlm([PlanResult(blocks=[], summary="")]), LlmSettings())
    planner.enqueue("wish")
    assert planner.limits()["limitReached"] is True
    assert planner.run(lambda agent: FakeLlm([]), LlmSettings()).status == "skipped"
    assert [q["reason"] for q in planner.queue()] == ["wish"]  # waits for tomorrow


def test_running_marker_exists_only_during_a_run(planner):
    seen = []

    class Watching(FakeLlm):
        def parse(self, system, prompt, schema, effort, images=None):
            seen.append(planner.running())
            return super().parse(system, prompt, schema, effort, images)

    planner.run(lambda agent: Watching([PlanResult(blocks=[], summary="")]), LlmSettings(), "wish")
    assert seen[0]["reason"] == "wish" and planner.running() is None
    planner.run(lambda agent: FakeLlm([RuntimeError("boom")]), LlmSettings(), "wish")
    assert planner.running() is None


def _two_blocks(planner):
    llm = FakeLlm([
        PlanResult(blocks=[block("2026-11-24", "10:00", "11:00", "A"),
                           block("2026-11-25", "10:00", "11:00", "B")], summary="1"),
    ])  # fmt: skip
    planner.run(lambda agent: llm, LlmSettings())
    return planner.plans.blocks()


def test_google_description_is_exactly_the_block_text(planner, fake):
    a, _ = _two_blocks(planner)
    target = planner.calendar.config()["target"]
    assert fake.items[target][a.gid]["description"] == "r"


def test_owner_edit_is_written_to_google_and_kept_by_the_planner(planner, fake, client):
    a, _ = _two_blocks(planner)
    planner.plans.edit(a.id, "LifeOS: Kalender-Tab bauen", "Wochenansicht einspaltig")
    target = planner.calendar.config()["target"]  # like the API: Google gets it right away
    fake.patch_event(target, a.gid, {"summary": "LifeOS: Kalender-Tab bauen",
                                     "description": "Wochenansicht einspaltig"})  # fmt: skip
    edited = {b.id: b for b in planner.plans.blocks()}[a.id]
    assert edited.edited and edited.reason == "Wochenansicht einspaltig"
    llm = FakeLlm([
        PlanResult(blocks=[block("2026-11-24", "10:00", "11:00", "Something else", id=a.id)],
                   summary="2"),
    ])  # fmt: skip
    planner.run(lambda agent: llm, LlmSettings())
    assert "SET BY THE OWNER" in llm.prompts[0] and "Wochenansicht einspaltig" in llm.prompts[0]
    kept = {b.id: b for b in planner.plans.blocks()}[a.id]
    assert (kept.title, kept.reason) == ("LifeOS: Kalender-Tab bauen", "Wochenansicht einspaltig")
    target = planner.calendar.config()["target"]
    assert fake.items[target][a.gid]["summary"] == "LifeOS: Kalender-Tab bauen"


def test_owner_block_survives_when_the_model_drops_it(planner):
    a, b = _two_blocks(planner)
    planner.plans.edit(b.id, None, "mein Plan")
    llm = FakeLlm([PlanResult(blocks=[block("2026-11-24", "10:00", "11:00", "A", id=a.id)],
                              summary="2")])  # fmt: skip
    planner.run(lambda agent: llm, LlmSettings())
    assert b.id in {x.id for x in planner.plans.blocks()}


def test_edits_in_google_are_adopted_but_old_footers_are_not_edits(planner, fake):
    a, b = _two_blocks(planner)
    target = planner.calendar.config()["target"]
    fake.items[target][a.gid]["description"] = "r\nGoal: Programmieren\nPlanned by LifeOS – x"
    fake.items[target][b.gid]["description"] = "Kapitel 3 lesen"
    planner.calendar.sync()
    store = planner.calendar.store()
    changes = planner.plans.reconcile(store["plans"], store["window"], store["synced_at"])
    by_id = {x.id: x for x in planner.plans.blocks()}
    assert not by_id[a.id].edited
    assert by_id[b.id].edited and by_id[b.id].reason == "Kapitel 3 lesen"
    assert changes == ["edited by you: B"]


def test_a_sync_older_than_the_last_change_is_ignored(planner, fake):
    a, _ = _two_blocks(planner)
    target = planner.calendar.config()["target"]
    del fake.items[target][a.gid]
    store = planner.calendar.store() | {"plans": [], "synced_at": "2000-01-01T00:00:00+01:00"}
    assert planner.plans.reconcile(store["plans"], store["window"], store["synced_at"]) == []


def test_edit_block_api(client, settings):
    from lifeos.modules.planning.plan import PlanStore

    PlanStore(Vault.open(settings.vault_root), settings.timezone).save(
        [Block("b1", "2026-11-24", "08:00", "09:00", "lifeos", "LifeOS", "Alt", "alt")]
    )
    r = client.patch("/api/planning/blocks/b1", json={"title": "Neu", "description": "Plan"})
    assert r.status_code == 200 and r.json()["edited"] is True
    assert (r.json()["title"], r.json()["reason"]) == ("Neu", "Plan")
    assert client.patch("/api/planning/blocks/x", json={"title": "y"}).status_code == 404


def test_week_goals_for_a_past_week(client):
    past = client.get("/api/planning/week?start=2026-11-02").json()
    assert past["start"] == "2026-11-02" and isinstance(past["goals"], list)
