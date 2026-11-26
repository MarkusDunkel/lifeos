#!/usr/bin/env python3
"""LifeOS host deployer: runs the test and deploy requests the worker queues.

The worker (and the AI agents in it) has no Docker. When a ticket's pull request should go
live, the worker writes a request file; this script – run on the host every few seconds by
``lifeos-deployer.timer`` – handles it and writes the result next to it:

    <queue>/requests/<id>.json   {"id", "action": "test" | "deploy", ...}
    <queue>/results/<id>.json    {"id", "status": "ok" | "failed" | "rolled_back", "message", ...}

- ``test`` (``ref``, ``sha``): checks the commit out in a separate clone, refuses changes
  that contain private data (patterns in ``LIFEOS_PRIVATE_PATTERNS``, a file only on the
  host – the app repository is public), builds the image's ``test`` stage and runs the test
  suite in a container without volumes or secrets.
- ``deploy`` (``branch``, ``sha``): only for the configured deploy branch, and only when no
  agent run is in progress (a non-empty ``runs-*.json`` younger than an hour). Updates the
  app checkout to the branch, rebuilds and restarts the containers and waits for
  ``/api/health`` and – if the worker ran before – for the worker to start its jobs. If
  either does not come up, the previous commit is restored and deployed again
  (``rolled_back``). This runs on the host, outside of what the agents can break.

Standard library only. Settings from the environment (see ``ops/deployer.env.example``).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

APP = Path(os.environ.get("LIFEOS_APP_DIR", "/srv/lifeos/app"))
CI = Path(os.environ.get("LIFEOS_CI_DIR", "/srv/lifeos/ci"))
QUEUE = Path(os.environ.get("LIFEOS_DEPLOY_QUEUE", "/srv/lifeos/deploy/state/deploy"))
VAULT = Path(os.environ.get("LIFEOS_VAULT", "/srv/lifeos/vault"))
BRANCH = os.environ.get("LIFEOS_DEPLOY_BRANCH", "main")
HEALTH = os.environ.get("LIFEOS_HEALTH_URL", "http://127.0.0.1:8000/api/health")
HEALTH_SECONDS = int(os.environ.get("LIFEOS_HEALTH_SECONDS", "120"))
WORKER_SECONDS = int(os.environ.get("LIFEOS_WORKER_SECONDS", "240"))
PRIVATE = Path(os.environ.get("LIFEOS_PRIVATE_PATTERNS", "/srv/lifeos/deploy/private-patterns.txt"))
MARKER_MAX_AGE = 3600  # a run marker older than this is left over from a killed run
WORKER_STARTED = "lifeos.worker job "  # logged once per job when the worker starts

SHA = re.compile(r"[0-9a-f]{7,40}")
REF = re.compile(r"refs/pull/\d+/head|refs/heads/[A-Za-z0-9._/-]+")


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 1800) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"{' '.join(cmd[:3])} timed out"
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def tail(text: str, lines: int = 40) -> str:
    return "\n".join(text.splitlines()[-lines:])


def busy(now: float | None = None) -> bool:
    """An agent run is in progress (a deploy would restart the worker under it)."""
    now = time.time() if now is None else now
    for path in (VAULT / ".lifeos" / "agents").glob("runs-*.json"):
        try:
            if now - path.stat().st_mtime > MARKER_MAX_AGE:
                continue
            if json.loads(path.read_text(encoding="utf-8")):
                return True
        except (OSError, ValueError):
            continue
    return False


def private_hits(diff: str, patterns: list[str]) -> list[str]:
    """``file: pattern #n`` for every added line of a unified diff that contains one of the
    patterns (case-insensitive). The pattern itself is not repeated in the result."""
    wanted = [(i, p.lower()) for i, p in enumerate(patterns, 1)]
    hits: list[str] = []
    current = "?"
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else line[4:]
            continue
        if not line.startswith("+"):
            continue
        low = line.lower()
        hits += [f"{current}: pattern #{i}" for i, p in wanted if p in low]
    return list(dict.fromkeys(hits))


def load_patterns(path: Path | None = None) -> list[str]:
    try:
        lines = (path or PRIVATE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def private_check(sha: str) -> str | None:
    """Why the change from the deploy branch to ``sha`` must not go in, or None."""
    patterns = load_patterns()
    if not patterns:
        return None
    code, out = run(["git", "fetch", "--quiet", "origin", BRANCH], cwd=CI)
    if code != 0:
        return "could not fetch the deploy branch for the privacy check:\n" + tail(out)
    code, diff = run(["git", "diff", f"origin/{BRANCH}...{sha}"], cwd=CI)
    if code != 0:
        return "could not diff for the privacy check:\n" + tail(diff)
    hits = private_hits(diff, patterns)
    if not hits:
        return None
    return ("the change contains private data (patterns from the host's private list); "
            "the app repository is public:\n" + "\n".join(hits[:20]))  # fmt: skip


def healthy() -> bool:
    deadline = time.monotonic() + HEALTH_SECONDS
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(HEALTH, timeout=5) as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(3)
    return False


def test(req: dict[str, Any]) -> dict[str, Any]:
    ref, sha = str(req.get("ref", "")), str(req.get("sha", ""))
    if not REF.fullmatch(ref) or not SHA.fullmatch(sha):
        return {"status": "failed", "message": f"invalid test request: {ref} {sha}"}
    if not (CI / ".git").exists():
        code, url = run(["git", "-C", str(APP), "remote", "get-url", "origin"])
        if code != 0:
            return {"status": "failed", "message": "no origin in the app checkout"}
        code, out = run(["git", "clone", "--quiet", url, str(CI)])
        if code != 0:
            return {"status": "failed", "message": tail(out)}
    for cmd in (["git", "fetch", "--quiet", "origin", f"+{ref}:refs/lifeos/test"],
                ["git", "checkout", "--quiet", "--detach", sha]):  # fmt: skip
        code, out = run(cmd, cwd=CI)
        if code != 0:
            return {"status": "failed", "message": tail(out)}
    refused = private_check(sha)
    if refused:
        return {"status": "failed", "message": refused}
    code, out = run(["docker", "build", "--quiet", "--target", "test", "-t", "lifeos:ci",
                     "-f", "docker/Dockerfile", "."], cwd=CI)  # fmt: skip
    if code != 0:
        return {"status": "failed", "message": "build failed:\n" + tail(out)}
    code, out = run(["docker", "run", "--rm", "--network", "none", "lifeos:ci"], cwd=CI)
    return {"status": "ok" if code == 0 else "failed", "message": tail(out, 25), "sha": sha}


def worker_running() -> bool:
    code, out = run(["docker", "compose", "ps", "--status", "running", "--services"], cwd=APP)
    return code == 0 and "worker" in out.split()


def worker_started(since: str) -> bool:
    """The worker came up after ``since`` (an ISO time) and stays up: it logged its jobs,
    which needs the code to import and the job list to build."""
    deadline = time.monotonic() + WORKER_SECONDS
    while time.monotonic() < deadline:
        code, out = run(["docker", "compose", "logs", "--since", since, "worker"], cwd=APP)
        if code == 0 and WORKER_STARTED in out:
            time.sleep(10)  # does it stay up?
            return worker_running()
        time.sleep(5)
    return False


def deploy_head() -> tuple[int, str]:
    with_worker = worker_running()
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    code, out = run(["docker", "compose", "build", "--quiet"], cwd=APP)
    if code != 0:
        return code, "build failed:\n" + tail(out)
    code, out = run(["docker", "compose", "up", "-d"], cwd=APP)
    if code != 0:
        return code, "start failed:\n" + tail(out)
    if not healthy():
        return 1, "the app did not answer /api/health"
    if with_worker and not worker_started(since):
        _, logs = run(["docker", "compose", "logs", "--since", since, "worker"], cwd=APP)
        return 1, "the worker did not start its jobs:\n" + tail(logs, 20)
    return 0, ""


def deploy(req: dict[str, Any]) -> dict[str, Any] | None:
    branch, sha = str(req.get("branch", "")), str(req.get("sha", ""))
    if branch != BRANCH or not SHA.fullmatch(sha):
        return {"status": "failed", "message": f"only {BRANCH} is deployed, not {branch} {sha}"}
    if busy():
        return None  # try again on the next tick
    _, previous = run(["git", "rev-parse", "HEAD"], cwd=APP)
    for cmd in (["git", "fetch", "--quiet", "origin", BRANCH],
                ["git", "checkout", "--quiet", BRANCH],
                ["git", "reset", "--quiet", "--hard", f"origin/{BRANCH}"]):  # fmt: skip
        code, out = run(cmd, cwd=APP)
        if code != 0:
            return {"status": "failed", "message": tail(out)}
    code, _ = run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=APP)
    if code != 0:
        return {"status": "failed", "message": f"{sha} is not on {BRANCH}"}
    _, version = run(["git", "rev-parse", "--short", "HEAD"], cwd=APP)
    code, message = deploy_head()
    if code == 0:
        return {"status": "ok", "version": version, "message": f"{BRANCH} at {version}"}
    run(["git", "reset", "--quiet", "--hard", previous], cwd=APP)
    code_back, message_back = deploy_head()
    note = "" if code_back == 0 else f"\nrollback failed too: {message_back}"
    return {"status": "rolled_back", "version": previous[:7], "message": message + note}


def main() -> int:
    import fcntl  # Linux host only; without it at the top the module imports in tests

    (QUEUE / "requests").mkdir(parents=True, exist_ok=True)
    (QUEUE / "results").mkdir(parents=True, exist_ok=True)
    heartbeat = QUEUE / "heartbeat.json"  # the control room checks that the timer runs
    beat = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    # the commit the server runs: the control room compares the PC tasks' commit with it
    code, version = run(["git", "rev-parse", "--short", "HEAD"], cwd=APP, timeout=30)
    data = {"at": beat} | ({"version": version.strip()} if code == 0 else {})
    heartbeat.with_suffix(".tmp").write_text(json.dumps(data) + "\n", encoding="utf-8")
    heartbeat.with_suffix(".tmp").replace(heartbeat)
    with open(QUEUE / ".lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 0  # another run is still busy
        for path in sorted((QUEUE / "requests").glob("*.json"), key=lambda p: p.stat().st_mtime):
            result_path = QUEUE / "results" / path.name
            if result_path.exists():
                continue
            try:
                req = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                req = {}
            action = req.get("action")
            started = time.time()
            if action == "test":
                result = test(req)
            elif action == "deploy":
                result = deploy(req)
                if result is None:
                    break  # an agent is working; keep the order and retry later
            else:
                result = {"status": "failed", "message": f"unknown action {action!r}"}
            result.update(id=req.get("id", path.stem), action=action,
                          seconds=int(time.time() - started))  # fmt: skip
            tmp = result_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(result) + "\n", encoding="utf-8")
            tmp.replace(result_path)
            print(f"{path.stem} {action}: {result['status']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
