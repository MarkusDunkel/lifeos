"""Releases: what the AI made live for a ticket is tested, merged, deployed – and undone."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_agents import FakeGit, mirrored, team
from test_assistant import APPS, ASSISTANT, TASKS, WRITER, GitHub

from lifeos.agents.config import load_config
from lifeos.agents.releases import DeployQueue, Releases
from lifeos.agents.team import RunContext, RunResult
from lifeos.core.git_sync import GitError, GitSync
from lifeos.core.vault import Vault

APP_REPO = "someone/app"
PR_URL = f"https://github.com/{APP_REPO}/pull/7"
TASKS_WITH_DISCARD = TASKS.replace("responsible_field: Responsible",
                                   "responsible_field: Responsible\n  discard: New")  # fmt: skip


class ReleaseGitHub(GitHub):
    def __init__(self) -> None:
        super().__init__()
        self.prs: dict[int, dict[str, Any]] = {
            7: {"id": "PR7", "number": 7, "url": PR_URL, "state": "OPEN", "title": "Wider gap",
                "baseRefName": "main", "headRefName": "gap", "headRefOid": "a" * 40,
                "headRepository": {"nameWithOwner": APP_REPO}, "mergeCommit": None},
        }  # fmt: skip
        self.merged: list[tuple[str, str, str]] = []

    def pull_request(self, repo: str, number: int) -> dict[str, Any] | None:
        assert repo == APP_REPO
        return self.prs.get(number)

    def merge_pull_request(self, pr_id: str, head_oid: str, headline: str, body: str) -> str:
        self.merged.append((pr_id, headline, body))
        return "m" * 40


class ReleaseGit(FakeGit):
    def __init__(self) -> None:
        super().__init__()
        self.reverted: list[tuple[str, str]] = []
        self.pushed = 0

    def head(self) -> str:
        return f"v{len(self.commits)}"

    def revert(self, commit: str, message: str) -> str:
        self.reverted.append((commit, message))
        return f"r-{commit}"

    def run(self):
        self.pushed += 1


class FakeApp:
    repo = APP_REPO
    branch = "main"

    def __init__(self) -> None:
        self.reverted: list[tuple[str, str]] = []

    def revert_merge(self, merge: str, message: str) -> str:
        self.reverted.append((merge, message))
        return "b" * 40


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    root = tmp_path / "vault"
    for rel, text in {
        "boards/tasks.md": TASKS_WITH_DISCARD,
        "boards/apps.md": APPS,
        "agents/writer.md": WRITER,
        "agents/assistant.md": ASSISTANT,
        "ready/knowledge/Note.md": "# Note\n",
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return Vault.open(root)


@pytest.fixture
def gh() -> ReleaseGitHub:
    fake = ReleaseGitHub()
    fake.add_project(3, ["New", "Todo", "In Progress", "Review", "Done"])
    fake.add_project(4, ["New", "Active", "In Review"])
    return fake


@pytest.fixture
def queue(tmp_path: Path) -> DeployQueue:
    return DeployQueue(tmp_path / "deploy")


def answer(queue: DeployQueue, status: str = "ok", **extra: Any) -> dict[str, Any]:
    """The deployer's answer to the newest open request."""
    requests = sorted((queue.root / "requests").glob("*.json"), key=lambda p: p.stat().st_mtime)
    open_ = [p for p in requests if not (queue.root / "results" / p.name).exists()]
    req = json.loads(open_[-1].read_text(encoding="utf-8"))
    (queue.root / "results").mkdir(parents=True, exist_ok=True)
    result = {"id": req["id"], "status": status, **extra}
    (queue.root / "results" / f"{req['id']}.json").write_text(json.dumps(result), "utf-8")
    return req


def work(gh, vault, git, queue, app, prs=(PR_URL,)) -> str:
    """The assistant finishes an approved ticket with a vault change and a pull request."""
    mirrored(gh, vault)
    item = gh.ticket(3, "Wider paragraph gap", Status="Todo", Responsible="AI: KI-Assistent")

    def assistant(ctx: RunContext) -> RunResult:
        vault.resolve("ready/knowledge/Note.md").write_text("# Note v2\n", encoding="utf-8")
        return RunResult(report="Done.", summary="gap", paths=["ready/knowledge/Note.md"],
                         pull_requests=list(prs))  # fmt: skip

    cfg = load_config(vault)
    releases = Releases(gh, vault, cfg, git, queue, app)
    team(gh, vault, "tasks", {"assistant": assistant}, git=git, releases=releases).poll()
    return item


def releases(gh, vault, git, queue, app) -> Releases:
    return Releases(gh, vault, load_config(vault), git, queue, app)


def test_report_links_the_vault_commit_and_queues_the_pull_request(gh, vault, queue) -> None:
    git = ReleaseGit()
    item = work(gh, vault, git, queue, FakeApp())
    report = gh.comments[-1][1]
    assert (
        "**Live in the vault:** commit [`v1`](https://github.com/someone/vault/commit/v1)" in report
    )
    assert f"pull request [#7]({PR_URL}) – tests, merge and deployment run now" in report
    assert '<!-- lifeos-release: {"vault": ["v1"], "prs": [7]} -->' in report
    assert ("PR7", "Belongs to someone/board#1.") in gh.comments
    req = json.loads(next((queue.root / "requests").glob("*.json")).read_text("utf-8"))
    assert req["action"] == "test" and req["ref"] == "refs/pull/7/head" and req["sha"] == "a" * 40
    assert gh.values(item)["Status"] == "Review"


def test_tests_ok_merge_then_deploy_then_live(gh, vault, queue) -> None:
    git, app = ReleaseGit(), FakeApp()
    work(gh, vault, git, queue, app)
    r = releases(gh, vault, git, queue, app)
    assert r.poll() == []  # the deployer has not answered yet
    answer(queue, "ok")
    assert r.poll() == ["someone/board#1: PR #7 merged, deploying"]
    assert gh.merged == [("PR7", "Merge #7: Wider gap", "Refs someone/board#1")]
    deploy = answer(queue, "ok", version="mmmmmmm")
    assert deploy["action"] == "deploy" and deploy["sha"] == "m" * 40 and deploy["branch"] == "main"
    assert r.poll() == ["someone/board#1: PR #7 live"]
    assert "🚀 **Live:** [#7]" in gh.comments[-1][1] and "`mmmmmmm`" in gh.comments[-1][1]
    assert r.poll() == []


def test_failed_tests_stop_the_release(gh, vault, queue) -> None:
    git, app = ReleaseGit(), FakeApp()
    work(gh, vault, git, queue, app)
    answer(queue, "failed", message="1 failed: test_letter")
    events = releases(gh, vault, git, queue, app).poll()
    assert events == ["someone/board#1: tests failed for PR #7"]
    assert gh.merged == [] and "1 failed: test_letter" in gh.comments[-1][1]
    # nothing is live: not ready for review, but blocked in the working column
    item = next(iter(gh.items))
    assert gh.values(item)["Status"] == "In Progress" and gh.values(item)["AI"] == "blocked"
    assert "moved the ticket back to **In Progress**" in gh.comments[-1][1]


def test_failed_release_leaves_a_ticket_the_owner_moved_alone(gh, vault, queue) -> None:
    git, app = ReleaseGit(), FakeApp()
    item = work(gh, vault, git, queue, app)
    gh.values(item)["Status"] = "Done"  # the owner was faster
    answer(queue, "failed", message="1 failed")
    releases(gh, vault, git, queue, app).poll()
    assert gh.values(item)["Status"] == "Done" and "AI" not in gh.values(item)
    assert "moved the ticket back" not in gh.comments[-1][1]


def test_failed_deployment_undoes_the_merge_on_the_branch(gh, vault, queue) -> None:
    git, app = ReleaseGit(), FakeApp()
    work(gh, vault, git, queue, app)
    r = releases(gh, vault, git, queue, app)
    answer(queue, "ok")
    r.poll()
    answer(queue, "rolled_back", message="the app did not answer /api/health")
    assert r.poll() == ["someone/board#1: deployment of PR #7 failed"]
    assert app.reverted[0][0] == "m" * 40 and "Refs someone/board#1" in app.reverted[0][1]
    assert (
        "Deployment failed" in gh.comments[-1][1] and "undone on the branch" in gh.comments[-1][1]
    )
    assert gh.values(next(iter(gh.items)))["AI"] == "blocked"


def test_moving_to_new_undoes_vault_and_app_once(gh, vault, queue) -> None:
    git, app = ReleaseGit(), FakeApp()
    item = work(gh, vault, git, queue, app)
    r = releases(gh, vault, git, queue, app)
    answer(queue, "ok")
    r.poll()
    answer(queue, "ok", version="mmmmmmm")
    r.poll()
    gh.values(item)["Status"] = "New"  # the owner discards the ticket
    assert r.poll() == ["someone/board#1: undone"]
    assert git.reverted == [
        ("v1", "Revert v1: Wider paragraph gap discarded\n\nRefs someone/board#1\n")
    ]
    assert app.reverted[0][0] == "m" * 40
    redeploy = answer(queue, "ok")
    assert redeploy["action"] == "deploy" and redeploy["sha"] == "b" * 40
    undo = gh.comments[-1][1]
    assert "↩️ **Undone**" in undo and "commit [`v1`]" in undo and "[`r-v1`]" in undo
    assert r.poll() == []  # only once
    assert len(git.reverted) == 1


def test_discard_before_merge_cancels_the_pull_request(gh, vault, queue) -> None:
    git, app = ReleaseGit(), FakeApp()
    item = work(gh, vault, git, queue, app)
    r = releases(gh, vault, git, queue, app)
    gh.values(item)["Status"] = "New"
    r.poll()
    assert "was not merged yet" in gh.comments[-1][1]
    answer(queue, "ok")  # the tests finish afterwards
    assert r.poll() == []  # cancelled pull requests are not advanced
    assert gh.merged == []


def test_new_tickets_without_a_release_are_left_alone(gh, vault, queue) -> None:
    mirrored(gh, vault)
    gh.ticket(3, "A proposal", Status="New")
    assert releases(gh, vault, ReleaseGit(), queue, FakeApp()).poll() == []


def test_pull_request_against_another_branch_is_not_released(gh, vault, queue) -> None:
    gh.prs[7]["baseRefName"] = "develop"
    work(gh, vault, ReleaseGit(), queue, FakeApp())
    assert (
        "is not released automatically: it targets `develop` instead of `main`"
        in (gh.comments[-1][1])
    )
    assert not (queue.root / "requests").exists()


def test_without_app_repo_pull_requests_are_only_listed(gh, vault) -> None:
    work(gh, vault, ReleaseGit(), None, None)
    assert "releases are not set up" in gh.comments[-1][1]


def test_boards_without_discard_only_link_the_commit(gh, vault) -> None:
    vault.resolve("boards/tasks.md").write_text(TASKS, encoding="utf-8")
    git = ReleaseGit()
    work(gh, vault, git, None, None)
    report = gh.comments[-1][1]
    assert "**Changes:** commit [`v1`](https://github.com/someone/vault/commit/v1)" in report
    assert "lifeos-release" not in report


def test_git_revert_in_a_real_vault(tmp_path: Path) -> None:
    root = tmp_path / "v"
    root.mkdir()

    def git(*args: str) -> str:
        cmd = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.org"]
        return subprocess.run([*cmd, *args], check=True, capture_output=True, text=True).stdout

    git("init", "-q")
    (root / "a.md").write_text("one\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "base")
    (root / "a.md").write_text("two\n", encoding="utf-8")
    git("commit", "-qam", "change")
    change = git("rev-parse", "--short", "HEAD").strip()
    (root / "b.md").write_text("pending edit\n", encoding="utf-8")  # the owner's, uncommitted
    sync = GitSync(root)
    sync._identity = lambda: ["-c", "user.name=t", "-c", "user.email=t@example.org"]  # type: ignore[method-assign]
    revert = sync.revert(change, "Revert change")
    assert (root / "a.md").read_text(encoding="utf-8") == "one\n"
    assert (root / "b.md").read_text(encoding="utf-8") == "pending edit\n"
    assert revert == sync.head()

    (root / "a.md").write_text("three\n", encoding="utf-8")
    git("commit", "-qam", "later change on the same line")
    with pytest.raises(GitError, match="revert of"):
        sync.revert(change, "Revert again")  # conflicts with the later change
    assert (root / "a.md").read_text(encoding="utf-8") == "three\n"  # nothing half-done
    assert (root / "b.md").read_text(encoding="utf-8") == "pending edit\n"


def test_a_deleted_ticket_does_not_stall_the_release(gh, vault, queue) -> None:
    """Commenting on a deleted ticket fails; the release still advances and is saved, and a
    pull request merged in an unsaved round is not merged again."""
    git, app = ReleaseGit(), FakeApp()
    work(gh, vault, git, queue, app)
    r = releases(gh, vault, git, queue, app)
    answer(queue, "ok")
    gh.prs[7].update(state="MERGED", mergeCommit={"oid": "c" * 40})

    def gone(subject_id: str, body: str) -> None:
        raise RuntimeError("Could not resolve to a node")

    gh.add_comment = gone  # type: ignore[method-assign]
    assert r.poll() == ["someone/board#1: PR #7 merged, deploying"]
    assert gh.merged == []
    deploy = answer(queue, "ok")
    assert deploy["sha"] == "c" * 40
    assert r.poll() == ["someone/board#1: PR #7 live"]
    assert r.poll() == []
