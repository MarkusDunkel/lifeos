"""The host deployer (ops/deployer.py): run markers, the privacy check, the worker check."""

from __future__ import annotations

import importlib.util
import json
import os
import time
from pathlib import Path

import pytest

DEPLOYER = Path(__file__).resolve().parents[2] / "ops" / "deployer.py"
if not DEPLOYER.is_file():  # the test image holds only the backend
    pytest.skip("ops/deployer.py is not part of this checkout", allow_module_level=True)


@pytest.fixture
def deployer():
    spec = importlib.util.spec_from_file_location("deployer", DEPLOYER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_run_marker_blocks_deploys_unless_it_is_left_over(deployer, tmp_path) -> None:
    deployer.VAULT = tmp_path
    agents = tmp_path / ".lifeos" / "agents"
    agents.mkdir(parents=True)
    marker = agents / "runs-lifeos-manager.json"
    marker.write_text(json.dumps({}), "utf-8")
    assert not deployer.busy()
    marker.write_text(json.dumps({"lifeos-manager": "2026-11-26T12:00:00+00:00"}), "utf-8")
    assert deployer.busy()
    old = time.time() - 2 * 3600
    os.utime(marker, (old, old))  # a killed run left it behind
    assert not deployer.busy()


def test_private_data_in_added_lines_is_found_without_repeating_it(deployer, tmp_path) -> None:
    diff = "\n".join([
        "diff --git a/src/x.py b/src/x.py",
        "--- a/src/x.py",
        "+++ b/src/x.py",
        "@@ -1 +1,2 @@",
        "-old = 'Jane Example'",  # removed lines do not count
        "+name = 'Jane Example'",
        "+path = 'C:/Users/jane'",
        " unchanged = 'jane@example.org'",
    ])  # fmt: skip
    patterns = ["jane example", "jane@example.org", "C:/Users/jane"]
    hits = deployer.private_hits(diff, patterns)
    assert hits == ["src/x.py: pattern #1", "src/x.py: pattern #3"]
    assert not any("jane" in h.lower() for h in hits)

    listing = tmp_path / "private-patterns.txt"
    listing.write_text("# names\nJane Example\n\n  jane@example.org  \n", "utf-8")
    assert deployer.load_patterns(listing) == ["Jane Example", "jane@example.org"]
    assert deployer.load_patterns(tmp_path / "missing.txt") == []


def test_deploy_rolls_back_when_the_worker_does_not_start(deployer, monkeypatch) -> None:
    calls: list[list[str]] = []
    logs = {"text": ""}

    def run(cmd, cwd=None, timeout=1800):
        calls.append(cmd)
        if cmd[:3] == ["docker", "compose", "ps"]:
            return 0, "web\nworker\n"
        if cmd[:3] == ["docker", "compose", "logs"]:
            return 0, logs["text"]
        return 0, ""

    monkeypatch.setattr(deployer, "run", run)
    monkeypatch.setattr(deployer, "healthy", lambda: True)
    monkeypatch.setattr(deployer.time, "sleep", lambda s: None)
    monkeypatch.setattr(deployer, "WORKER_SECONDS", 0)
    code, message = deployer.deploy_head()
    assert code == 1 and "worker did not start" in message

    monkeypatch.setattr(deployer, "WORKER_SECONDS", 60)
    logs["text"] = "worker-1 | lifeos.worker job agents: Every(seconds=120)"
    assert deployer.deploy_head() == (0, "")
