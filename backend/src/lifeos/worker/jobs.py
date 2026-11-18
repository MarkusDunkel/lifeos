"""The worker's jobs. Each one returns a :class:`JobResult` and never raises on purpose."""

from __future__ import annotations

from collections.abc import Callable
from zoneinfo import ZoneInfo

from lifeos.connectors.moodle.client import MoodleError
from lifeos.connectors.moodle.setup import build_moodle
from lifeos.connectors.moodle.sync import MoodleSync, SyncReport
from lifeos.core.config import Settings
from lifeos.core.git_sync import GitError, GitSync, file_list
from lifeos.core.vault import KNOWLEDGE, Vault
from lifeos.modules.study.bank import STUDY_DIR
from lifeos.modules.study.progress import PROGRESS_FILE
from lifeos.modules.study.service import plural
from lifeos.worker.scheduler import Daily, Every, Job, JobResult, parse_time


def commit_study_leftovers(git: GitSync) -> list[str]:
    """Commit progress logs of study sessions that were never ended, as ``study: …``.

    Normally ``end_session`` commits them; this keeps them out of ``auto-sync`` commits.
    """
    root = git.root / KNOWLEDGE / STUDY_DIR
    committed: list[str] = []
    if not root.is_dir():
        return committed
    for log_file in sorted(root.glob(f"*/{PROGRESS_FILE}")):
        rel = log_file.relative_to(git.root).as_posix()
        added = git.added_lines(rel)
        if not added:
            continue
        subject = log_file.parent.name
        message = f"study: {subject} – {plural(added, 'answer')} (session not ended)\n"
        committed += git.commit_paths([rel], message)
    return committed


def git_sync_job(git: GitSync) -> Callable[[], JobResult]:
    def run() -> JobResult:
        try:
            commit_study_leftovers(git)
        except GitError as exc:
            return JobResult("error", f"study commit failed: {exc}")
        result = git.run()
        return JobResult(result.status, result.message)

    return run


def moodle_commit_message(report: SyncReport, files: list[str]) -> str:
    """``moodle: 3 new, 2 updated`` plus courses and files, for a readable vault history."""
    counts = [
        f"{len(items)} {label}"
        for items, label in (
            (report.created, "new"),
            (report.updated, "updated"),
            (report.renamed, "renamed"),
            (report.deleted, "removed"),
        )
        if items
    ]
    subject = "moodle: " + (", ".join(counts) if counts else "course overviews updated")
    body: list[str] = []
    courses = sorted(
        {path.split("/")[1] for path in report.created + report.updated if path.count("/") >= 2}
    )
    if courses:
        body.append("Courses: " + ", ".join(courses))
    for items, label in (
        (report.created, "New"),
        (report.updated, "Updated"),
        (report.renamed, "Renamed"),
        (report.deleted, "Removed"),
        (report.errors, "Errors"),
    ):
        if items:
            body.append(f"{label}:\n{file_list(items)}")
    if not counts:
        body.append(f"Files:\n{file_list(files)}")
    return subject + "\n\n" + "\n\n".join(body) + "\n"


def moodle_job(moodle: MoodleSync, missing: list[str], git: GitSync) -> Callable[[], JobResult]:
    """Sync Moodle, commit the mirror as ``moodle: …`` and push right away."""
    folder = f"{KNOWLEDGE}/{moodle.target_dir}"

    def run() -> JobResult:
        if missing:
            return JobResult("skipped", f"Moodle is not set up: {', '.join(missing)}")
        try:
            report = moodle.sync()
        except MoodleError as exc:
            return JobResult("error", f"Moodle: {exc}")

        summary = (
            f"{len(report.created)} new, {len(report.updated)} updated, "
            f"{len(report.deleted)} removed, {report.unchanged} unchanged"
        )
        try:
            committed = git.commit_paths(
                [folder], lambda files: moodle_commit_message(report, files)
            )
        except GitError as exc:
            return JobResult("error", f"{summary}; commit failed: {exc}")
        pushed = ""
        if committed:
            sync = git.run()
            pushed = f"; committed {len(committed)} file(s), git sync {sync.status}"
        if report.errors:
            return JobResult("error", f"{summary}{pushed}; {len(report.errors)} error(s)")
        return JobResult("ok", summary + pushed)

    return run


def applications_board_job(settings: Settings, git: GitSync) -> Callable[[], JobResult]:
    """Poll the applications board and run the handler of each column change once;
    release the postings of applications whose ticket was deleted."""
    from lifeos.connectors.github.client import GitHubClient, GitHubError
    from lifeos.core.git_sync import background_committer
    from lifeos.modules.applications.board import BoardError, BoardWatcher, split_project
    from lifeos.modules.applications.service import BOARD_STATE_FILE, Applications
    from lifeos.modules.jobsearch.store import JobStore

    vault = Vault.open(settings.vault_root)
    applications = Applications(
        vault,
        JobStore(vault),
        (lambda: GitHubClient(settings.github_token)) if settings.github_token else None,
        settings.applications_repo or next(iter(settings.github_repo_list), None),
        settings.applications_project,
        committer=background_committer(git),
        timezone=settings.timezone,
    )

    def run() -> JobResult:
        if not settings.github_token or not settings.applications_project:
            return JobResult("skipped", "LIFEOS_GITHUB_TOKEN / LIFEOS_APPLICATIONS_PROJECT not set")
        try:
            gh = GitHubClient(settings.github_token)
            owner, number = split_project(settings.applications_project)
            watcher = BoardWatcher(gh, gh.project(owner, number), vault.resolve(BOARD_STATE_FILE))
            events = watcher.poll()
            events += applications.reconcile({item.item_id for item in watcher.items})
        except (GitHubError, BoardError) as exc:
            return JobResult("error", f"applications board: {exc}")
        return JobResult("ok", "; ".join(events) if events else "no changes")

    return run


def agents_job(settings: Settings, git: GitSync) -> Callable[[], JobResult]:
    """Mirror the board contracts to GitHub and let the AI teammates take their tickets."""
    from lifeos.agents.service import run_round
    from lifeos.agents.workflows import build_llm_factory, build_release, build_workflows
    from lifeos.connectors.github.client import GitHubClient

    vault = Vault.open(settings.vault_root)

    def run() -> JobResult:
        if not settings.github_token:
            return JobResult("skipped", "LIFEOS_GITHUB_TOKEN not set")
        gh = GitHubClient(settings.github_token)
        repo = next(iter(settings.github_repo_list), None)
        queue, app = build_release(settings)
        events, failed = run_round(
            gh, vault, build_workflows(settings), repo, git=git, timezone=settings.timezone,
            llm_factory=build_llm_factory(settings), eur_per_usd=settings.agents_eur_per_usd,
            queue=queue, app=app,
        )  # fmt: skip
        message = "; ".join(events) if events else "no changes"
        return JobResult("error" if failed else "ok", message)

    return run


def build_jobs(settings: Settings, git: GitSync) -> list[Job]:
    jobs: list[Job] = []
    if settings.moodle_sync_at:
        moodle, missing = build_moodle(settings, Vault.open(settings.vault_root))
        jobs.append(
            Job(
                "moodle-sync",
                Daily(parse_time(settings.moodle_sync_at), ZoneInfo(settings.timezone)),
                moodle_job(moodle, missing, git),
            )
        )
    if settings.applications_interval > 0:
        jobs.append(
            Job(
                "applications-board",
                Every(settings.applications_interval),
                applications_board_job(settings, git),
            )
        )
    if settings.agents_interval > 0:
        jobs.append(Job("agents", Every(settings.agents_interval), agents_job(settings, git)))
    if settings.git_sync_interval > 0:
        jobs.append(Job("git-sync", Every(settings.git_sync_interval), git_sync_job(git)))
    return jobs
