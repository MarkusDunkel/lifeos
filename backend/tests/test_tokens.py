from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi.testclient import TestClient
from test_control import PASSWORD, TOKEN, settings

from lifeos.agents.llm import usage_usd
from lifeos.core import tokens
from lifeos.main import create_app
from lifeos.modules.control.catalog import build_catalog
from lifeos.modules.control.claude_usage import parse, report_usage
from lifeos.modules.control.reporter import ReporterSettings
from lifeos.modules.control.tokens import InteractiveUsage, series
from lifeos.modules.planning.planner import token_mode
from lifeos.worker.scheduler import Every, Job, JobResult, Scheduler

TZ = ZoneInfo("Europe/Vienna")
NOW = datetime(2026, 3, 10, 12, 30, tzinfo=UTC)


def ledger_line(state: Path, at: datetime, mode: str, n: int, process: str = "agents",
                detail: str = "") -> None:  # fmt: skip
    with (state / tokens.LEDGER_FILE).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": at.isoformat(), "process": process, "mode": mode,
                            "tokens": n, "detail": detail}) + "\n")  # fmt: skip


def test_record_uses_the_context(tmp_path):
    tokens.configure(tmp_path, "web", tokens.SEMI)
    try:
        tokens.record(100, "m", "api", 0.01)
        with tokens.context("agents", tokens.AUTO), tokens.context(detail="writer"):
            tokens.record(50, "m", "subscription")
        tokens.record(0, "m", "api")  # nothing processed, nothing booked
        lines = tokens.read()
    finally:
        tokens.configure(None)
    assert [(x["process"], x["mode"], x["tokens"]) for x in lines] == [
        ("web", "semi", 100),
        ("agents", "auto", 50),
    ]
    assert lines[1]["detail"] == "writer" and lines[0]["usd"] == 0.01


def test_api_usage_counts_tokens():
    class U:
        input_tokens, output_tokens = 10, 5
        cache_creation_input_tokens, cache_read_input_tokens = 20, 100
        server_tool_use = None

    assert usage_usd("claude-opus-5-5", U).tokens == 135


def test_scheduler_books_run_now_as_semi(tmp_path):
    from lifeos.modules.control.store import RunRequests

    modes: list[str] = []

    def job() -> JobResult:
        modes.append(tokens.current().mode + ":" + tokens.current().process)
        return JobResult("ok", "")

    requests = RunRequests(tmp_path)
    clock = [datetime.now(UTC) - timedelta(seconds=10)]
    sched = Scheduler([Job("x", Every(3600), job)], tmp_path / "jobs.json",
                      clock=lambda: clock[0], requests=requests)  # fmt: skip
    sched.tick()
    requests.request("x")
    clock[0] += timedelta(seconds=5)
    sched.tick()
    assert modes == ["auto:x", "semi:x"]


def test_planner_reasons():
    assert token_mode("daily") == "auto"
    assert token_mode("calendar changed, note in the calendar") == "auto"
    assert token_mode("calendar changed, wish") == "semi"


def test_series_buckets_modes_and_top(tmp_path):
    cat = build_catalog(settings(tmp_path))
    state = tmp_path / "state"
    ledger_line(state, NOW - timedelta(minutes=10), "auto", 1000, detail="writer")
    ledger_line(state, NOW - timedelta(hours=2), "semi", 300, process="web")
    ledger_line(state, NOW - timedelta(days=3), "auto", 999)  # outside 24 h
    InteractiveUsage(state).upsert(
        [
            {
                "source": "s1",
                "hour": (NOW - timedelta(hours=1)).isoformat(),
                "tokens": 500,
                "project": "LifeOS",
            }
        ]  # fmt: skip
    )
    out = series(cat, state, TZ, "24h", NOW)
    assert len(out["buckets"]) == 24 and out["bucketSeconds"] == 3600
    assert out["totals"] == {"auto": 1000, "semi": 300, "interactive": 500}
    last = out["buckets"][-1]
    assert last["auto"] == 1000 and datetime.fromisoformat(last["start"]) == NOW.replace(minute=0)
    first = out["top"][0]
    assert (first["id"], first["detail"], first["tokens"]) == ("agents/writer", "writer", 1000)
    assert {t["mode"] for t in out["top"]} == {"auto", "semi", "interactive"}

    week = series(cat, state, TZ, "7d", NOW)
    assert week["totals"]["auto"] == 1999 and len(week["buckets"]) == 28
    # 30 days: one bucket per local day, starting at midnight in Vienna
    month = series(cat, state, TZ, "30d", NOW)
    start = datetime.fromisoformat(month["buckets"][-1]["start"]).astimezone(TZ)
    assert (start.hour, start.minute) == (0, 0)


def test_interactive_upsert_replaces_a_bucket(tmp_path):
    usage = InteractiveUsage(tmp_path)
    bucket = {"source": "s1", "hour": "2026-03-10T11:42:00Z", "tokens": 10}
    usage.upsert([bucket])
    usage.upsert([{**bucket, "tokens": 25}])
    assert [b["tokens"] for b in usage.all().values()] == [25]


def transcript(path: Path, cwd: str = "/work/LifeOS") -> None:
    def assistant(mid: str, ts: str, out: int) -> dict:
        return {"type": "assistant", "cwd": cwd, "timestamp": ts, "sessionId": "s",
                "message": {"id": mid, "model": "claude-opus-5-5",
                            "usage": {"input_tokens": 1, "cache_read_input_tokens": 100,
                                      "output_tokens": out}}}  # fmt: skip

    lines = [
        {"type": "user", "cwd": cwd, "message": {"content": "hi"}},
        assistant("a", "2026-03-10T10:05:00Z", 9),
        assistant("a", "2026-03-10T10:05:01Z", 9),  # the same message streamed twice
        assistant("b", "2026-03-10T10:50:00Z", 19),
        assistant("c", "2026-03-10T11:10:00Z", 49),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")


def test_parse_transcript_per_hour_once_per_message(tmp_path):
    path = tmp_path / "proj" / "sess.jsonl"
    transcript(path)
    assert parse(path) == [
        {"source": "sess", "hour": "2026-03-10T10:00:00+00:00", "tokens": 230,
         "model": "claude-opus-5-5", "project": "LifeOS"},
        {"source": "sess", "hour": "2026-03-10T11:00:00+00:00", "tokens": 150,
         "model": "claude-opus-5-5", "project": "LifeOS"},
    ]  # fmt: skip
    own = tmp_path / "proj" / "headless.jsonl"
    transcript(own, cwd="/tmp/lifeos-claude-abc/work")
    assert parse(own) == []


def test_report_usage_sends_only_changed_files(tmp_path):
    root = tmp_path / "projects"
    transcript(root / "p" / "one.jsonl")
    transcript(root / "p" / "s" / "subagents" / "agent-x.jsonl")
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        sent.extend(json.loads(request.content)["buckets"])
        return httpx.Response(200, json={})

    cfg = ReporterSettings(control_server_url="https://lifeos.test", control_report_token=TOKEN)
    cursor = tmp_path / "cursor.json"
    transport = httpx.MockTransport(handler)
    assert "2 transcript(s)" in report_usage(cfg, cursor, root, transport)
    assert len(sent) == 4
    sent.clear()
    assert "0 transcript(s)" in report_usage(cfg, cursor, root, transport)
    assert sent == []


def test_api_tokens_endpoints(tmp_path):
    s = settings(tmp_path)
    app = create_app(s)
    try:
        machine = TestClient(app)
        body = {"buckets": [{"source": "s", "hour": datetime.now(UTC).isoformat(),
                             "tokens": 42, "project": "LifeOS"}]}  # fmt: skip
        url = "/api/control/report/claude-usage/tokens"
        assert machine.post(url, json=body).status_code == 401
        auth = {"Authorization": f"Bearer {TOKEN}"}
        assert machine.post(url, json=body, headers=auth).status_code == 200
        assert machine.get("/api/control/tokens", headers=auth).status_code == 401

        client = TestClient(app)
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        out = client.get("/api/control/tokens?range=24h").json()
        assert out["totals"]["interactive"] == 42
        assert client.get("/api/control/tokens?range=1y").status_code == 422
        status = client.get("/api/control/status").json()
        assert any(p["id"] == "claude-usage" for p in status["processes"])
    finally:
        tokens.configure(None)
