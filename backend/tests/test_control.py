from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from lifeos.core.auth import hash_password
from lifeos.core.config import Settings
from lifeos.main import create_app
from lifeos.modules.control.catalog import build_catalog
from lifeos.modules.control.checks import Check
from lifeos.modules.control.health import FAIL, OFF, OK, WARN, Sources, evaluate
from lifeos.modules.control.store import RunRequests
from lifeos.worker.scheduler import Every, Job, JobResult, Scheduler

TZ = ZoneInfo("Europe/Vienna")
NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)  # 13:00 in Vienna
TOKEN = "r" * 40
PASSWORD = "pw for the control tests"


def iso(delta: timedelta) -> str:
    return (NOW - delta).isoformat(timespec="seconds")


def settings(tmp_path: Path, **kw) -> Settings:
    vault = tmp_path / "vault"
    (vault / "ready" / "knowledge").mkdir(parents=True, exist_ok=True)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    base = dict(
        vault_root=vault, state_dir=state, session_secret="x" * 40,
        password_hash=hash_password(PASSWORD), timezone="Europe/Vienna",
        git_sync_interval=300, moodle_sync_at="05:00", teams_mail_sync_at="07:30",
        google_client_id="id", google_client_secret="secret", moodle_url="https://moodle.test",
        control_report_token=TOKEN,
    )  # fmt: skip
    return Settings(**{**base, **kw})


def light(result: dict, pid: str) -> tuple[str, str]:
    p = next(p for p in result["processes"] if p["id"] == pid)
    return p["level"], p["reason"]


def run(tmp_path: Path, jobs: dict, pc: dict | None = None, checks=None, **kw) -> dict:
    cat = build_catalog(settings(tmp_path, **kw))
    alive = {"git-sync": {"lastRun": iso(timedelta(minutes=2)),
                          "lastSuccess": iso(timedelta(minutes=2)), "status": "ok"}}  # fmt: skip
    src = Sources(jobs={**alive, **jobs}, pc=pc or {}, checks=checks or {})
    return evaluate(cat, src, NOW, TZ)


def test_interval_job_by_age_of_last_success(tmp_path):
    def git(minutes: int, status: str = "ok") -> dict:
        at = iso(timedelta(minutes=minutes))
        return {"git-sync": {"lastRun": iso(timedelta(minutes=1)), "lastSuccess": at,
                             "status": status, "message": "boom"}}  # fmt: skip

    assert light(run(tmp_path, git(4)), "git-sync")[0] == OK
    assert light(run(tmp_path, git(20)), "git-sync")[0] == WARN
    level, reason = light(run(tmp_path, git(45)), "git-sync")
    assert level == FAIL and "45 min" in reason
    level, reason = light(run(tmp_path, git(1, "error")), "git-sync")
    assert level == FAIL and "boom" in reason


def test_daily_job_against_todays_slot(tmp_path):
    def moodle(success: timedelta | None, status="ok") -> dict:
        entry = {"lastRun": iso(timedelta(minutes=30)), "status": status, "message": ""}
        if success is not None:
            entry["lastSuccess"] = iso(success)
        return {"moodle-sync": entry}

    # slot 05:00 Vienna = 04:00 UTC, eight hours ago
    assert light(run(tmp_path, moodle(timedelta(hours=7))), "moodle-sync")[0] == OK
    level, reason = light(run(tmp_path, moodle(timedelta(hours=20))), "moodle-sync")
    assert level == FAIL and "since" in reason
    # before the slot of today, yesterday's counts: teams-mail at 07:30 is done at 11:00 UTC
    early = NOW.replace(hour=5, minute=0)  # 06:00 Vienna, before 07:30
    cat = build_catalog(settings(tmp_path))
    jobs = {
        "git-sync": {"lastRun": early.isoformat(), "lastSuccess": early.isoformat(),
                     "status": "ok"},
        "teams-mail": {"lastRun": (early - timedelta(hours=22)).isoformat(),
                       "lastSuccess": (early - timedelta(hours=22)).isoformat(), "status": "ok"},
    }  # fmt: skip
    result = evaluate(cat, Sources(jobs=jobs), early, TZ)
    assert light(result, "teams-mail")[0] == OK


def test_daily_job_late_is_yellow_first(tmp_path):
    # teams-mail slot 07:30 Vienna = 06:30 UTC; at 08:00 UTC it is 1.5 h late
    at = NOW.replace(hour=8)
    cat = build_catalog(settings(tmp_path))
    jobs = {"git-sync": {"lastRun": at.isoformat(), "lastSuccess": at.isoformat(), "status": "ok"}}
    assert light(evaluate(cat, Sources(jobs=jobs), at, TZ), "teams-mail")[0] == WARN


def test_stopped_worker_turns_server_jobs_red(tmp_path):
    cat = build_catalog(settings(tmp_path))
    old = iso(timedelta(minutes=40))
    src = Sources(jobs={"git-sync": {"lastRun": old, "lastSuccess": old, "status": "ok"}})
    result = evaluate(cat, src, NOW, TZ)
    level, reason = light(result, "moodle-sync")
    assert level == FAIL and "worker" in reason
    assert result["level"] == FAIL
    assert next(c for c in result["system"] if c["id"] == "worker")["ok"] is False


def test_missing_precondition_is_red_and_targets_inherit(tmp_path):
    fresh = {"lastRun": iso(timedelta(hours=1)), "lastSuccess": iso(timedelta(hours=1)),
             "status": "ok"}  # fmt: skip
    checks = {
        "gmail": Check("gmail", "Gmail permission", False, "no Gmail permission", "reconnect")
    }
    result = run(tmp_path, {"teams-mail": fresh}, checks=checks)
    level, reason = light(result, "teams-mail")
    assert level == FAIL and "reconnect" in reason
    node = next(n for n in result["nodes"] if n["id"] == "kb-teams-chats")
    assert node["level"] == FAIL and "Teams messages" in node["reason"]


def test_pc_processes(tmp_path):
    fresh = {"lastRun": iso(timedelta(hours=1)), "lastSuccess": iso(timedelta(hours=1)),
             "status": "ok"}  # fmt: skip
    stale = {"lastRun": iso(timedelta(days=4)), "lastSuccess": iso(timedelta(days=4)),
             "status": "ok"}  # fmt: skip
    result = run(tmp_path, {}, pc={"teams-files": fresh, "job-import": stale})
    assert light(result, "teams-files")[0] == OK
    assert light(result, "job-import")[0] == FAIL
    # without a report token the PC processes are not set up
    result = run(tmp_path, {}, control_report_token=None)
    assert light(result, "teams-files")[0] == OFF


def test_quiet_skips_stay_green(tmp_path):
    q = {"lastRun": iso(timedelta(seconds=30)), "lastSuccess": iso(timedelta(seconds=30)),
         "status": "skipped", "message": "nothing queued"}  # fmt: skip
    result = run(tmp_path, {"planner-queue": q}, calendar_sync_interval=300)
    assert light(result, "planner-queue")[0] == OK


def test_api_report_status_and_run_request(tmp_path):
    s = settings(tmp_path)
    app = create_app(s)
    anon = TestClient(app)
    body = {"status": "ok", "message": "3 new", "duration": 4.2}
    assert anon.post("/api/control/report/teams-files", json=body).status_code == 401
    wrong = {"Authorization": "Bearer " + "w" * 40}
    assert anon.post("/api/control/report/teams-files", json=body, headers=wrong).status_code == 401
    auth = {"Authorization": f"Bearer {TOKEN}"}
    r = anon.post("/api/control/report/teams-files", json=body, headers=auth)
    assert r.status_code == 200 and r.json()["lastSuccess"]
    assert anon.post("/api/control/report/git-sync", json=body, headers=auth).status_code == 404
    # the token only opens the report prefix
    assert anon.get("/api/control/status", headers=auth).status_code == 401

    client = TestClient(app)
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    status = client.get("/api/control/status").json()
    teams = next(p for p in status["processes"] if p["id"] == "teams-files")
    assert teams["message"] == "3 new" and teams["history"][-1]["status"] == "ok"
    assert client.post("/api/control/run/moodle-sync").status_code == 200
    assert "moodle-sync" in json.loads((s.state_dir / "job-requests.json").read_text())
    assert client.post("/api/control/run/teams-files").status_code == 404


def test_scheduler_runs_requested_job_once(tmp_path):
    calls: list[str] = []
    requests = RunRequests(tmp_path)
    base = datetime.now(UTC) - timedelta(seconds=10)
    clock = [base]
    job = Job("x", Every(3600), lambda: calls.append("x") or JobResult("ok", "done"))
    sched = Scheduler([job], tmp_path / "jobs.json", clock=lambda: clock[0], requests=requests)
    sched.tick()
    clock[0] = base + timedelta(seconds=5)
    sched.tick()
    assert calls == ["x"]  # not due, not requested
    requests.request("x")  # real time: newer than the last run
    clock[0] = base + timedelta(seconds=6)
    sched.tick()
    assert calls == ["x", "x"]
    assert requests.all() == {}
    state = json.loads((tmp_path / "jobs.json").read_text())
    assert [h["status"] for h in state["x"]["history"]] == ["ok", "ok"]


def test_reporter_sends_and_never_raises(tmp_path):
    import httpx

    from lifeos.modules.control.reporter import ReporterSettings, send

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    cfg = ReporterSettings(jobsearch_server_url="https://lifeos.test", control_report_token=TOKEN)
    assert send("teams-files", "ok", "1 new", 2.0, cfg, httpx.MockTransport(handler))
    assert seen[0].url.path == "/api/control/report/teams-files"
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    assert not send("teams-files", "ok", "", None, cfg, httpx.MockTransport(broken))
    assert not send("teams-files", "ok", "", None, ReporterSettings())


def test_google_token_keeps_granted_scope(tmp_path):
    from lifeos.connectors.google.auth import GoogleAuth

    auth = GoogleAuth("id", "secret", tmp_path, clock=lambda: 1000.0)
    auth._store({"access_token": "a", "refresh_token": "r", "scope": "openid email x"}, "me")
    auth._store({"access_token": "b"})  # a refresh without scope keeps the old one
    data = json.loads((tmp_path / "google-token.json").read_text())
    assert data["scope"] == "openid email x" and data["access_token"] == "b"


class FakeLlm:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def parse(self, system, prompt, schema, effort, images=None):
        from lifeos.agents.llm import Usage

        self.calls.append(prompt)
        problems = json.loads(prompt)["problems"]
        findings = [{"process": p["id"], "cause": "token lacks Gmail", "action": "reconnect"}
                    for p in problems]  # fmt: skip
        return schema.model_validate({"summary": "One problem.", "findings": findings}), Usage()


def test_supervisor_diagnoses_only_when_problems_change(tmp_path):
    from lifeos.core.vault import Vault
    from lifeos.modules.control.service import Control
    from lifeos.modules.control.supervisor import Supervisor

    s = settings(tmp_path, moodle_sync_at=None, teams_mail_sync_at=None, control_report_token=None)
    agents = s.vault_root / "agents"
    agents.mkdir()
    (agents / "control-room.md").write_text(
        "---\nname: control-room\ntitle: Control room\nrunner: job\nmodel: m\n"
        "language: German\ndaily_limit: 5\n---\n\n# Control room\n",
        encoding="utf-8",
    )
    jobs_file = s.state_dir / "jobs.json"
    now = datetime.now(UTC)

    def write_jobs(status: str) -> None:
        at = now.isoformat(timespec="seconds")
        entry = {"lastRun": at, "status": status, "message": "push rejected"}
        if status == "ok":
            entry["lastSuccess"] = at
        jobs_file.write_text(json.dumps({"git-sync": entry}), encoding="utf-8")

    llm = FakeLlm()
    control = Control(s, Vault.open(s.vault_root), jobs_file)
    sup = Supervisor(control, lambda agent: llm, git_log=lambda: ["2026-03-10 moodle: 1 new"])
    write_jobs("error")
    assert sup.run(now)[0] == "ok"
    assert "push rejected" in llm.calls[0]
    report = json.loads((s.state_dir / "control-report.json").read_text())
    assert report["level"] == "fail"
    assert [f["process"] for f in report["findings"]] == ["git-sync"]
    assert sup.run(now + timedelta(minutes=5)) == ("skipped", "unchanged")
    assert len(llm.calls) == 1
    assert control.status()["report"]["summary"] == "One problem."

    write_jobs("ok")
    assert sup.run(now + timedelta(minutes=6)) == ("skipped", "all green")
    assert len(llm.calls) == 1
    assert json.loads((s.state_dir / "control-report.json").read_text())["level"] == "ok"


def test_ai_processes_show_agent_and_runs_today(tmp_path):
    from lifeos.core.vault import Vault
    from lifeos.modules.career.matcher import STATE_FILE as MATCHER_STATE
    from lifeos.modules.control.service import Control
    from lifeos.modules.planning.planner import STATE_FILE as PLANNER_STATE

    s = settings(tmp_path, planner_at="17:30", calendar_sync_interval=300, agents_interval=120,
                 github_token="t")  # fmt: skip
    vault = s.vault_root
    agents = vault / "agents"
    (agents / "assistant").mkdir(parents=True)

    def agent(name: str, runner: str, extra: str = "") -> None:
        text = f"---\nname: {name}\ntitle: T {name}\nrunner: {runner}\nmodel: m-{name}\n"
        (agents / f"{name}.md").write_text(text + extra + "---\n\n# x\n", encoding="utf-8")

    agent("calendar-planner", "job", "billing: subscription\ndaily_limit: 20\n")
    agent("job-matcher", "job", "daily_limit: 10\nmanual_limit: 5\n")
    agent("assistant", "workflow", "workflow: assistant\n")
    now = datetime.now(TZ)
    today = now.date().isoformat()
    (agents / "assistant" / "runs.md").write_text(
        f"# Runs\n\n## {today} 09:00 · #1 A · done\n\n## 2020-01-01 09:00 · #2 B · done\n",
        encoding="utf-8",
    )
    for rel, data in (
        (PLANNER_STATE, {"runs": {today: 3}}),
        (MATCHER_STATE, {"days": {today: {"auto": 2, "manual": 1}}}),
    ):
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    status = Control(s, Vault.open(vault), None).status()
    by_id = {p["id"]: p for p in status["processes"]}
    planner = by_id["calendar-planner"]["ai"][0]
    assert planner["model"] == "m-calendar-planner" and planner["billing"] == "subscription"
    assert planner["runsToday"] == 3 and planner["dailyLimit"] == 20
    matcher = by_id["job-matcher"]["ai"][0]
    assert matcher["runsToday"] == 3 and matcher["dailyLimit"] == 15
    assert [(a["agent"], a["runsToday"]) for a in by_id["agents"]["ai"]] == [("assistant", 1)]
    assert by_id["git-sync"]["ai"] == []


def test_actionable_trusts_upstream_and_counts_new_material(tmp_path):
    import os

    from lifeos.core.vault import Vault
    from lifeos.modules.control.service import PLANNER_STATE, Control

    s = settings(tmp_path, github_token="t", university_project="me/5", uni_planner_interval=1800)
    kb = s.vault_root / "ready" / "knowledge"
    (kb / "Moodle").mkdir()
    (kb / "Teams" / "Chats").mkdir(parents=True)
    old_note, new_note, chat = (
        kb / "Moodle" / "a.md",
        kb / "Moodle" / "b.md",
        kb / "Teams" / "Chats" / "c.md",
    )
    for p in (old_note, new_note, chat):
        p.write_text("x", encoding="utf-8")
    now = datetime.now(UTC)
    ran = now - timedelta(minutes=20)
    os.utime(old_note, (ran.timestamp() - 600, ran.timestamp() - 600))
    state = s.vault_root / PLANNER_STATE
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"lastRun": ran.isoformat(), "summary": "2 new",
                                 "pending": {"teams-messages": 4}, "totals": {"moodle": 2},
                                 "considered": {"moodle": 2}}), encoding="utf-8")  # fmt: skip
    fresh = {"lastRun": now.isoformat(), "lastSuccess": now.isoformat(), "status": "ok"}
    jobs = {
        "git-sync": fresh,
        "uni-planner": {"lastRun": ran.isoformat(), "lastSuccess": ran.isoformat(), "status": "ok"},
        "moodle-sync": {"lastRun": now.isoformat(), "status": "error", "message": "token expired"},
        "teams-mail": fresh,
    }
    (s.state_dir / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
    (s.state_dir / "pc-jobs.json").write_text(json.dumps({"teams-files": fresh}), encoding="utf-8")

    status = Control(s, Vault.open(s.vault_root), s.state_dir / "jobs.json").status(now)
    uni = next(n for n in status["nodes"] if n["id"] == "act-university")
    assert uni["kind"] == "actionable" and uni["url"] == "https://github.com/users/me/projects/5"
    assert uni["level"] == "warn" and "Moodle sync" in uni["reason"]
    based = {b["id"]: b for b in uni["trust"]["basedOn"]}
    assert based["kb-moodle"]["level"] == "fail" and based["kb-moodle"]["newSince"] == 1
    assert based["kb-teams-files"]["newSince"] == 0  # Teams/Chats belongs to its own node
    assert based["kb-teams-chats"]["pending"] == 4
    assert uni["trust"]["generators"]["uni-planner"]["summary"] == "2 new"
    assert "moodle-sync" in uni["trust"]["upstream"]
    foundation = {p["id"] for p in status["processes"] if p["foundation"]}
    assert foundation == {"git-sync", "deployer", "tickets"}
