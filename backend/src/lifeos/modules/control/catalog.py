"""The catalog of every LifeOS process: where it runs, how often it must succeed, and what
flows from which source into which target.

Every flow ends in an **actionable**: a dataset that makes the owner act (a ticket board,
the day plan, job assessments). Actionables are data too: the **second order** row reads
them (and the control room itself) to make new actionables, e.g. the LifeOS manager. The
technical foundation (vault sync, deployer, ticket copy) is monitored too, but drawn apart:
it carries everything and ends in no actionable.

The catalog is generic: what is switched on comes from the settings, so a process that
should run but is not set up shows as such instead of silently missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from lifeos.core.config import Settings

MIN = 60
HOUR = 3600
DAY = 86400

SERVER = "server"  # worker job on the server
PC = "pc"  # scheduled task on the PC, reports to the server
MANUAL = "manual"  # runs when someone presses a button
HOST = "host"  # outside Docker on the server (deployer timer)


@dataclass(frozen=True)
class Node:
    """A source, a dataset (what LifeOS keeps) or an actionable (a dataset that makes the
    owner act) in the flow diagram."""

    id: str
    label: str
    kind: str  # source | dataset | actionable
    detail: str = ""
    folder: str | None = None  # knowledge folder, for mirror badges
    group: str | None = None  # drawn inside a box, e.g. everything in the vault
    url: str | None = None  # where to act (actionables)
    local: bool = False  # kept by the owner, not written by a process
    # a dataset made of other nodes (e.g. all boards): takes their worst light
    derived_from: tuple[str, ...] = ()
    ref: str = ""  # the global id, e.g. ``D-8``, see ``REFS``


EXPLAINER = "Control room explainer"  # the process that writes the explanation on /control
WORKFLOW_AGENTS = "@workflow"  # the board agents (`runner: workflow` in agents/)
VAULT = "vault"
# targets that live in the vault: one box in the diagram, one arrow to the vault sync
VAULT_TARGETS = (
    "kb-moodle", "kb-teams-chats", "kb-teams-files", "kb-presentations", "kb-planning",
    "applications",
)  # fmt: skip

# The global id of every building block: a letter for its kind and a number. The numbers
# are one count for all kinds, like issue numbers, so "P-46" (or just 46) says what is meant.
# A number is never reused: a block that goes away moves its number to ``RETIRED``; a new
# one takes the next free number.
PREFIX = {"source": "S", "dataset": "D", "actionable": "A", "process": "P"}
REFS: dict[str, int] = {
    # sources
    "moodle": 1, "gmail": 2, "onedrive-pc": 3, "onedrive": 4,
    "google-calendar": 5, "devjobs": 6, "github-boards": 7,
    # datasets
    "kb-moodle": 8, "kb-teams-chats": 9, "kb-teams-files": 10,
    "kb-presentations": 11, "kb-planning": 12, "applications": 13,
    "job-postings": 14, "postings-commute": 15,
    # actionables
    "act-university": 16, "act-day-plan": 17, "act-job-assessments": 18,
    "act-applications": 19, "act-tasks": 20,
    # second order and foundation nodes
    "boards": 21, "control-room": 22, "control-explanation": 23,
    "pull-requests": 24, "tickets": 25, "app-live": 26, "vault-remote": 27,
    "claude-code-pc": 28, "token-usage": 29,
    # processes
    "moodle-sync": 30, "teams-mail": 31, "teams-files": 32,
    "presentations": 33, "uni-planner": 34, "calendar-sync": 35,
    "calendar-planner": 36, "planner-queue": 37, "job-import": 38,
    "commute": 39, "job-matcher": 40, "start-application": 41,
    "application-writer": 42, "applications-board": 43, "agents": 44,
    "lifeos-manager": 45, "control-report": 46, "git-sync": 47,
    "deployer": 48, "tickets-copy": 49, "claude-usage": 50,
}  # fmt: skip
RETIRED: tuple[int, ...] = ()  # numbers of blocks that are gone: never hand them out again


def ref_of(block_id: str, kind: str) -> str:
    """The global id (e.g. ``P-46``) of a node (``kind`` = its kind) or a process. The
    process "tickets" shares its id with the dataset it writes: it is ``tickets-copy`` here."""
    key = "tickets-copy" if kind == "process" and block_id == "tickets" else block_id
    number = REFS.get(key)
    return f"{PREFIX[kind]}-{number}" if number is not None else ""


@dataclass(frozen=True)
class Process:
    id: str
    label: str
    where: str
    trigger: str  # human readable, e.g. "every 5 min" or "daily 05:00"
    enabled: bool
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    every: int | None = None  # seconds, for interval jobs
    daily_at: str | None = None  # HH:MM local, for daily jobs
    warn_after: int | None = None  # seconds since the last success
    fail_after: int | None = None
    checks: tuple[str, ...] = ()  # preconditions, see :mod:`lifeos.modules.control.checks`
    run_path: str | None = None  # POST endpoint that runs it now
    hint: str = ""  # how to run it by hand
    mirror: bool = False  # its outputs are mirrors: hand edits are overwritten
    foundation: bool = False  # technical foundation: own band below the flow
    # reads actionables or the control room and makes a new actionable: own row below
    second_order: bool = False
    # actionables whose changes by the owner it reacts to (moved tickets, comments): drawn
    # as a dashed loop back from the actionable, not as an input
    reacts_to: tuple[str, ...] = ()
    reaction: str = ""  # what the owner does there, the label of the loop (e.g. "Sent")
    job: str | None = None  # worker job that runs it, when it shares one (default: its id)
    quiet_skips: tuple[str, ...] = ()  # skip messages that are normal, not a warning
    ai: tuple[str, ...] = ()  # agents whose model it calls (``WORKFLOW_AGENTS`` = board agents)
    description: str = ""
    ref: str = ""  # the global id, e.g. ``D-8``, see ``REFS``


@dataclass
class Catalog:
    nodes: list[Node] = field(default_factory=list)
    processes: list[Process] = field(default_factory=list)

    def process(self, process_id: str) -> Process | None:
        return next((p for p in self.processes if p.id == process_id), None)

    def node(self, node_id: str) -> Node | None:
        return next((n for n in self.nodes if n.id == node_id), None)


def interval(seconds: int) -> tuple[int, int]:
    """Default deadlines of an interval job: warning after 3, failure after 6 intervals,
    at least 5 and 15 minutes."""
    return max(3 * seconds, 5 * MIN), max(6 * seconds, 15 * MIN)


def _every(seconds: int) -> str:
    if seconds % HOUR == 0:
        hours = seconds // HOUR
        return "hourly" if hours == 1 else f"every {hours} h"
    if seconds % MIN == 0:
        minutes = seconds // MIN
        return "every minute" if minutes == 1 else f"every {minutes} min"
    return f"every {seconds} s"


def _job(pid: str, label: str, seconds: int, enabled: bool = True, via: str = "", **kw) -> Process:
    """``via``: a service the job uses as a tool, named in its trigger line instead of
    being drawn as a source (e.g. the routing service of the commute times)."""
    warn, fail = interval(seconds) if seconds > 0 else (None, None)
    kw.setdefault("warn_after", warn)
    kw.setdefault("fail_after", fail)
    trigger = _every(seconds) if seconds > 0 else "off"
    return Process(
        pid, label, SERVER, f"{trigger} · via {via}" if via and seconds > 0 else trigger,
        enabled=seconds > 0 and enabled, every=seconds or None, **kw,
    )  # fmt: skip


def _daily(pid: str, label: str, at: str | None, enabled: bool = True, **kw) -> Process:
    """Daily jobs: yellow 1 h after the slot without success, red after 4 h (the
    scheduler retries hourly, at most three times)."""
    return Process(
        pid, label, SERVER, f"daily {at}" if at else "off",
        enabled=bool(at) and enabled, daily_at=at, warn_after=HOUR, fail_after=4 * HOUR, **kw,
    )  # fmt: skip


def _project_url(ref: str | None) -> str | None:
    if not ref or "/" not in ref:
        return None
    owner, number = ref.split("/", 1)
    return f"https://github.com/users/{owner}/projects/{number}"


def build_catalog(s: Settings) -> Catalog:
    kb = "ready/knowledge"
    teams = s.teams_dir
    tasks_board = next((p for p in s.github_project_list if p != s.applications_project), None)
    nodes = [
        # sources
        Node("moodle", "Moodle", "source", "courses, files, announcements"),
        Node("gmail", "Gmail", "source", "Teams messages forwarded by mail"),
        Node("onedrive-pc", "OneDrive on the PC", "source", "synced Teams document libraries"),
        Node("onedrive", "OneDrive", "source", "presentations folder"),
        Node("google-calendar", "Google Calendar", "source", "input calendars (read only)"),
        Node("devjobs", "devjobs.at", "source", "job postings"),
        Node("github-boards", "GitHub boards", "source", "tickets and comments"),
        # what LifeOS keeps (vault and state)
        Node("kb-moodle", s.moodle_dir, "dataset", f"{kb}/{s.moodle_dir}", s.moodle_dir),
        Node(
            "kb-teams-chats", f"{teams}/Chats", "dataset", f"{kb}/{teams}/Chats", f"{teams}/Chats"
        ),
        Node("kb-teams-files", f"{teams} files", "dataset", f"{kb}/{teams}/…", teams),
        Node(
            "kb-presentations",
            s.presentations_dir,
            "dataset",
            f"{kb}/{s.presentations_dir}",
            s.presentations_dir,
        ),  # fmt: skip
        Node(
            "kb-planning",
            s.planning_dir,
            "dataset",
            "goals, wishes, journal",
            s.planning_dir,
            local=True,
        ),  # fmt: skip
        Node("applications", "Applications", "dataset", "frozen PDFs, released postings"),
        Node("job-postings", "Job postings", "dataset", "imported postings"),
        Node("postings-commute", "Postings with commute", "dataset", "car and bike times added"),
        # actionables: what makes the owner act
        Node(
            "act-university",
            "University board",
            "actionable",
            "exams, submissions, deadlines",
            url=_project_url(s.university_project),
        ),  # fmt: skip
        Node(
            "act-day-plan",
            "Day plan",
            "actionable",
            "calendar 'LifeOS'",
            url="https://calendar.google.com",
        ),  # fmt: skip
        Node(
            "act-job-assessments",
            "Job assessments",
            "actionable",
            "apply / maybe / no",
            url="/jobs",
        ),  # fmt: skip
        Node(
            "act-applications",
            "Applications board",
            "actionable",
            "documents to review",
            url=_project_url(s.applications_project),
        ),  # fmt: skip
        Node(
            "act-tasks",
            "LifeOS Tasks",
            "actionable",
            "AI work to review",
            url=_project_url(tasks_board),
        ),  # fmt: skip
        # second order: actionables and the control room as data for new actionables
        Node(
            "boards",
            "Boards",
            "dataset",
            "University, Applications, LifeOS Tasks",
            derived_from=("act-university", "act-applications", "act-tasks"),
        ),  # fmt: skip
        Node("control-room", "Control room", "dataset", "flows and lights", local=True),
        Node("control-explanation", "Explanation", "dataset", "what is wrong and what to do"),
        # technical foundation
        Node("pull-requests", "Pull requests", "source", "released by the assistant"),
        Node("tickets", "Ticket copy", "dataset", "ready/tickets"),
        Node("app-live", "Running app", "dataset", "deployed branch"),
        Node("vault-remote", "Vault repository", "dataset", "private GitHub repo"),
        Node("claude-code-pc", "Claude Code on the PC", "source", "session transcripts"),
        Node("token-usage", "Token usage", "dataset", "Burned tokens chart"),
    ]
    nodes = [
        replace(n, group=VAULT if n.id in VAULT_TARGETS else n.group, ref=ref_of(n.id, n.kind))
        for n in nodes
    ]  # fmt: skip
    agents_on = s.agents_interval > 0 and bool(s.github_token)
    releases = agents_on and bool(s.app_repo)
    pc_on = bool(s.control_report_token)
    processes = [
        _daily(
            "moodle-sync",
            "Moodle sync",
            s.moodle_sync_at,
            inputs=("moodle",),
            outputs=("kb-moodle",),
            checks=("moodle",),
            mirror=True,
            run_path="/api/moodle/sync",
            description="Mirrors the Moodle courses and commits them as `moodle: …`.",
        ),  # fmt: skip
        (_job if s.teams_mail_interval > 0 else _daily)(
            "teams-mail",
            "Teams messages",
            s.teams_mail_interval or s.teams_mail_sync_at,
            inputs=("gmail",),
            outputs=("kb-teams-chats",),
            checks=("google", "gmail"),
            mirror=True,
            description="Imports the Teams chats from Gmail and commits them as `teams-mail: …`.",
        ),  # fmt: skip
        Process(
            "teams-files",
            "Teams files",
            PC,
            "daily 07:00 and at logon",
            enabled=pc_on,
            inputs=("onedrive-pc",),
            outputs=("kb-teams-files",),
            warn_after=26 * HOUR,
            fail_after=3 * DAY,
            mirror=True,
            hint="On the PC: run deploy/teams-files-sync.cmd (or `lifeos teams-files sync "
            "--commit`). The PC must be on.",
            description="Mirrors the synced Teams document libraries on the PC into the vault.",
        ),  # fmt: skip
        Process(
            "presentations",
            "Presentations sync",
            MANUAL,
            "button",
            enabled=bool(s.ms_client_id and s.onedrive_folder and s.state_dir),
            inputs=("onedrive",),
            outputs=("kb-presentations",),
            checks=("onedrive",),
            warn_after=14 * DAY,
            mirror=True,
            run_path="/api/presentations/sync",
            description="Converts the presentations in OneDrive into notes.",
        ),  # fmt: skip
        _job(
            "uni-planner",
            "Study planner",
            s.uni_planner_interval,
            enabled=bool(s.github_token and s.university_project),
            inputs=("kb-moodle", "kb-teams-chats", "kb-teams-files"),
            outputs=("act-university",),
            checks=("github",),
            warn_after=3 * HOUR,
            fail_after=26 * HOUR,
            quiet_skips=("nothing new",),
            ai=("uni-planner",),
            reacts_to=("act-university",),
            reaction="comments",
            description="The AI turns new Moodle and Teams material into tickets on the "
            "University board and keeps them current (never deletes, closes).",
        ),  # fmt: skip
        _job(
            "calendar-sync",
            "Calendar sync",
            s.calendar_sync_interval,
            inputs=("google-calendar",),
            outputs=("act-day-plan",),
            checks=("google",),
            description="Reads the input calendars and mirrors them into the calendar 'LifeOS'.",
        ),  # fmt: skip
        _daily(
            "calendar-planner",
            "Day planner",
            s.planner_at,
            inputs=("google-calendar", "kb-planning"),
            outputs=("act-day-plan",),
            checks=("google",),
            ai=("calendar-planner",),
            description="The AI plans the coming days and writes its blocks to Google.",
        ),  # fmt: skip
        _job(
            "planner-queue",
            "Planner queue",
            60 if s.calendar_sync_interval > 0 else 0,
            inputs=("google-calendar", "kb-planning"),
            outputs=("act-day-plan",),
            quiet_skips=("nothing queued",),
            ai=("calendar-planner",),
            description="Re-plans within a minute after wishes or changed appointments.",
        ),  # fmt: skip
        Process(
            "job-import",
            "Job import",
            PC,
            "hourly and at logon",
            enabled=pc_on,
            inputs=("devjobs",),
            outputs=("job-postings",),
            warn_after=6 * HOUR,
            fail_after=2 * DAY,
            hint="On the PC: the task 'Job search import' runs hourly; "
            "`lifeos jobsearch import` starts it by hand. The PC must be on.",
            description="Fetches devjobs.at from home (blocked for servers) and sends the "
            "postings to the server.",
        ),  # fmt: skip
        _job(
            "commute",
            "Commute times",
            HOUR if s.ors_api_key else 0,
            inputs=("job-postings",),
            outputs=("postings-commute",),
            via="OpenRouteService",
            description="Fetches commute times from the homes to the postings.",
        ),  # fmt: skip
        _job(
            "job-matcher",
            "Job matcher",
            600 if agents_on else 0,
            inputs=("postings-commute",),
            outputs=("act-job-assessments",),
            quiet_skips=("nothing to assess",),
            ai=("job-matcher",),
            description="The AI rates new postings against the job profiles.",
        ),  # fmt: skip
        # the application cycle: you apply → the writer drafts → you review and send
        Process(
            "start-application",
            "Start application",
            MANUAL,
            "'Apply' on the Jobs page",
            enabled=bool(s.github_token and s.applications_project),
            inputs=("job-postings",),
            outputs=("applications",),
            reacts_to=("act-job-assessments",),
            reaction="Apply",
            run_path=None,
            hint="On the Jobs page: 'Apply' at a posting.",
            description="Opens the ticket in New on the applications board and the folder in "
            "Career/Applications with a snapshot of the posting.",
        ),  # fmt: skip
        _job(
            "application-writer",
            "Application writer",
            s.agents_interval if agents_on else 0,
            job="agents",
            inputs=("applications",),
            outputs=("act-applications",),
            reacts_to=("act-applications",),
            reaction="Active",
            checks=("github",),
            ai=("application-writer",),
            description="Takes applications you move to Active: researches, writes CV and "
            "cover letter into the folder and moves the ticket to In Review.",
        ),  # fmt: skip
        _job(
            "applications-board",
            "Applications watcher",
            s.applications_interval,
            enabled=bool(s.github_token and s.applications_project),
            outputs=("applications",),
            checks=("github",),
            reacts_to=("act-applications",),
            reaction="Sent",
            description="Reacts to Sent: freezes the PDFs and keeps letter and CV as examples; "
            "forgets applications whose ticket was deleted.",
        ),  # fmt: skip
        _job(
            "agents",
            "AI teammates",
            s.agents_interval if agents_on else 0,
            outputs=("act-tasks",),
            reacts_to=("act-tasks",),
            reaction="Todo, KI: comments",
            checks=("github",),
            ai=(WORKFLOW_AGENTS,),
            description="Mirrors the board contracts and lets the agents (assistant, profile "
            "designer) take the tickets you approve.",
        ),  # fmt: skip
        # second order
        _job(
            "lifeos-manager",
            "LifeOS manager",
            s.manager_interval if s.github_token else 0,
            inputs=("boards", "control-room", "control-explanation"),
            outputs=("act-tasks",),
            reacts_to=("act-tasks",),
            reaction="comments",
            checks=("github",),
            quiet_skips=("nothing new", "waiting"),
            ai=("lifeos-manager",),
            second_order=True,
            warn_after=max(3 * s.manager_interval, 6 * HOUR),
            fail_after=max(6 * s.manager_interval, 26 * HOUR),
            description="The AI looks after the whole system. The watch (code) checks for new "
            "blocks, worse lights and your comments on its tickets; then a repair run follows "
            "(at most every 15 min): lift blocks whose cause is gone, fix faults, open a "
            "ticket for what needs you. Once a day, in the morning, an ideas run adds "
            "improvement proposals.",
        ),  # fmt: skip
        # technical foundation
        _job(
            "git-sync",
            "Vault sync",
            s.git_sync_interval,
            inputs=VAULT_TARGETS,
            outputs=("vault-remote",),
            run_path="/api/vault-sync/run",
            foundation=True,
            description="Commits, pulls and pushes the vault every few minutes.",
        ),  # fmt: skip
        Process(
            "deployer",
            "Deployer",
            HOST,
            "every few seconds",
            enabled=releases,
            inputs=("pull-requests",),
            outputs=("app-live",),
            warn_after=5 * MIN,
            fail_after=15 * MIN,
            foundation=True,
            hint="On the server: `systemctl status lifeos-deployer.timer`.",
            description="Tests and deploys the pull requests the assistant releases.",
        ),  # fmt: skip
        Process(
            "tickets",
            "Ticket copy",
            MANUAL,
            "button",
            enabled=bool(s.github_token),
            inputs=("github-boards",),
            outputs=("tickets",),
            checks=("github",),
            warn_after=14 * DAY,
            mirror=True,
            run_path="/api/tickets/sync",
            foundation=True,
            description="Copies the GitHub issues into ready/tickets for context and search.",
        ),  # fmt: skip
        Process(
            "claude-usage",
            "Claude Code usage",
            PC,
            "every 15 min and at logon",
            enabled=pc_on,
            inputs=("claude-code-pc",),
            outputs=("token-usage",),
            foundation=True,
            warn_after=26 * HOUR,
            fail_after=4 * DAY,
            hint="On the PC: `lifeos claude-usage install-autostart --env-file <env>` registers "
            "the task; `lifeos claude-usage report` sends now. The PC must be on.",
            description="Sends the tokens of your Claude Code sessions for the Burned tokens "
            "chart (numbers, model and project folder only).",
        ),  # fmt: skip
        _job(
            "control-report",
            EXPLAINER,
            s.control_report_interval,
            inputs=("control-room",),
            outputs=("control-explanation",),
            second_order=True,
            warn_after=30 * MIN,
            fail_after=2 * HOUR,
            quiet_skips=("all green", "unchanged", "no agents/control-room.md", "daily limit"),
            ai=("control-room",),
            description="The AI explains the current problems at the top of this page; the "
            "LifeOS manager reads the explanation. The model is called only when the yellow "
            "and red lights change (or once a day while they persist), at most daily_limit "
            "times a day.",
        ),  # fmt: skip
    ]
    processes = [replace(p, ref=ref_of(p.id, "process")) for p in processes]
    return Catalog(nodes, processes)
