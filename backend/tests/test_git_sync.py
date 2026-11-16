from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from lifeos.core.config import Settings
from lifeos.core.git_sync import GitSync
from lifeos.core.vault_init import init_vault
from lifeos.main import create_app
from lifeos.worker.runner import run_worker

ID = ["-c", "user.name=Test", "-c", "user.email=test@example.org"]


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *ID, *args], cwd=cwd, capture_output=True, text=True, check=True)
    return proc.stdout


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A bare remote, the server's vault clone and a second clone (the laptop)."""
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    seed = tmp_path / "seed"
    git(tmp_path, "init", "-q", "-b", "main", str(seed))
    (seed / "note.md").write_text("first\n", encoding="utf-8")
    git(seed, "add", "--all")
    git(seed, "commit", "-q", "-m", "init")
    git(seed, "push", "-q", str(remote), "main")
    server = tmp_path / "server"
    laptop = tmp_path / "laptop"
    git(tmp_path, "clone", "-q", str(remote), str(server))
    git(tmp_path, "clone", "-q", str(remote), str(laptop))
    for clone in (server, laptop):
        git(clone, "config", "user.name", "Test")
        git(clone, "config", "user.email", "test@example.org")
    return remote, server, laptop


def log(repo: Path) -> list[str]:
    return git(repo, "log", "--format=%s").splitlines()


def test_commits_and_pushes_local_changes(repos, tmp_path: Path) -> None:
    _, server, laptop = repos
    (server / "progress.jsonl").write_text('{"id": "Q-1"}\n', encoding="utf-8")
    status_file = tmp_path / "state" / "git-sync.json"
    sync = GitSync(server, status_file)

    result = sync.run()

    assert result.status == "ok"
    assert result.committed == ["progress.jsonl"]
    assert result.pushed == 1
    git(laptop, "pull", "-q")
    assert (laptop / "progress.jsonl").exists()
    assert log(laptop)[0] == "lifeos: auto-sync (1 file(s))"
    assert sync.status()["status"] == "ok"
    assert sync.status()["lastSuccess"] == result.at


def test_pulls_remote_changes_and_is_idempotent(repos) -> None:
    _, server, laptop = repos
    (laptop / "mine.md").write_text("written on the laptop\n", encoding="utf-8")
    git(laptop, "add", "--all")
    git(laptop, "commit", "-q", "-m", "laptop edit")
    git(laptop, "push", "-q")
    (server / "app.jsonl").write_text("{}\n", encoding="utf-8")
    sync = GitSync(server)

    first = sync.run()
    second = sync.run()

    assert first.status == "ok"
    assert (first.pulled, first.pushed) == (1, 1)
    assert (server / "mine.md").exists()
    assert second.status == "ok"
    assert second.message == "up to date"
    git(laptop, "pull", "-q")
    assert (laptop / "app.jsonl").exists()


def test_conflict_aborts_rebase_and_keeps_both_sides(repos, tmp_path: Path) -> None:
    _, server, laptop = repos
    (laptop / "note.md").write_text("laptop version\n", encoding="utf-8")
    git(laptop, "commit", "-q", "-am", "laptop edit")
    git(laptop, "push", "-q")
    (server / "note.md").write_text("server version\n", encoding="utf-8")
    sync = GitSync(server, tmp_path / "git-sync.json")

    result = sync.run()

    assert result.status == "conflict"
    assert (server / "note.md").read_text(encoding="utf-8") == "server version\n"
    assert not (server / ".git" / "rebase-merge").exists()
    assert git(server, "status", "--porcelain") == ""
    # The remote still has the laptop version; nothing was pushed over it.
    git(laptop, "pull", "-q")
    assert (laptop / "note.md").read_text(encoding="utf-8") == "laptop version\n"
    assert sync.status()["status"] == "conflict"
    assert sync.status()["lastSuccess"] is None


def test_skips_without_git_or_remote(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert GitSync(plain).run().status == "skipped"

    local_only = tmp_path / "local"
    git(tmp_path, "init", "-q", "-b", "main", str(local_only))
    result = GitSync(local_only).run()
    assert result.status == "skipped"
    assert "no remote" in result.message


def test_reports_unreachable_remote_as_error(repos) -> None:
    _, server, _ = repos
    git(server, "remote", "set-url", "origin", str(server.parent / "missing.git"))
    (server / "x.md").write_text("x\n", encoding="utf-8")

    result = GitSync(server).run()

    assert result.status == "error"
    assert "fetch" in result.message


def test_worker_runs_sync_until_stopped(repos, tmp_path: Path) -> None:
    _, server, laptop = repos
    (server / "from-worker.md").write_text("hi\n", encoding="utf-8")
    settings = Settings(
        vault_root=server,
        session_secret="x" * 40,
        state_dir=tmp_path / "state",
        git_sync_interval=3600,
    )
    stop = threading.Event()
    thread = threading.Thread(target=run_worker, args=(settings, stop))
    thread.start()
    status_file = tmp_path / "state" / "git-sync.json"
    for _ in range(100):
        if status_file.exists():
            break
        threading.Event().wait(0.1)
    stop.set()
    thread.join(timeout=10)

    assert not thread.is_alive()
    git(laptop, "pull", "-q")
    assert (laptop / "from-worker.md").exists()


def test_api_status_and_run(repos, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    _, server, _ = repos
    settings = Settings(
        vault_root=server,
        session_secret="x" * 40,
        auth_disabled=True,
        state_dir=tmp_path / "state",
        git_sync_interval=300,
    )
    client = TestClient(create_app(settings))

    assert client.get("/api/vault-sync/status").json()["status"] == "never"
    (server / "y.md").write_text("y\n", encoding="utf-8")
    ran = client.post("/api/vault-sync/run").json()
    assert ran["status"] == "ok"
    status = client.get("/api/vault-sync/status").json()
    assert status["enabled"] is True
    assert status["status"] == "ok"


def test_api_run_is_refused_when_disabled(client) -> None:
    assert client.get("/api/vault-sync/status").json()["enabled"] is False
    assert client.post("/api/vault-sync/run").status_code == 400


def test_vault_sync_api_requires_login(anon) -> None:
    assert anon.get("/api/vault-sync/status").status_code == 401


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
def test_pre_push_hook_allows_only_configured_urls(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    init_vault(vault)
    hook = (vault / ".git/hooks/pre-push").as_posix()

    def push_allowed(url: str) -> bool:
        proc = subprocess.run(["sh", hook, "origin", url], cwd=vault, capture_output=True)
        return proc.returncode == 0

    assert not push_allowed("https://example.org/any.git")
    git(vault, "config", "--add", "lifeos.allowedPushUrl", "git@example.org:me/vault.git")
    assert push_allowed("git@example.org:me/vault.git")
    assert not push_allowed("https://example.org/other.git")
