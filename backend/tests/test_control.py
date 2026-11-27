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
    assert node["level"] == FAIL and "Import Teams chats from Gmail" in node["reason"]


def test_pc_processes(tmp_path):
    fresh = {"lastRun": iso(timedelta(hours=1)), "lastSuccess": iso(timedelta(hours=1)),
             "status": "ok"}  # fmt: skip
    stale = {"lastRun": iso(timedelta(days=4)), "lastSuccess": iso(timedelta(days=4)),
             "status": "ok"}  # fmt: skip
    result = run(tmp_path, {}, pc={"teams-files": fresh, "job-import": stale})
    assert light(result, "teams-files")[0] == OK
    assert light(result, "job-import")[0] == FAIL
    # a night with the PC off is no warning
    night = {"lastRun": iso(timedelta(hours=10)), "lastSuccess": iso(timedelta(hours=10)),
             "status": "ok"}  # fmt: skip
    assert light(run(tmp_path, {}, pc={"job-import": night}), "job-import")[0] == OK
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
    assert uni["level"] == "warn" and "Mirror Moodle courses" in uni["reason"]
    based = {b["id"]: b for b in uni["trust"]["basedOn"]}
    assert based["kb-moodle"]["level"] == "fail" and based["kb-moodle"]["newSince"] == 1
    assert based["kb-teams-files"]["newSince"] == 0  # Teams/Chats belongs to its own node
    assert based["kb-teams-chats"]["pending"] == 4
    assert uni["trust"]["generators"]["uni-planner"]["summary"] == "2 new"
    assert "moodle-sync" in uni["trust"]["upstream"]
    foundation = {p["id"] for p in status["processes"] if p["foundation"]}
    assert foundation == {"git-sync", "deployer", "claude-usage"}


def test_application_cycle(tmp_path):
    from lifeos.core.vault import Vault
    from lifeos.modules.control.service import Control

    s = settings(tmp_path, github_token="t", applications_project="me/4", applications_interval=120,
                 agents_interval=120, github_projects="me/3")  # fmt: skip
    agents = s.vault_root / "agents"
    agents.mkdir()
    for name in ("application-writer", "assistant"):
        (agents / f"{name}.md").write_text(
            f"---\nname: {name}\ntitle: {name}\nrunner: workflow\n"
            f"workflow: {name}\nmodel: m\n---\n",
            encoding="utf-8",
        )
    index = s.vault_root / ".lifeos" / "applications" / "index.json"
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps({"7": {"created": "2026-11-18T17:55:41+00:00"}}), encoding="utf-8")
    now = datetime.now(UTC)
    at = now.isoformat(timespec="seconds")
    jobs = {"agents": {"lastRun": at, "lastSuccess": at, "status": "ok", "message": "no changes"}}
    (s.state_dir / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")

    status = Control(s, Vault.open(s.vault_root), s.state_dir / "jobs.json").status(now)
    by_id = {p["id"]: p for p in status["processes"]}
    start = by_id["start-application"]
    assert start["reactsTo"] == ["act-job-assessments"] and start["reaction"] == "Apply"
    assert start["lastRun"].startswith("2026-11-18")
    writer = by_id["application-writer"]
    assert writer["message"] == "no changes"  # status of the shared agents job
    assert [a["agent"] for a in writer["ai"]] == ["application-writer"]
    assert [a["agent"] for a in by_id["agents"]["ai"]] == ["assistant"]  # not listed twice
    assert by_id["applications-board"]["reaction"] == "Sent"


def test_second_order_manager_and_derived_boards(tmp_path):
    jobs = {"agents": {"lastRun": iso(timedelta(minutes=1)), "status": "error",
                       "message": "boom"}}  # fmt: skip
    result = run(tmp_path, jobs, github_token="t", github_projects="o/3", agents_interval=120,
                 manager_interval=7200)  # fmt: skip
    manager = next(p for p in result["processes"] if p["id"] == "lifeos-manager")
    assert manager["enabled"] and manager["secondOrder"]
    assert manager["inputs"] == ["boards", "control-room", "control-explanation"] and manager[
        "outputs"
    ] == ["act-tasks"]
    nodes = {n["id"]: n for n in result["nodes"]}
    assert nodes["act-tasks"]["level"] == FAIL  # the AI teammates failed
    assert nodes["boards"]["kind"] == "dataset"
    assert nodes["boards"]["level"] == FAIL
    assert "Approve and review AI work" in nodes["boards"]["reason"]
    assert nodes["control-room"]["level"] == "ok"
    off = run(tmp_path, {}, github_token="t")
    assert not next(p for p in off["processes"] if p["id"] == "lifeos-manager")["enabled"]


def test_status_shows_the_managers_explanations(tmp_path):
    s = settings(tmp_path)
    state = s.vault_root / ".lifeos" / "agents" / "lifeos-manager.json"
    state.parent.mkdir(parents=True)
    note = {"cause": "Ticket gelöscht", "expect": "green", "ticket": "", "level": "fail",
            "at": iso(timedelta(minutes=5)), "until": iso(timedelta(minutes=-25))}  # fmt: skip
    watch = {"explained": {"agents": note}, "attempts": {"agents": 1, "git-sync": 2}}
    state.write_text(json.dumps({"watch": watch}), "utf-8")
    from lifeos.core.vault import Vault
    from lifeos.modules.control.service import Control

    notes = Control(s, Vault.open(s.vault_root), None).status(NOW)["manager"]
    keys = ("cause", "expect", "ticket", "at", "until")
    assert notes["agents"] == {k: note[k] for k in keys} | {"attempts": 1}
    assert notes["git-sync"]["cause"] is None and notes["git-sync"]["attempts"] == 2


def test_teams_mail_interval_overrides_daily_slot(tmp_path):
    cat = build_catalog(settings(tmp_path, teams_mail_interval=3600))
    proc = next(p for p in cat.processes if p.id == "teams-mail")
    assert proc.every == 3600 and proc.daily_at is None and proc.trigger == "hourly"


def test_every_block_has_a_unique_global_ref(tmp_path):
    import re

    from lifeos.modules.control.catalog import REFS, RETIRED

    cat = build_catalog(settings(tmp_path))
    prefix = {"source": "S", "dataset": "D", "actionable": "A"}
    for n in cat.nodes:
        assert re.fullmatch(rf"{prefix[n.kind]}-\d+", n.ref), (n.id, n.ref)
    for p in cat.processes:
        assert re.fullmatch(r"P-\d+", p.ref), (p.id, p.ref)
    numbers = [int(b.ref.split("-")[1]) for b in [*cat.nodes, *cat.processes]]
    assert len(numbers) == len(set(numbers))  # one count for all kinds: P-12 and D-12 never both
    assert len(REFS) == len(set(REFS.values()))
    assert not set(RETIRED) & set(REFS.values())
    assert len(numbers) == len(REFS)  # no stale entries in the table


def test_daily_limit_of_the_explainer_is_no_warning(tmp_path):
    ok = iso(timedelta(minutes=1))
    job = {"lastRun": ok, "lastSuccess": ok, "status": "skipped",
           "message": "daily limit of 12 diagnoses reached"}  # fmt: skip
    result = run(tmp_path, {"control-report": job}, control_report_interval=300)
    assert light(result, "control-report")[0] == OK


def test_agent_quotas_mark_a_used_up_daily_limit(tmp_path):
    from lifeos.core.vault import Vault
    from lifeos.modules.control.ai import agent_quotas

    s = settings(tmp_path, control_report_interval=300)
    agents = s.vault_root / "agents"
    agents.mkdir()
    head = "---\nrunner: job\nmodel: m\n"
    (agents / "control-room.md").write_text(
        f"{head}name: control-room\ntitle: Control room explainer\ndaily_limit: 2\n---\n\n# C\n",
        encoding="utf-8",
    )
    (agents / "job-matcher.md").write_text(
        f"{head}name: job-matcher\ntitle: Job matcher\ndaily_limit: 1\nmanual_limit: 5\n"
        "max_searches: 3\n---\n\n# J\n",
        encoding="utf-8",
    )
    vault = Vault.open(s.vault_root)
    from lifeos.modules.career.matcher import STATE_FILE

    target = vault.resolve(STATE_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    today = NOW.astimezone(TZ).date().isoformat()
    target.write_text(json.dumps({"days": {today: {"auto": 1, "manual": 0}}}), encoding="utf-8")
    report = {"callsDay": today, "calls": {"n": 2}}
    ledger = [
        {"at": iso(timedelta(hours=1)), "process": "control-report", "tokens": 500},
        {"at": iso(timedelta(hours=2)), "process": "control-report", "tokens": 250, "usd": 0.01},
        {"at": iso(timedelta(days=2)), "process": "control-report", "tokens": 999},  # not today
        {"at": iso(timedelta(hours=1)), "process": "agents", "detail": "job-matcher", "tokens": 7},
    ]
    jobs = {"control-report": {"lastRun": iso(timedelta(minutes=4)), "status": "skipped",
                               "message": "unchanged"}}  # fmt: skip
    quotas = agent_quotas(
        vault, build_catalog(s), NOW.astimezone(TZ), report, ledger=ledger, jobs_state=jobs
    )
    assert quotas["control-room"]["tokensToday"] == 750
    # the last week per local day (today last), the last model call and the last check
    assert quotas["control-room"]["tokens7d"] == [0, 0, 0, 0, 999, 0, 750]
    assert quotas["control-room"]["lastCall"] == iso(timedelta(hours=1))
    assert quotas["control-room"]["lastJob"] == {
        "at": iso(timedelta(minutes=4)), "status": "skipped", "message": "unchanged",
    }  # fmt: skip
    assert quotas["job-matcher"]["lastJob"] is None and quotas["job-matcher"]["lastLog"] is None
    assert quotas["control-room"]["usdToday"] == 0.01
    assert quotas["job-matcher"]["tokensToday"] == 7
    room = quotas["control-room"]
    assert room["runsToday"] == 2 and room["dailyLimit"] == 2 and room["exhausted"]
    assert [p["ref"] for p in room["processes"]] == ["P-46"]
    matcher = quotas["job-matcher"]
    assert not matcher["exhausted"] and "automatic runs used up" in matcher["note"]
    assert "up to 3 web searches per run" in matcher["limits"]


# -- the worker's heartbeat ---------------------------------------------------------------


def beat(running: str | None, since: timedelta = timedelta(0), at: timedelta = timedelta(0)):
    run = {"job": running, "since": iso(since)} if running else None
    return {"at": iso(at), "running": run}


def test_a_long_run_is_not_a_dead_worker(tmp_path):
    cat = build_catalog(settings(tmp_path))
    # the vault sync last ran 26 min ago: one minute before a Moodle sync that still runs
    then = iso(timedelta(minutes=26))
    jobs = {"git-sync": {"lastRun": then, "lastSuccess": then, "status": "ok"}}
    worker = beat("moodle-sync", since=timedelta(minutes=25), at=timedelta(seconds=20))
    result = evaluate(cat, Sources(jobs=jobs, worker=worker), NOW, TZ)
    check = next(c for c in result["system"] if c["id"] == "worker")
    assert (
        check["ok"] is True and check["message"] == 'busy with "Mirror Moodle courses" for 25 min'
    )
    assert light(result, "git-sync") == (
        WARN, 'waiting: the worker is busy with "Mirror Moodle courses" (for 25 min)',
    )  # fmt: skip
    moodle = next(p for p in result["processes"] if p["id"] == "moodle-sync")
    assert moodle["runningSince"] == iso(timedelta(minutes=25))
    assert result["worker"]["running"]["label"] == "Mirror Moodle courses"
    # without the run the same age is past the deadline, as before
    idle = evaluate(cat, Sources(jobs=jobs, worker=beat(None, at=timedelta(seconds=20))), NOW, TZ)
    assert light(idle, "git-sync")[0] == WARN and "waiting" not in light(idle, "git-sync")[1]
    assert idle["worker"]["running"] is None


def test_a_silent_heartbeat_is_a_dead_worker_even_mid_run(tmp_path):
    cat = build_catalog(settings(tmp_path))
    then = iso(timedelta(minutes=26))
    jobs = {"git-sync": {"lastRun": then, "lastSuccess": then, "status": "ok"}}
    worker = beat("moodle-sync", since=timedelta(minutes=25), at=timedelta(minutes=12))
    result = evaluate(cat, Sources(jobs=jobs, worker=worker), NOW, TZ)
    check = next(c for c in result["system"] if c["id"] == "worker")
    assert check["ok"] is False and "no sign of life" in check["message"]
    assert light(result, "git-sync")[0] == FAIL


def test_a_run_longer_than_usual_is_worth_a_look(tmp_path):
    cat = build_catalog(settings(tmp_path))
    worker = beat("moodle-sync", since=timedelta(hours=4), at=timedelta(seconds=5))
    result = evaluate(cat, Sources(worker=worker), NOW, TZ)
    check = next(c for c in result["system"] if c["id"] == "worker")
    assert check["ok"] is None and "longer than usual" in check["message"]


# -- the page loads when something else is broken -------------------------------------------


def control_for(tmp_path, **kw):
    from lifeos.core.vault import Vault
    from lifeos.modules.control.service import Control

    s = settings(tmp_path, **kw)
    return s, Control(s, Vault.open(s.vault_root), s.state_dir / "jobs.json")


def test_status_survives_an_unreadable_part(tmp_path, monkeypatch):
    import lifeos.modules.control.service as service

    s, control = control_for(tmp_path)

    def boom(*_a, **_kw):
        raise KeyError("daily_limit")

    monkeypatch.setattr(service, "ai_usage", boom)
    status = control.status(NOW)
    check = next(c for c in status["system"] if c["id"] == "control-agent-settings")
    assert check["ok"] is None and "KeyError" in check["message"]
    assert status["level"] in (WARN, FAIL)  # visible, not swallowed
    assert all(p["ai"] == [] for p in status["processes"])


def test_status_survives_broken_state_files(tmp_path):
    s, control = control_for(tmp_path)
    (s.state_dir / "jobs.json").write_text('{"git-sync": "oops", "moodle-sync": []}', "utf-8")
    (s.state_dir / "pc-jobs.json").write_text("[1, 2]", "utf-8")
    (s.state_dir / "job-requests.json").write_text('{"git-sync": 5}', "utf-8")
    (s.state_dir / "worker.json").write_text('"nope"', "utf-8")
    (s.state_dir / "control-report.json").write_text('{"findings": "x"}', "utf-8")
    lifeos = s.vault_root / ".lifeos" / "agents"
    lifeos.mkdir(parents=True)
    (lifeos / "lifeos-manager.json").write_text('{"watch": ["a"]}', "utf-8")
    status = control.status(NOW)
    assert status["report"] is None and status["requests"] == {} and status["manager"] == {}
    assert light(status, "git-sync")[0] == FAIL  # no job on record: the worker is down


def test_a_number_that_is_no_number_is_reported_with_the_agent_file(tmp_path):
    from lifeos.agents.config import load_config
    from lifeos.core.vault import Vault

    s, control = control_for(tmp_path)
    agents = s.vault_root / "agents"
    agents.mkdir()
    (agents / "control-room.md").write_text(
        "---\nname: control-room\ntitle: Control room\nrunner: job\ndaily_limit: abc\n---\n",
        encoding="utf-8",
    )
    cfg = load_config(Vault.open(s.vault_root))
    assert cfg.agents == {}
    assert cfg.errors == ["agents/control-room.md: daily_limit must be a number, not 'abc'"]
    assert control.status(NOW)["level"]  # and the control room still loads


def test_requests_are_reported_per_process(tmp_path):
    s, control = control_for(
        tmp_path, github_token="t", github_repos="someone/repo", agents_interval=120,
        applications_project="someone/4",
    )  # fmt: skip
    asked = control.requests.request("agents")
    requests = control.status(NOW)["requests"]
    # two processes share the worker job "agents": both are queued
    assert requests == {"application-writer": asked, "agents": asked}


def test_web_status_is_kept_for_a_moment(tmp_path):
    from lifeos.core.vault import Vault
    from lifeos.modules.control.service import Control

    s = settings(tmp_path)
    control = Control(s, Vault.open(s.vault_root), s.state_dir / "jobs.json", cache_seconds=60)
    first = control.status()
    assert control.status() is first
    control.invalidate()
    assert control.status() is not first
    assert control.status(NOW) is not control.status(NOW)  # jobs asking with a time: fresh
