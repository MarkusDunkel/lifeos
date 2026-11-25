"""The catalog of every LifeOS process: where it runs, how often it must succeed, and what
flows from which source into which target.

The catalog is generic: what is switched on comes from the settings, so a process that
should run but is not set up shows as such instead of silently missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    """A source or target in the flow diagram."""

    id: str
    label: str
    kind: str  # source | target
    detail: str = ""
    folder: str | None = None  # knowledge folder, for mirror badges


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
    system: bool = False  # infrastructure, shown in the system strip, not the diagram
    quiet_skips: tuple[str, ...] = ()  # skip messages that are normal, not a warning
    description: str = ""


@dataclass
class Catalog:
    nodes: list[Node] = field(default_factory=list)
    processes: list[Process] = field(default_factory=list)

    def process(self, process_id: str) -> Process | None:
        return next((p for p in self.processes if p.id == process_id), None)


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


def _job(pid: str, label: str, seconds: int, enabled: bool = True, **kw) -> Process:
    warn, fail = interval(seconds) if seconds > 0 else (None, None)
    kw.setdefault("warn_after", warn)
    kw.setdefault("fail_after", fail)
    return Process(
        pid, label, SERVER, _every(seconds) if seconds > 0 else "off",
        enabled=seconds > 0 and enabled, every=seconds or None, **kw,
    )  # fmt: skip


def _daily(pid: str, label: str, at: str | None, enabled: bool = True, **kw) -> Process:
    """Daily jobs: yellow 1 h after the slot without success, red after 4 h (the
    scheduler retries hourly, at most three times)."""
    return Process(
        pid, label, SERVER, f"daily {at}" if at else "off",
        enabled=bool(at) and enabled, daily_at=at, warn_after=HOUR, fail_after=4 * HOUR, **kw,
    )  # fmt: skip


def build_catalog(s: Settings) -> Catalog:
    kb = "ready/knowledge"
    teams = s.teams_dir
    nodes = [
        Node("moodle", "Moodle", "source", "courses, files, announcements"),
        Node("gmail", "Gmail", "source", "Teams messages forwarded by mail"),
        Node("onedrive-pc", "OneDrive on the PC", "source", "synced Teams document libraries"),
        Node("onedrive", "OneDrive", "source", "presentations folder"),
        Node("google-calendar", "Google Calendar", "source", "input calendars (read only)"),
        Node("devjobs", "devjobs.at", "source", "job postings"),
        Node("github-boards", "GitHub boards", "source", "tasks, applications"),
        Node("ors", "OpenRouteService", "source", "commute times"),
        Node("kb-moodle", s.moodle_dir, "target", f"{kb}/{s.moodle_dir}", s.moodle_dir),
        Node("kb-teams-chats", f"{teams}/Chats", "target", f"{kb}/{teams}/Chats", f"{teams}/Chats"),
        Node("kb-teams-files", f"{teams} files", "target", f"{kb}/{teams}/…", teams),
        Node(
            "kb-presentations",
            s.presentations_dir,
            "target",
            f"{kb}/{s.presentations_dir}",
            s.presentations_dir,
        ),  # fmt: skip
        Node("kb-planning", s.planning_dir, "target", f"{kb}/{s.planning_dir}", s.planning_dir),
        Node("calendar-lifeos", "Calendar 'LifeOS'", "target", "plan blocks and mirrors"),
        Node("job-postings", "Job postings", "target", "imported postings"),
        Node("job-assessments", "Job assessments", "target", "AI fit ratings"),
        Node("commute-times", "Commute times", "target", "car and bike"),
        Node("applications", "Applications", "target", "frozen PDFs, released postings"),
        Node("tickets", "Ticket copy", "target", "ready/tickets"),
        Node("agent-work", "Agent work", "target", "reports, commits, pull requests"),
        Node("app-live", "Running app", "target", "deployed branch"),
        Node("vault-remote", "Vault repository", "target", "private GitHub repo"),
    ]
    agents_on = s.agents_interval > 0 and bool(s.github_token)
    releases = agents_on and bool(s.app_repo)
    pc_on = bool(s.control_report_token)
    kb_targets = (
        "kb-moodle", "kb-teams-chats", "kb-teams-files", "kb-presentations", "kb-planning",
        "applications", "agent-work",
    )  # fmt: skip
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
        ),
        _daily(
            "teams-mail",
            "Teams messages",
            s.teams_mail_sync_at,
            inputs=("gmail",),
            outputs=("kb-teams-chats",),
            checks=("google", "gmail"),
            mirror=True,
            description="Imports the Teams chats from Gmail and commits them as `teams-mail: …`.",
        ),
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
        ),
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
        ),
        _job(
            "calendar-sync",
            "Calendar sync",
            s.calendar_sync_interval,
            inputs=("google-calendar",),
            outputs=("calendar-lifeos",),
            checks=("google",),
            description="Reads the input calendars and mirrors them into the calendar 'LifeOS'.",
        ),
        _daily(
            "calendar-planner",
            "Day planner",
            s.planner_at,
            inputs=("google-calendar",),
            outputs=("calendar-lifeos", "kb-planning"),
            checks=("google",),
            description="The AI plans the coming days and writes its blocks to Google.",
        ),
        _job(
            "planner-queue",
            "Planner queue",
            60 if s.calendar_sync_interval > 0 else 0,
            system=True,
            quiet_skips=("nothing queued",),
            description="Re-plans within a minute after wishes or changed appointments.",
        ),
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
        ),
        _job(
            "job-matcher",
            "Job matcher",
            600 if agents_on else 0,
            inputs=("job-postings",),
            outputs=("job-assessments",),
            quiet_skips=("nothing to assess",),
            description="The AI rates new postings against the job profiles.",
        ),
        _job(
            "commute",
            "Commute times",
            HOUR if s.ors_api_key else 0,
            inputs=("job-postings", "ors"),
            outputs=("commute-times",),
            description="Fetches commute times from the homes to the postings.",
        ),
        _job(
            "applications-board",
            "Applications board",
            s.applications_interval,
            enabled=bool(s.github_token and s.applications_project),
            inputs=("github-boards",),
            outputs=("applications",),
            checks=("github",),
            description="Watches the applications board, freezes what was sent.",
        ),
        _job(
            "agents",
            "AI teammates",
            s.agents_interval if agents_on else 0,
            inputs=("github-boards",),
            outputs=("agent-work",),
            checks=("github",),
            description="Mirrors the board contracts and lets the agents take their tickets.",
        ),
        Process(
            "deployer",
            "Deployer",
            HOST,
            "every few seconds",
            enabled=releases,
            inputs=("agent-work",),
            outputs=("app-live",),
            warn_after=5 * MIN,
            fail_after=15 * MIN,
            hint="On the server: `systemctl status lifeos-deployer.timer`.",
            description="Tests and deploys the pull requests the assistant releases.",
        ),
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
            description="Copies the GitHub issues into ready/tickets for context and search.",
        ),
        _job(
            "git-sync",
            "Vault sync",
            s.git_sync_interval,
            inputs=kb_targets,
            outputs=("vault-remote",),
            run_path="/api/vault-sync/run",
            description="Commits, pulls and pushes the vault every few minutes.",
        ),
        _job(
            "control-report",
            "AI diagnosis",
            s.control_report_interval,
            system=True,
            warn_after=30 * MIN,
            fail_after=2 * HOUR,
            quiet_skips=("all green", "unchanged", "no agents/control-room.md"),
            description="The AI explains the current problems on this page.",
        ),
    ]
    return Catalog(nodes, processes)
