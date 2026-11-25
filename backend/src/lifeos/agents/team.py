"""AI teammates take tickets by their state, not by events.

A ticket is the AI's when it is open, sits in the board's pickup (or working) column, the
``AI`` field is empty and – on boards with a ``Responsible`` field – that field names an
agent. The agent then

1. sets ``AI = running`` (and moves the ticket to the working column),
2. posts a start comment (what the column means, what it will do, link to its
   instructions at their current revision),
3. runs its workflow,
4. reports and moves the ticket to the review column with ``AI`` cleared – or, when it
   has a question or fails, comments and sets ``AI = blocked``,
5. appends to its run log in the vault and commits ``agent(<name>): …``.

A ticket left at ``running`` (worker restarted mid-run) becomes ``blocked`` after a while.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from lifeos.agents import comments
from lifeos.agents.comments import Texts
from lifeos.agents.config import (
    AI_FIELD,
    BLOCKED,
    RUNNING,
    AgentConfig,
    AgentDef,
    BoardContract,
)
from lifeos.agents.inbox import assistant_option, create_inbox_ticket
from lifeos.agents.mirror import STATUS, split_project
from lifeos.agents.runlog import RunRecord, append_run
from lifeos.agents.spend import Spend
from lifeos.connectors.github.client import Project, item_values
from lifeos.core.vault import Vault, write_atomic
from lifeos.modules.reviews import handoff

log = logging.getLogger("lifeos.agents")

STALE_MINUTES = 30


@dataclass
class Ticket:
    item_id: str
    issue_id: str
    number: int
    title: str
    url: str
    state: str
    body: str
    status: str | None
    values: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""  # of the issue: changes with every new comment

    @property
    def ref(self) -> str:
        """``owner/repo#12`` from the issue URL."""
        parts = self.url.rstrip("/").split("/")
        if len(parts) >= 4 and parts[-2] == "issues":
            return f"{parts[-4]}/{parts[-3]}#{parts[-1]}"
        return f"#{self.number}"


def tickets(raw: list[dict[str, Any]], project: Project) -> list[Ticket]:
    out = []
    for node in raw:
        issue = node.get("content") or {}
        if issue.get("__typename") != "Issue":
            continue
        values = item_values(node, project.fields)
        out.append(
            Ticket(
                item_id=node["id"],
                issue_id=issue["id"],
                number=issue["number"],
                title=issue["title"],
                url=issue["url"],
                state=issue.get("state", "OPEN"),
                body=issue.get("body") or "",
                status=values.get(STATUS),
                values=values,
                updated_at=issue.get("updatedAt") or "",
            )
        )
    return out


class AgentBlocked(Exception):  # noqa: N818 - a signal, not an error
    """Raised by a workflow that needs an answer from the owner before it can go on."""

    def __init__(self, question: str) -> None:
        super().__init__(question)
        self.question = question


@dataclass
class RunResult:
    report: str  # Markdown for the report comment
    summary: str = ""  # one line for the commit message and the run log
    paths: list[str] = field(default_factory=list)  # vault paths to commit
    cost_eur: float = 0.0
    # general points the agent noticed (e.g. from the owner's comments): each becomes a
    # proposal ticket in the inbox, waiting for the owner's approval
    proposals: list[Proposal] = field(default_factory=list)
    # pull requests the agent opened (URLs); released by the team's releases, if configured
    pull_requests: list[str] = field(default_factory=list)


@dataclass
class Proposal:
    title: str
    body: str  # Markdown: occasion, current state, proposal, pros and cons


@dataclass
class RunContext:
    agent: AgentDef
    contract: BoardContract
    ticket: Ticket
    vault: Vault
    gh: Any
    project: Project
    instructions_revision: str | None
    texts: Texts = field(default_factory=Texts)
    vault_web_url: str | None = None  # https://github.com/<owner>/<vault repo>
    steps: list[str] = field(default_factory=list)
    spend: Spend = field(default_factory=Spend)

    @property
    def cost_eur(self) -> float:
        return self.spend.cost_eur

    @cost_eur.setter
    def cost_eur(self, value: float) -> None:
        self.spend.cost_eur = value

    def step(self, text: str) -> None:
        """Record a step for the run log (what happened, in plain words)."""
        log.info("%s #%s: %s", self.agent.name, self.ticket.number, text)
        self.steps.append(text)

    def _owner_comments(self) -> list[str]:
        """The owner's comments since this agent last wrote on the ticket before this run.

        Comments with an agent marker are not the owner's; the start comment of the
        current run is the agent's last comment, so feedback lies between the one before
        it and now.
        """
        notes = self.gh.issue_comments(self.ticket.issue_id)
        mine = [i for i, c in enumerate(notes) if comments.agent_of(c["body"]) == self.agent.name]
        since = mine[-2] if len(mine) >= 2 else -1
        return [
            c["body"].strip()
            for c in notes[since + 1 :]
            if comments.agent_of(c["body"]) is None and c["body"].strip()
        ]

    def owner_feedback(self) -> list[str]:
        """The owner's plain comments (without those sent from the review page)."""
        return [c for c in self._owner_comments() if handoff.parse_marker(c) is None]

    def review_rounds(self) -> list[handoff.SentRound]:
        """The sealed rounds the owner sent from the review page since the last run."""
        out: list[handoff.SentRound] = []
        for c in self._owner_comments():
            payload = handoff.parse_marker(c)
            if payload is not None:
                out += handoff.load_sent(self.vault, payload)
        return out


Workflow = Callable[[RunContext], RunResult]


class Team:
    """The AI teammates of one board."""

    def __init__(
        self,
        gh: Any,
        vault: Vault,
        cfg: AgentConfig,
        contract: BoardContract,
        workflows: dict[str, Workflow],
        state_file: Path,
        git: Any | None = None,
        timezone: str = "UTC",
        stale_minutes: int = STALE_MINUTES,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        texts: Texts | None = None,
        repo: str | None = None,
        releases: Any | None = None,
    ) -> None:
        self.gh = gh
        self.repo = repo  # repository for new tickets in the inbox (proposals)
        self.releases = releases  # lifeos.agents.releases.Releases
        self.vault = vault
        self.cfg = cfg
        self.contract = contract
        self.workflows = workflows
        self.state_file = state_file
        self.git = git
        self.tz = ZoneInfo(timezone)
        self.stale = timedelta(minutes=stale_minutes)
        self.clock = clock
        self.t = texts or Texts.load(vault)

    # -- state of runs in progress (to detect interrupted runs) -----------------

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, state: dict[str, Any]) -> None:
        write_atomic(self.state_file, json.dumps(state, ensure_ascii=False, indent=1))

    # -- polling ------------------------------------------------------------------

    def poll(self) -> list[str]:
        if not self.contract.ai.responsible_field and not any(
            c.agent for c in self.contract.columns
        ):
            return []  # no column hands tickets to an agent (e.g. a board a job agent fills)
        owner, number = split_project(self.contract.project)
        project = self.gh.project(owner, number)
        if AI_FIELD not in project.fields:
            return [f"{self.contract.board}: field '{AI_FIELD}' missing (board not mirrored yet)"]
        events: list[str] = []
        running_now: set[str] = set()
        for ticket in tickets(self.gh.project_items(project.id), project):
            if ticket.state != "OPEN":
                continue
            ai = ticket.values.get(AI_FIELD)
            if ai == RUNNING:
                running_now.add(ticket.item_id)
                event = self._check_stale(ticket, project)
                if event:
                    events.append(event)
                continue
            if ai == BLOCKED:
                continue
            agent = self.agent_for(ticket)
            if agent is None:
                continue
            events.append(self.run(ticket, agent, project))
        # forget entries of tickets that are no longer running
        state = self._load()
        for item_id in [k for k in state if k not in running_now]:
            state.pop(item_id)
        self._save(state)
        return events

    def agent_for(self, ticket: Ticket) -> AgentDef | None:
        ai = self.contract.ai
        if ticket.status not in {ai.pickup, ai.working}:
            return None
        if ai.responsible_field:
            wanted = ticket.values.get(ai.responsible_field)
            return next(
                (a for a in self.cfg.agents_of(self.contract.board)
                 if a.responsible_option == wanted),
                None,
            )  # fmt: skip
        column = self.contract.column(ticket.status)
        if column is None or not column.agent:
            return None
        return self.cfg.agents.get(column.agent)

    def _check_stale(self, ticket: Ticket, project: Project) -> str | None:
        now = self.clock()
        state = self._load()
        entry = state.setdefault(ticket.item_id, {"seen": now.isoformat(), "issue": ticket.number})
        self._save(state)
        since = datetime.fromisoformat(entry.get("started") or entry["seen"])
        if now - since < self.stale:
            return None
        owner = self.agent_for(ticket)
        agent = entry.get("agent") or (owner.name if owner else "lifeos")
        minutes = int(self.stale.total_seconds() // 60)
        self.gh.set_field(project.id, ticket.item_id, project.fields[AI_FIELD], BLOCKED)
        self.gh.add_comment(ticket.issue_id, comments.interrupted_comment(self.t, agent, minutes))
        state.pop(ticket.item_id, None)
        self._save(state)
        return f"#{ticket.number}: run interrupted → blocked"

    # -- one run ------------------------------------------------------------------

    def instructions(self, agent: AgentDef) -> tuple[str, str | None, bool]:
        revision = self.git.last_commit(agent.path) if self.git else None
        dirty = self.git.is_dirty(agent.path) if self.git else False
        base = self.git.web_url() if self.git else None
        if base and revision:
            link = f"[{agent.path}]({base}/blob/{revision}/{agent.path})"
        else:
            link = self.t("instructions_local", path=agent.path)
        return link, revision, dirty

    def run(self, ticket: Ticket, agent: AgentDef, project: Project) -> str:
        ai = self.contract.ai
        started = self.clock()
        t0 = time.monotonic()
        self.gh.set_field(project.id, ticket.item_id, project.fields[AI_FIELD], RUNNING)
        if ticket.status != ai.working:
            self.gh.set_field(project.id, ticket.item_id, project.fields[STATUS], ai.working)
        state = self._load()
        state[ticket.item_id] = {
            "agent": agent.name,
            "started": started.isoformat(),
            "issue": ticket.number,
        }
        self._save(state)

        link, revision, dirty = self.instructions(agent)
        instructions = comments.instructions_line(self.t, link, revision, dirty)
        column = self.contract.column(ticket.status) or self.contract.column(ai.pickup)
        self.gh.add_comment(
            ticket.issue_id,
            comments.start_comment(
                self.t,
                agent.name,
                agent.title,
                column.name if column else ai.pickup,
                column.description if column else "",
                comments.numbered_steps(agent.section(self.t("workflow_heading"))),
                instructions,
                comments.billing_line(self.t, agent.billing),
            ),
        )

        ctx = RunContext(
            agent, self.contract, ticket, self.vault, self.gh, project, revision,
            texts=self.t, vault_web_url=self.git.web_url() if self.git else None,
        )  # fmt: skip
        outcome, result, message = "done", None, ""
        workflow = self.workflows.get(agent.workflow or "") if agent.runner == "workflow" else None
        try:
            if workflow is None:
                raise AgentBlocked(self.t("no_workflow", workflow=agent.workflow or agent.runner))
            result = workflow(ctx)
        except AgentBlocked as exc:
            outcome, message = "blocked", exc.question
        except Exception as exc:  # report on the ticket, never stop the worker
            log.exception("%s failed on #%s", agent.name, ticket.number)
            outcome, message = "error", f"{type(exc).__name__}: {exc}"

        seconds = int(time.monotonic() - t0)
        spend = ctx.spend
        if result is not None and result.cost_eur:
            spend.cost_eur = max(spend.cost_eur, result.cost_eur)
        footer = comments.footer(self.t, revision, seconds, spend, agent.billing, self.clock())

        # the run log and the files first: the report links the commit
        first_line = message.strip().splitlines()[0] if message.strip() else ""
        summary = (result.summary if result else "") or first_line[:120]
        record = RunRecord(
            started=started.astimezone(self.tz).strftime("%Y-%m-%d %H:%M"),
            ticket=f"#{ticket.number} {ticket.title}",
            ticket_url=ticket.url,
            board=self.contract.title,
            outcome=outcome,
            instructions=revision or "",
            seconds=seconds,
            cost_eur=spend.cost_eur,
            steps=ctx.steps,
            result=summary,
            billing=agent.billing,
            tokens=spend.tokens,
            list_eur=spend.list_eur,
        )
        paths = [append_run(self.vault, agent.name, agent.title, record, self.t)]
        paths += result.paths if result else []
        commit = self._commit(agent, ticket, outcome, summary, paths)

        if outcome == "done" and result is not None:
            report = (
                result.report
                + self._proposals(agent, ticket, result.proposals)
                + self._release(agent, ticket, commit if result.paths else None, result)
            )
            body = comments.report_comment(self.t, agent.name, report, footer)
            self.gh.add_comment(ticket.issue_id, body)
            self.gh.set_field(project.id, ticket.item_id, project.fields[STATUS], ai.review)
            self.gh.set_field(project.id, ticket.item_id, project.fields[AI_FIELD], None)
        else:
            body = (
                comments.question_comment(self.t, agent.name, message, footer)
                if outcome == "blocked"
                else comments.error_comment(self.t, agent.name, message, footer)
            )
            self.gh.add_comment(ticket.issue_id, body)
            self.gh.set_field(project.id, ticket.item_id, project.fields[AI_FIELD], BLOCKED)

        state = self._load()
        state.pop(ticket.item_id, None)
        self._save(state)
        return f"#{ticket.number} {agent.name}: {outcome}" + (f" – {summary}" if summary else "")

    def _release(
        self, agent: AgentDef, ticket: Ticket, commit: str | None, result: RunResult
    ) -> str:
        """Report lines that link what went live; on boards with a discard column the
        releases also remember it, to undo it when the ticket is moved there."""
        if self.releases is not None and self.contract.ai.discard:
            return self.releases.record(agent, self.contract, ticket, commit, result.pull_requests)
        base = self.git.web_url() if self.git else None
        if commit and base:
            link = f"[`{commit}`]({base}/commit/{commit})"
            return "\n\n" + self.t("report_commit", link=link)
        return ""

    def _proposals(self, agent: AgentDef, ticket: Ticket, proposals: list[Proposal]) -> str:
        """Create the proposal tickets; returns the report lines that link them."""
        links = []
        source = self.t("proposal_source", agent=agent.title, ref=ticket.ref, url=ticket.url)
        for proposal in proposals:
            body = f"{proposal.body.strip()}\n\n---\n{source}"
            try:
                url = create_inbox_ticket(
                    self.gh,
                    self.cfg,
                    self.repo,
                    proposal.title,
                    body,
                    author=agent.name,
                    responsible=assistant_option(self.cfg),
                )
            except Exception:  # the run is done; a failed proposal must not undo it
                log.exception("proposal of %s failed: %s", agent.name, proposal.title)
                url = None
            if url:
                links.append(f"- [{proposal.title}]({url})")
        return "\n\n" + "\n".join([self.t("report_proposals"), *links]) if links else ""

    def _commit(
        self, agent: AgentDef, ticket: Ticket, outcome: str, summary: str, paths: list[str]
    ) -> str | None:
        """Commit and push ``paths``; returns the commit (short) when something changed."""
        if self.git is None:
            return None
        subject = f"agent({agent.name}): #{ticket.number} {outcome}"
        if summary:
            subject += f" – {summary}"
        message = f"{subject[:150]}\n\nRefs {ticket.ref}\n"
        try:
            if not self.git.commit_paths(paths, message):
                return None
            commit = self.git.head() if hasattr(self.git, "head") else None
            self.git.run()
            return commit
        except Exception:  # the run itself is done; a commit failure must not undo it
            log.exception("commit after %s run failed", agent.name)
            return None
