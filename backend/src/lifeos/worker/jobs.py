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
    if settings.git_sync_interval > 0:
        jobs.append(Job("git-sync", Every(settings.git_sync_interval), git_sync_job(git)))
    return jobs
