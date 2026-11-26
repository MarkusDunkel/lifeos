"""The PC tasks fast-forward their copy of the app before each run (lifeos.core.self_update)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from test_control import NOW, TOKEN, TZ, iso, settings

from lifeos.core.self_update import UpdateResult, update_app
from lifeos.main import create_app
from lifeos.modules.control.catalog import build_catalog
from lifeos.modules.control.health import OK, WARN, Sources, evaluate

BRANCH = "deploy"


def git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    )  # fmt: skip
    return out.stdout.strip()


def commit(repo: Path, name: str, text: str) -> str:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", f"change {name}")
    return git(repo, "rev-parse", "--short", "HEAD")


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path]:
    """``upstream`` (where releases land) and ``pc`` (the PC's clone on the deploy branch)."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init", "-q", "-b", BRANCH)
    commit(upstream, "backend/uv.lock", "v1\n")
    commit(upstream, "backend/src/code.py", "one\n")
    git(tmp_path, "clone", "-q", "-b", BRANCH, str(upstream), "pc")
    return upstream, tmp_path / "pc"


def test_fast_forwards_a_clean_checkout(repos):
    upstream, pc = repos
    new = commit(upstream, "backend/src/code.py", "two\n")
    result = update_app(pc, BRANCH, uv="uv-is-not-needed")
    assert result.status == "updated" and result.ok and result.changed
    assert result.version == new and "dependencies" not in result.message
    assert (pc / "backend/src/code.py").read_text() == "two\n"
    again = update_app(pc, BRANCH)
    assert again.status == "current" and again.version == new and not again.changed


def test_never_touches_work_in_progress(repos):
    upstream, pc = repos
    old = git(pc, "rev-parse", "--short", "HEAD")
    commit(upstream, "backend/src/code.py", "two\n")

    (pc / "backend/src/code.py").write_text("mine\n", encoding="utf-8")
    result = update_app(pc, BRANCH)
    assert result.status == "skipped" and not result.ok and "uncommitted" in result.message
    assert (pc / "backend/src/code.py").read_text() == "mine\n" and result.version == old
    git(pc, "checkout", "-q", "--", ".")

    git(pc, "checkout", "-q", "-b", "experiment")
    result = update_app(pc, BRANCH)
    assert result.status == "skipped" and "on experiment, not on deploy" in result.message
    git(pc, "checkout", "-q", BRANCH)

    commit(pc, "backend/src/other.py", "local\n")
    result = update_app(pc, BRANCH)
    assert result.status == "skipped" and "local commits" in result.message
    assert (pc / "backend/src/code.py").read_text() == "one\n"


def test_untracked_files_do_not_block(repos):
    upstream, pc = repos
    (pc / "notes.txt").write_text("scratch\n", encoding="utf-8")
    commit(upstream, "backend/src/code.py", "two\n")
    assert update_app(pc, BRANCH).status == "updated"


def test_dependencies_synced_or_rolled_back(repos, tmp_path):
    upstream, pc = repos
    old = git(pc, "rev-parse", "--short", "HEAD")
    commit(upstream, "backend/uv.lock", "v2\n")
    calls: list[tuple[list[str], Path]] = []
    real = __import__("lifeos.core.self_update", fromlist=["_run"])._run

    def failing_uv(cmd: list[str], cwd: Path) -> tuple[int, str]:
        if cmd[0] == "fake-uv":
            calls.append((cmd, cwd))
            return 1, "error: cannot replace a file in use"
        return real(cmd, cwd)

    result = update_app(pc, BRANCH, run=failing_uv, uv="fake-uv")
    assert calls == [
        (["fake-uv", "sync", "--frozen", "--extra", "pc", "--inexact"], pc / "backend")
    ]
    assert result.status == "failed" and result.version == old and "uv sync" in result.message
    assert (pc / "backend/uv.lock").read_text() == "v1\n"  # back on the old commit

    def working_uv(cmd: list[str], cwd: Path) -> tuple[int, str]:
        return (0, "") if cmd[0] == "fake-uv" else real(cmd, cwd)

    result = update_app(pc, BRANCH, run=working_uv, uv="fake-uv")
    assert result.status == "updated" and "dependencies synced" in result.message
    assert (pc / "backend/uv.lock").read_text() == "v2\n"


def test_fetch_failure_runs_on_old_code(repos, tmp_path):
    _, pc = repos
    git(pc, "remote", "set-url", "origin", str(tmp_path / "gone"))
    result = update_app(pc, BRANCH)
    assert result.status == "failed" and "fetch" in result.message and result.version


def test_not_a_checkout(tmp_path):
    assert update_app(tmp_path, BRANCH).status == "skipped"


def test_result_survives_the_restart():
    result = UpdateResult("updated", "updated a → b", "bbbbbbb")
    assert UpdateResult.from_env(result.to_env()) == result
    assert UpdateResult.from_env("not json") is None


def test_report_carries_the_commit(tmp_path):
    from lifeos.modules.control.reporter import ReporterSettings, send

    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={})

    cfg = ReporterSettings(control_server_url="https://lifeos.test", control_report_token=TOKEN)
    app = UpdateResult("skipped", "not updated: the app has uncommitted changes", "abc1234")
    assert send("teams-files", "ok", "1 new", 1.0, cfg, httpx.MockTransport(handler), app)
    assert bodies[0]["version"] == "abc1234" and bodies[0]["updateOk"] is False

    s = settings(tmp_path)
    client = TestClient(create_app(s))
    auth = {"Authorization": f"Bearer {TOKEN}"}
    r = client.post("/api/control/report/teams-files", json=bodies[0], headers=auth)
    assert r.status_code == 200
    assert r.json()["app"] == {"version": "abc1234", "update": app.message, "ok": False}
    old = {"status": "ok", "message": "", "duration": 1.0}  # a PC without the self-update
    r = client.post("/api/control/report/teams-files", json=old, headers=auth)
    assert "app" not in r.json()


def pc_entry(app: dict | None) -> dict:
    at = iso(timedelta(hours=1))
    entry = {"lastRun": at, "lastSuccess": at, "status": "ok", "message": "1 new"}
    return entry | ({"app": app} if app else {})


def teams(tmp_path: Path, app: dict | None, server: str | None = "def5678") -> dict:
    cat = build_catalog(settings(tmp_path))
    alive = {"git-sync": {"lastRun": iso(timedelta(minutes=2)),
                          "lastSuccess": iso(timedelta(minutes=2)), "status": "ok"}}  # fmt: skip
    src = Sources(jobs=alive, pc={"teams-files": pc_entry(app)}, server_version=server)
    result = evaluate(cat, src, NOW, TZ)
    return next(p for p in result["processes"] if p["id"] == "teams-files")


def test_control_room_shows_when_the_pc_falls_behind(tmp_path):
    current = teams(tmp_path, {"version": "def5678", "update": "up to date", "ok": True})
    assert current["level"] == OK and current["app"]["behind"] is False
    # just deployed, the PC has not run yet: visible, but no warning
    late = teams(tmp_path, {"version": "abc1234", "update": "up to date", "ok": True})
    assert late["level"] == OK and late["app"]["behind"] is True
    stuck = {"version": "abc1234", "update": "not updated: the app is on x, not on y",
             "ok": False}  # fmt: skip
    p = teams(tmp_path, stuck)
    assert p["level"] == WARN and "PC app not updated" in p["reason"]
    assert "server at def5678" in p["reason"]
    assert teams(tmp_path, None)["app"] is None


def test_cli_restarts_after_an_update(monkeypatch, tmp_path):
    from lifeos import cli
    from lifeos.core import self_update
    from lifeos.modules.control import reporter

    env_file = tmp_path / "pc.env"
    env_file.write_text("LIFEOS_DEPLOY_BRANCH=deploy\n", encoding="utf-8")
    branches: list[str] = []

    def fake_update(root: Path, branch: str) -> UpdateResult:
        branches.append(branch)
        return UpdateResult("updated", "updated a → b", "bbbbbbb")

    started: list[tuple[list[str], dict]] = []

    class Done:
        returncode = 7

    def fake_run(cmd, env):
        started.append((cmd, env))
        return Done()

    for name in (self_update.ENV_RESULT, "LIFEOS_DEPLOY_BRANCH", "LIFEOS_PC_SELF_UPDATE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(self_update, "update_app", fake_update)
    monkeypatch.setattr(subprocess, "run", fake_run)
    argv = ["claude-usage", "report", "--env-file", str(env_file)]
    assert cli.main(argv) == 7
    assert branches == ["deploy"]
    cmd, env = started[0]
    assert cmd == [sys.executable, "-m", "lifeos.cli", *argv]
    assert UpdateResult.from_env(env[self_update.ENV_RESULT]).version == "bbbbbbb"

    # the restarted process does not update again and reports what the first one did
    monkeypatch.setenv(self_update.ENV_RESULT, env[self_update.ENV_RESULT])
    reported: list[UpdateResult | None] = []
    monkeypatch.setattr(cli, "_claude_usage", lambda args: reported.append(reporter._app) or 0)
    assert cli.main(argv) == 0 and branches == ["deploy"]
    assert reported[0] is not None and reported[0].version == "bbbbbbb"
    reporter.set_app_update(None)
