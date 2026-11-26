"""Releasing what the AI made for a ticket – and undoing it when the owner discards it.

On boards with a discard column (``ai.discard`` in the contract, e.g. ``New``) every
ticket an agent hands over to review is live:

- vault changes are live as soon as the team committed them,
- pull requests the agent opened in the app repository are tested, merged into the deploy
  branch and deployed – by the host deployer (``ops/deployer.sh``), because the worker has
  no Docker. The worker and the deployer talk through files in ``<state>/deploy``:
  ``requests/<id>.json`` and ``results/<id>.json``.

What went live is remembered per ticket (``.lifeos/agents/releases.json`` and an invisible
marker in the report). A ticket in the discard column whose record is still ``live`` is
undone: every vault commit with ``git revert``, every merged pull request with a revert
commit on the deploy branch that is then deployed. The undo runs once; what cannot be
undone automatically is reported on the ticket. Every commit names the ticket
(``Refs owner/repo#N``), so all of them show up in its timeline.

When the tests, the merge or the deployment of a pull request fail, nothing went live: the
ticket is not ready for review. If it still sits in the review column, it goes back to the
working column with ``AI = blocked`` and the failure as the reason – a block like any other,
which the LifeOS manager can look at and lift, or the owner.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lifeos.agents import comments
from lifeos.agents.comments import Texts
from lifeos.agents.config import AI_FIELD, BLOCKED, AgentConfig, AgentDef, BoardContract
from lifeos.agents.mirror import STATUS, split_project
from lifeos.agents.team import Ticket, tickets
from lifeos.connectors.github.client import GitHubError, item_values
from lifeos.core.vault import STATE, Vault, write_atomic

log = logging.getLogger("lifeos.agents.releases")

STATE_FILE = f"{STATE}/agents/releases.json"
PR_URL = re.compile(r"github\.com/([^/\s]+/[^/\s]+)/pull/(\d+)")
GIT_TIMEOUT = 300


class DeployQueue:
    """Requests for the host deployer and its results, as JSON files."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def request(self, action: str, **data: Any) -> str:
        request_id = uuid.uuid4().hex[:12]
        payload = {"id": request_id, "action": action,
                   "created": datetime.now(UTC).isoformat(), **data}  # fmt: skip
        write_atomic(self.root / "requests" / f"{request_id}.json", json.dumps(payload) + "\n")
        return request_id

    def result(self, request_id: str) -> dict[str, Any] | None:
        path = self.root / "results" / f"{request_id}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None


class AppRepo:
    """Revert commits on the deploy branch of the app repository (temporary clone)."""

    def __init__(self, repo: str, branch: str, token: str) -> None:
        self.repo = repo
        self.branch = branch
        self.token = token

    def _git(self, cwd: Path | None, *args: str) -> str:
        auth = base64.b64encode(f"x-access-token:{self.token}".encode()).decode()
        proc = subprocess.run(
            ["git", "-c", f"http.extraHeader=Authorization: Basic {auth}",
             "-c", "user.name=LifeOS", "-c", "user.email=lifeos@localhost", *args],
            cwd=cwd, capture_output=True, text=True, timeout=GIT_TIMEOUT,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )  # fmt: skip
        if proc.returncode != 0:
            lines = [ln for ln in (proc.stderr or proc.stdout).splitlines() if ln.strip()]
            raise RuntimeError(f"git {args[0]}: {lines[-1] if lines else proc.returncode}")
        return proc.stdout.strip()

    def revert_merge(self, merge: str, message: str) -> str:
        """Undo a merge commit on the deploy branch; returns the new head of the branch."""
        with tempfile.TemporaryDirectory(prefix="lifeos-app-", ignore_cleanup_errors=True) as tmp:
            work = Path(tmp) / "app"
            url = f"https://github.com/{self.repo}.git"
            self._git(None, "clone", "--quiet", "--branch", self.branch, url, str(work))
            self._git(work, "revert", "--no-edit", "--no-commit", "-m", "1", merge)
            self._git(work, "commit", "--quiet", "-m", message)
            self._git(work, "push", "--quiet", "origin", f"HEAD:refs/heads/{self.branch}")
            return self._git(work, "rev-parse", "HEAD")


class Releases:
    def __init__(
        self,
        gh: Any,
        vault: Vault,
        cfg: AgentConfig,
        git: Any | None,
        queue: DeployQueue | None,
        app: AppRepo | None,
        texts: Texts | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.gh = gh
        self.vault = vault
        self.cfg = cfg
        self.git = git
        self.queue = queue
        self.app = app
        self.t = texts or Texts.load(vault)
        self.clock = clock
        self.state_file = vault.resolve(STATE_FILE)

    # -- state --------------------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, state: dict[str, Any]) -> None:
        write_atomic(self.state_file, json.dumps(state, indent=1) + "\n")

    # -- links ----------------------------------------------------------------------------

    def _vault_link(self, commit: str) -> str:
        base = self.git.web_url() if self.git else None
        return f"[`{commit[:7]}`]({base}/commit/{commit})" if base else f"`{commit[:7]}`"

    def _app_link(self, commit: str) -> str:
        if self.app is None:
            return f"`{commit[:7]}`"
        return f"[`{commit[:7]}`](https://github.com/{self.app.repo}/commit/{commit})"

    @staticmethod
    def _pr_link(pr: dict[str, Any]) -> str:
        return f"[#{pr['number']}]({pr['url']})"

    # -- after a run: remember and start the release ---------------------------------------

    def record(
        self,
        agent: AgentDef,
        contract: BoardContract,
        ticket: Ticket,
        vault_commit: str | None,
        pull_requests: list[str],
    ) -> str:
        """Remember what went live for ``ticket``; returns the report lines."""
        state = self._load()
        entry = state.get(ticket.issue_id) or {"vault": [], "prs": []}
        entry.update(number=ticket.number, ref=ticket.ref, url=ticket.url, title=ticket.title,
                     board=contract.board, agent=agent.name, status="live")  # fmt: skip
        lines: list[str] = []
        if vault_commit:
            entry["vault"].append(vault_commit)
            lines.append(self.t("release_vault", link=self._vault_link(vault_commit)))
        for url in dict.fromkeys(pull_requests):
            lines.append(self._start_pr(entry, ticket, url))
        state[ticket.issue_id] = entry
        self._save(state)
        marker = json.dumps({"vault": entry["vault"], "prs": [p["number"] for p in entry["prs"]]})
        return "\n\n" + "\n".join(lines) + f"\n<!-- lifeos-release: {marker} -->" if lines else ""

    def _start_pr(self, entry: dict[str, Any], ticket: Ticket, url: str) -> str:
        match = PR_URL.search(url)
        if self.app is None or self.queue is None:
            return self.t("release_pr_skipped", link=url, reason=self.t("release_not_configured"))
        if not match or match.group(1).lower() != self.app.repo.lower():
            return self.t("release_pr_skipped", link=url, reason=self.t("release_other_repo"))
        number = int(match.group(2))
        pr = self.gh.pull_request(self.app.repo, number)
        reason = None
        if pr is None:
            reason = self.t("release_pr_missing")
        elif pr.get("state") != "OPEN":
            reason = self.t("release_pr_not_open", state=pr.get("state"))
        elif pr.get("baseRefName") != self.app.branch:
            reason = self.t("release_pr_wrong_base", base=pr.get("baseRefName"),
                            branch=self.app.branch)  # fmt: skip
        elif ((pr.get("headRepository") or {}).get("nameWithOwner") or "").lower() != (
            self.app.repo.lower()
        ):
            reason = self.t("release_other_repo")
        if reason or pr is None:
            return self.t("release_pr_skipped", link=url, reason=reason)
        request = self.queue.request("test", ref=f"refs/pull/{number}/head",
                                     sha=pr["headRefOid"], ticket=ticket.ref)  # fmt: skip
        item = {"number": number, "url": pr["url"], "id": pr["id"], "head": pr["headRefOid"],
                "title": pr.get("title") or "", "stage": "test", "request": request}  # fmt: skip
        entry["prs"] = [p for p in entry["prs"] if p["number"] != number] + [item]
        try:  # cross-link: the pull request then shows up in the ticket's timeline
            self.gh.add_comment(pr["id"], self.t("release_pr_belongs", ref=ticket.ref))
        except Exception:
            log.exception("could not link pull request #%s to %s", number, ticket.ref)
        return self.t("release_pr_queued", link=self._pr_link(item))

    # -- every round ------------------------------------------------------------------------

    def poll(self) -> list[str]:
        state = self._load()
        events: list[str] = []
        if self.queue is not None:
            for issue_id, entry in state.items():
                for pr in entry.get("prs", []):
                    event = self._advance(issue_id, entry, pr)
                    if event:
                        events.append(event)
                        self._save(state)
        events += self._discards(state)
        self._save(state)
        return events

    def _say(self, entry: dict[str, Any], issue_id: str, text: str) -> None:
        body = "\n".join([comments.marker(entry.get("agent") or "lifeos"), text])
        self.gh.add_comment(issue_id, body)

    def _back_to_work(self, issue_id: str, entry: dict[str, Any]) -> str:
        """A failed release: back from review to the working column, blocked. Returns the
        line for the comment (empty when the owner already moved the ticket)."""
        contract = self.cfg.boards.get(entry.get("board") or "")
        if contract is None:
            return ""
        try:
            owner, number = split_project(contract.project)
            project = self.gh.project(owner, number)
            item = next((n for n in self.gh.project_items(project.id)
                         if (n.get("content") or {}).get("id") == issue_id), None)  # fmt: skip
            if item is None or AI_FIELD not in project.fields:
                return ""
            if item_values(item, project.fields).get(STATUS) != contract.ai.review:
                return ""  # the owner moved it meanwhile: leave it there
            self.gh.set_field(project.id, item["id"], project.fields[STATUS], contract.ai.working)
            self.gh.set_field(project.id, item["id"], project.fields[AI_FIELD], BLOCKED)
        except (GitHubError, KeyError, ValueError) as exc:
            log.warning("could not move %s back to work: %s", entry.get("ref"), exc)
            return ""
        return "\n\n" + self.t("release_back_to_work", column=contract.ai.working)

    def _advance(self, issue_id: str, entry: dict[str, Any], pr: dict[str, Any]) -> str | None:
        if pr.get("stage") not in ("test", "deploy") or not pr.get("request"):
            return None
        assert self.queue is not None
        result = self.queue.result(pr["request"])
        if result is None:
            return None
        ok = result.get("status") == "ok"
        message = str(result.get("message") or "")[-1500:]
        link = self._pr_link(pr)
        ref = entry.get("ref", "")
        if pr["stage"] == "test":
            if not ok:
                pr["stage"] = "failed"
                self._say(entry, issue_id, self.t("release_test_failed", link=link,
                                                  message=message)
                          + self._back_to_work(issue_id, entry))  # fmt: skip
                return f"{ref}: tests failed for PR #{pr['number']}"
            if entry.get("status") != "live":  # discarded while the tests ran
                pr["stage"] = "cancelled"
                return f"{ref}: PR #{pr['number']} not merged (ticket discarded)"
            try:
                merge = self.gh.merge_pull_request(
                    pr["id"],
                    pr["head"],
                    f"Merge #{pr['number']}: {pr.get('title', '')}"[:120],
                    f"Refs {ref}",
                )
            except Exception as exc:
                pr["stage"] = "failed"
                self._say(entry, issue_id, self.t("release_merge_failed", link=link, error=exc)
                          + self._back_to_work(issue_id, entry))  # fmt: skip
                return f"{ref}: merge of PR #{pr['number']} failed"
            assert self.app is not None
            pr.update(merge=merge, stage="deploy",
                      request=self.queue.request("deploy", sha=merge, branch=self.app.branch,
                                                 ticket=ref))  # fmt: skip
            self._say(entry, issue_id, self.t("release_tested", link=link,
                                              merge=self._app_link(merge)))  # fmt: skip
            return f"{ref}: PR #{pr['number']} merged, deploying"
        # stage deploy
        if ok:
            pr["stage"] = "live"
            version = str(result.get("version") or pr.get("merge", ""))[:7]
            self._say(entry, issue_id, self.t("release_live", link=link, version=version))
            return f"{ref}: PR #{pr['number']} live"
        pr["stage"] = "failed"
        undone = ""
        if self.app is not None and pr.get("merge"):  # keep the branch equal to what runs
            try:
                revert = self.app.revert_merge(
                    pr["merge"], f"Revert #{pr['number']}: deployment failed\n\nRefs {ref}"
                )
                undone = self.t("release_branch_reset", revert=self._app_link(revert))
            except Exception as exc:
                undone = self.t("release_branch_not_reset", error=exc)
        self._say(entry, issue_id, self.t("release_deploy_failed", link=link, message=message)
                  + undone + self._back_to_work(issue_id, entry))  # fmt: skip
        return f"{ref}: deployment of PR #{pr['number']} failed"

    # -- discarded tickets -------------------------------------------------------------------

    def _discards(self, state: dict[str, Any]) -> list[str]:
        live = {issue for issue, entry in state.items() if entry.get("status") == "live"}
        if not live:
            return []
        events: list[str] = []
        for contract in self.cfg.boards.values():
            column = contract.ai.discard
            if not column:
                continue
            owner, number = split_project(contract.project)
            project = self.gh.project(owner, number)
            for ticket in tickets(self.gh.project_items(project.id), project):
                if ticket.issue_id in live and ticket.status == column:
                    events.append(self._undo(ticket, column, state[ticket.issue_id]))
                    self._save(state)
        return events

    def _undo(self, ticket: Ticket, column: str, entry: dict[str, Any]) -> str:
        ref = ticket.ref
        lines = [self.t("release_reverted", column=column)]
        problems: list[str] = []
        for commit in reversed(entry.get("vault", [])):
            if self.git is None:
                problems.append(self.t("release_revert_failed", what=commit, error="no git"))
                continue
            try:
                revert = self.git.revert(
                    commit, f"Revert {commit}: {ticket.title} discarded\n\nRefs {ref}\n"
                )
                lines.append(self.t("release_revert_vault", commit=self._vault_link(commit),
                                    revert=self._vault_link(revert)))  # fmt: skip
            except Exception as exc:
                problems.append(self.t("release_revert_failed", what=commit, error=exc))
        if entry.get("vault") and self.git is not None:
            try:
                self.git.run()
            except Exception:
                log.exception("push after undoing %s failed", ref)
        for pr in entry.get("prs", []):
            link = self._pr_link(pr)
            if pr.get("stage") == "test":
                pr["stage"] = "cancelled"
                lines.append(self.t("release_revert_cancelled", link=link))
            elif pr.get("merge") and pr.get("stage") in ("deploy", "live") and self.app:
                try:
                    message = f"Revert #{pr['number']}: {ticket.title} discarded\n\nRefs {ref}"
                    revert = self.app.revert_merge(pr["merge"], message)
                    pr["stage"] = "reverted"
                    if self.queue is not None:
                        self.queue.request("deploy", sha=revert, branch=self.app.branch,
                                           ticket=ref)  # fmt: skip
                    lines.append(self.t("release_revert_app", link=link,
                                        revert=self._app_link(revert)))  # fmt: skip
                except Exception as exc:
                    problems.append(self.t("release_revert_failed", what=f"PR #{pr['number']}",
                                           error=exc))  # fmt: skip
        entry["status"] = "reverted" if not problems else "revert_failed"
        self._say(entry, ticket.issue_id, "\n".join(lines + problems))
        return f"{ref}: undone" + (" with problems" if problems else "")
