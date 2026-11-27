"""Command line helpers: ``lifeos serve``, ``lifeos worker``, ``lifeos init-vault`` and more."""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from pathlib import Path

from lifeos.core.auth import hash_password
from lifeos.core.vault_init import init_vault


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lifeos")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="run the web server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")

    sub.add_parser("hash-password", help="print an argon2 hash for LIFEOS_PASSWORD_HASH")
    sub.add_parser("secret", help="print a random value for LIFEOS_SESSION_SECRET")

    init = sub.add_parser("init-vault", help="create the vault skeleton (idempotent)")
    init.add_argument("path", type=Path)

    imp = sub.add_parser("import", help="copy a folder into the vault (never overwrites)")
    imp.add_argument("src", type=Path, help="source folder")
    imp.add_argument("dest", help="destination inside the vault, e.g. ready/knowledge/Studium")
    imp.add_argument("--vault", type=Path, required=True)
    imp.add_argument("--dry-run", action="store_true")

    jobs = sub.add_parser("jobsearch", help="job search: import postings on the PC")
    jobs.add_argument(
        "action",
        choices=["import", "autorun", "install-autostart", "uninstall-autostart"],
        help="import: now, with progress; autorun: what the scheduled task runs (asks first); "
        "install-/uninstall-autostart: register the Windows scheduled task",
    )
    jobs.add_argument("--limit", type=int, default=None, help="fetch at most N new postings")
    jobs.add_argument(
        "--env-file", type=Path, default=None, help="file with LIFEOS_JOBSEARCH_* settings"
    )

    apps = sub.add_parser("applications", help="job applications board")
    apps.add_argument("action", choices=["setup-board", "migrate-vault"])
    apps.add_argument("--vault", type=Path, default=None, help="default: LIFEOS_VAULT_ROOT")
    apps.add_argument(
        "--commit", action="store_true", help="commit and sync the moved files (server only)"
    )

    agents = sub.add_parser("agents", help="AI teammates on the GitHub boards")
    agents.add_argument(
        "action",
        choices=["sync", "run"],
        help="sync: mirror the board contracts (vault boards/) to GitHub; "
        "run: one full round (mirror, then agents take tickets) – what the worker does",
    )

    sub.add_parser("moodle-sync", help="sync Moodle now (same as the button in the app)")
    teams_files = sub.add_parser(
        "teams-files", help="mirror the Teams files (local OneDrive sync folder) on the PC"
    )
    teams_files.add_argument("action", choices=["sync", "install-autostart", "uninstall-autostart"])
    teams_files.add_argument("--env-file", type=Path, default=None, help="e.g. deploy/.env")
    teams_files.add_argument(
        "--source", type=Path, default=None, help="default: LIFEOS_TEAMS_FILES_DIR"
    )
    teams_files.add_argument(
        "--commit", action="store_true", help="commit the notes and sync the vault afterwards"
    )
    teams_mail = sub.add_parser(
        "teams-mail", help="import the Teams messages from Gmail (read-only) into the vault"
    )
    teams_mail.add_argument(
        "--commit", action="store_true", help="commit the notes and sync the vault afterwards"
    )
    sub.add_parser("worker", help="run background jobs (vault git sync)")
    sub.add_parser("git-sync", help="sync the vault with its git remote once")
    usage = sub.add_parser(
        "claude-usage", help="report the tokens of Claude Code sessions on the PC"
    )
    usage.add_argument("action", choices=["report", "install-autostart", "uninstall-autostart"])
    usage.add_argument(
        "--env-file", type=Path, default=None, help="file with LIFEOS_CONTROL_* settings"
    )

    args = parser.parse_args(argv)
    if _scheduled(args):
        restarted = _self_update(args, sys.argv[1:] if argv is None else argv)
        if restarted is not None:
            return restarted
    if args.cmd == "claude-usage":
        return _claude_usage(args)
    if args.cmd == "worker":
        import logging

        from lifeos.worker.runner import run_worker

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
        # httpx logs every request URL at INFO, and Moodle download URLs carry the token.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        run_worker()
        return 0
    if args.cmd == "git-sync":
        from dataclasses import asdict

        from lifeos.core.config import get_settings
        from lifeos.core.git_sync import git_sync_for

        result = git_sync_for(get_settings()).run()
        _print(asdict(result))
        return 0 if result.status in ("ok", "skipped") else 1
    if args.cmd == "moodle-sync":
        return _moodle_sync()
    if args.cmd == "teams-files":
        return _teams_files(args)
    if args.cmd == "teams-mail":
        from lifeos.core.config import get_settings
        from lifeos.core.git_sync import git_sync_for
        from lifeos.worker.jobs import teams_mail_job

        settings = get_settings()
        if not args.commit:
            from lifeos.connectors.teamsmail.setup import build_teams_mail
            from lifeos.connectors.teamsmail.sync import report_dict
            from lifeos.core.vault import Vault

            _print(report_dict(build_teams_mail(settings, Vault.open(settings.vault_root)).run()))
            return 0
        result = teams_mail_job(settings, git_sync_for(settings))()
        print(f"{result.status}: {result.message}")
        return 0 if result.status == "ok" else 1
    if args.cmd == "jobsearch":
        return _jobsearch(args)
    if args.cmd == "agents":
        return _agents(args)
    if args.cmd == "applications":
        if args.action == "migrate-vault":
            return _applications_migrate(args)
        return _applications_setup()
    if args.cmd == "import":
        from lifeos.core.importer import import_tree

        report = import_tree(args.src, args.vault, args.dest, dry_run=args.dry_run)
        verb = "would copy" if args.dry_run else "copied"
        for f in report.copied:
            print(f"{verb}  {f}")
        for f in report.skipped_existing:
            print(f"exists  {f} (skipped)")
        print(f"{len(report.copied)} {verb}, {len(report.skipped_existing)} skipped")
        return 0
    if args.cmd == "serve":
        import uvicorn

        uvicorn.run(
            "lifeos.main:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
    elif args.cmd == "hash-password":
        pw = getpass.getpass("Password: ")
        if pw != getpass.getpass("Repeat: "):
            print("passwords differ", file=sys.stderr)
            return 1
        print(hash_password(pw))
    elif args.cmd == "secret":
        print(secrets.token_urlsafe(48))
    elif args.cmd == "init-vault":
        for line in init_vault(args.path):
            print(line)
    return 0


# the PC tasks that run on their own (scheduled task or deploy/teams-files-sync.cmd)
SCHEDULED = {("teams-files", "sync"), ("jobsearch", "autorun"), ("claude-usage", "report")}


def _scheduled(args: argparse.Namespace) -> bool:
    return (args.cmd, getattr(args, "action", None)) in SCHEDULED


def _self_update(args: argparse.Namespace, argv: list[str]) -> int | None:
    """Update the PC's copy of the app to the deploy branch before the task runs.

    Returns the exit code of the restarted task when the code changed, else ``None`` (the
    task runs here). The result goes with the task's report to the control room.
    """
    import os
    import subprocess

    from lifeos.core import self_update
    from lifeos.modules.control.reporter import load_settings, set_app_update

    passed = os.environ.get(self_update.ENV_RESULT)
    if passed is not None:  # the restarted task: the update already ran
        set_app_update(self_update.UpdateResult.from_env(passed))
        return None
    root = self_update.app_root()
    settings = load_settings(args.env_file)
    if not settings.pc_self_update:
        version = self_update.current_version(root)
        result = self_update.UpdateResult("off", "self-update off (LIFEOS_PC_SELF_UPDATE)", version)
    else:
        if sys.prefix != sys.base_prefix:  # uv sync must update the env this task runs in
            os.environ.setdefault("UV_PROJECT_ENVIRONMENT", sys.prefix)
        result = self_update.update_app(root, settings.deploy_branch)
    set_app_update(result)
    if not result.changed:
        return None
    env = os.environ | {self_update.ENV_RESULT: result.to_env()}
    try:
        return subprocess.run([sys.executable, "-m", "lifeos.cli", *argv], env=env).returncode
    except OSError as exc:  # run on the old code rather than not at all
        set_app_update(self_update.UpdateResult("failed", f"restart failed: {exc}", None))
        return None


def _claude_usage(args: argparse.Namespace) -> int:
    from lifeos.modules.control import claude_usage

    try:
        if args.action == "uninstall-autostart":
            print(f"removed scheduled task {claude_usage.uninstall()}")
            return 0
        if args.action == "install-autostart":
            if args.env_file is None:
                print("pass --env-file with LIFEOS_CONTROL_* settings", file=sys.stderr)
                return 2
            print(f"installed scheduled task {claude_usage.install(args.env_file)}")
            return 0
    except claude_usage.AutostartError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return claude_usage.run(args.env_file)


def _print(data) -> None:
    """Print ``data`` as UTF-8 JSON; never fails.

    Scheduled tasks run under ``pythonw`` where ``sys.stdout`` is ``None``. The output is
    only informative (the result goes to the control panel), so it is skipped there and a
    failing write never stops the caller (e.g. before the vault commit).
    """
    import json

    out = sys.stdout
    if out is None:
        return
    text = json.dumps(data, indent=2, ensure_ascii=False)
    try:
        buffer = getattr(out, "buffer", None)
        if buffer is not None:
            buffer.write(text.encode("utf-8") + b"\n")
            buffer.flush()
        else:
            out.write(text + "\n")
    except (OSError, ValueError, UnicodeError):
        pass


def _applications_migrate(args) -> int:
    import os

    from lifeos.core.vault import Vault
    from lifeos.modules.applications.migrate import changed_paths, migrate_vault

    root = args.vault or os.environ.get("LIFEOS_VAULT_ROOT")
    if not root:
        print("set --vault or LIFEOS_VAULT_ROOT", file=sys.stderr)
        return 2
    moved = migrate_vault(Vault.open(Path(root)))
    for old, new in moved:
        print(f"{old} -> {new}")
    print(f"{len(moved)} item(s) moved")
    if moved and args.commit:
        from lifeos.core.config import get_settings
        from lifeos.core.git_sync import git_sync_for

        git = git_sync_for(get_settings())
        message = "application: move career data to English names\n"
        committed = git.commit_paths(changed_paths(), message)
        print(f"committed {len(committed)} file(s); sync: {git.run().status}")
    return 0


def _agents(args) -> int:
    from lifeos.agents.service import run_round
    from lifeos.agents.workflows import build_llm_factory, build_release, build_workflows
    from lifeos.connectors.github.client import GitHubClient
    from lifeos.core.config import get_settings
    from lifeos.core.git_sync import git_sync_for
    from lifeos.core.vault import Vault

    settings = get_settings()
    if not settings.github_token:
        print("set LIFEOS_GITHUB_TOKEN", file=sys.stderr)
        return 2
    queue, app = build_release(settings)
    events, failed = run_round(
        GitHubClient(settings.github_token),
        Vault.open(settings.vault_root),
        build_workflows(settings),
        next(iter(settings.github_repo_list), None),
        git=git_sync_for(settings),
        timezone=settings.timezone,
        mirror_only=args.action == "sync",
        llm_factory=build_llm_factory(settings),
        eur_per_usd=settings.agents_eur_per_usd,
        queue=queue,
        app=app,
    )
    for line in events or ["no changes"]:
        print(line)
    return 1 if failed else 0


def _applications_setup() -> int:
    from lifeos.connectors.github.client import GitHubClient, GitHubError
    from lifeos.core.config import get_settings
    from lifeos.modules.applications.board import BoardError, setup_board

    settings = get_settings()
    repo = settings.applications_repo or next(iter(settings.github_repo_list), None)
    if not settings.github_token or not repo:
        print("set LIFEOS_GITHUB_TOKEN and LIFEOS_APPLICATIONS_REPO", file=sys.stderr)
        return 2
    try:
        ref = setup_board(GitHubClient(settings.github_token), repo, settings.applications_project)
    except (GitHubError, BoardError) as exc:
        print(f"setup failed: {exc}", file=sys.stderr)
        return 1
    print(f"board ready: LIFEOS_APPLICATIONS_PROJECT={ref}")
    if not settings.applications_project:
        print("add that line to deploy/.env, plus LIFEOS_APPLICATIONS_INTERVAL=120 for the worker")
    return 0


# outcome of `jobsearch autorun` → control room status (the rest counts as skipped)
AUTORUN_STATUS = {
    "imported": "ok", "done-today": "ok", "failed": "error", "blocked": "error",
    "not-configured": "error",
}  # fmt: skip


def _jobsearch(args) -> int:
    from lifeos.connectors.devjobs.client import DevjobsClient, DevjobsError
    from lifeos.modules.jobsearch.importer import (
        ImportServer,
        ServerError,
        load_settings,
        run_import,
    )

    if args.action in ("install-autostart", "uninstall-autostart"):
        from lifeos.modules.jobsearch.autostart import AutostartError, install, uninstall

        try:
            if args.action == "uninstall-autostart":
                print(f"removed scheduled task {uninstall()}")
                return 0
            if args.env_file is None:
                print("--env-file is required (the task reads its settings there)", file=sys.stderr)
                return 2
            if load_settings(args.env_file).missing():
                print("the env file lacks LIFEOS_JOBSEARCH_* settings", file=sys.stderr)
                return 2
            print(f"installed scheduled task {install(args.env_file)}")
        except AutostartError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0

    settings = load_settings(args.env_file)
    if args.action == "autorun":
        from lifeos.modules.control.reporter import reported
        from lifeos.modules.jobsearch.autorun import (
            app_dir,
            autorun,
            default_notifier,
            setup_logging,
        )

        setup_logging(app_dir())
        with reported("job-import", args.env_file) as run:
            try:
                outcome = autorun(settings, default_notifier(), app_dir())
            except Exception:  # no console: make sure the log tells what happened
                import logging

                logging.getLogger("lifeos.jobsearch.autorun").exception("autorun failed")
                run.message = "autorun failed, see the log on the PC"
                return 1
            run.status = AUTORUN_STATUS.get(outcome, "skipped")
            run.message = outcome
        import logging

        logging.getLogger("lifeos.jobsearch.autorun").info("autorun: %s", outcome)
        return 0
    if settings.missing():
        print(f"job import is not set up: {', '.join(settings.missing())}", file=sys.stderr)
        return 2
    server = ImportServer(settings.jobsearch_server_url, settings.jobsearch_import_token)
    devjobs = DevjobsClient()

    def progress(done: int, total: int) -> None:
        if done == total or done % 10 == 0:
            print(f"\r{done}/{total} postings fetched", end="", flush=True)

    from lifeos.modules.control.reporter import reported

    with reported("job-import", args.env_file) as run:
        try:
            result = run_import(server, devjobs, args.limit, progress)
        except (DevjobsError, ServerError) as exc:
            print(f"\nimport stopped: {exc}", file=sys.stderr)
            run.message = f"import stopped: {exc}"
            return 1
        except KeyboardInterrupt:
            print("\nimport interrupted; the next run continues", file=sys.stderr)
            run.status, run.message = "skipped", "interrupted"
            return 130
        finally:
            devjobs.close()
            server.close()
        run.status, run.message = "ok", result.summary()
    print(f"\n{result.listed} postings listed on devjobs.at; {result.summary()}")
    return 0


def _teams_files(args: argparse.Namespace) -> int:
    from dataclasses import asdict

    from lifeos.connectors.folder.sync import FolderError, FolderMirror, load_settings

    if args.action in ("install-autostart", "uninstall-autostart"):
        from lifeos.connectors.folder.autostart import AutostartError, install, uninstall

        try:
            if args.action == "uninstall-autostart":
                print(f"removed scheduled task {uninstall()}")
                return 0
            if args.env_file is None:
                print("--env-file is required (the task reads its settings there)", file=sys.stderr)
                return 2
            if load_settings(args.env_file).teams_files_dir is None:
                print("the env file lacks LIFEOS_TEAMS_FILES_DIR", file=sys.stderr)
                return 2
            print(f"installed scheduled task {install(args.env_file)}")
        except AutostartError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0

    from lifeos.core.git_sync import GitSync, file_list
    from lifeos.core.vault import Vault

    settings = load_settings(args.env_file)
    source = args.source or settings.teams_files_dir
    if source is None:
        print("no source folder: pass --source or set LIFEOS_TEAMS_FILES_DIR", file=sys.stderr)
        return 2
    from lifeos.modules.control.reporter import reported

    mirror = FolderMirror(Vault.open(settings.vault_root), source, settings.teams_files_target)
    with reported("teams-files", args.env_file) as run:
        try:
            report = mirror.run()
        except FolderError as exc:
            print(str(exc), file=sys.stderr)
            run.message = str(exc)
            return 1
        _print({**asdict(report), "summary": report.summary()})
        run.status, run.message = "ok", report.summary()
        if args.commit:
            git = GitSync(settings.vault_root)
            committed = git.commit_paths(
                [mirror.target_dir],
                lambda files: f"teams-files: {report.summary()}\n\n{file_list(files)}",
            )
            if committed:
                sync = git.run()
                _print(asdict(sync))
                if sync.status not in ("ok", "skipped"):
                    run.status = "error"
                    run.message += f"; vault sync {sync.status}: {sync.message}"
        failed = bool(report.errors) and not report.changed
        if report.errors:
            run.status = "error" if failed else run.status
            run.message += f"; {len(report.errors)} error(s)"
    return 1 if failed else 0


def _moodle_sync() -> int:
    from dataclasses import asdict

    from lifeos.connectors.moodle.client import MoodleError
    from lifeos.connectors.moodle.setup import build_moodle
    from lifeos.core.config import get_settings
    from lifeos.core.vault import Vault

    settings = get_settings()
    sync, missing = build_moodle(settings, Vault.open(settings.vault_root))
    if missing:
        print(f"Moodle is not set up ({', '.join(missing)})", file=sys.stderr)
        return 2
    try:
        report = asdict(sync.sync())
    except MoodleError as exc:
        print(f"Moodle: {exc}", file=sys.stderr)
        return 1
    _print(
        {k: v for k, v in report.items() if k not in ("created", "updated", "unchanged")}
        | {
            "created": len(report["created"]),
            "updated": len(report["updated"]),
            "unchanged": report["unchanged"],
        }
    )
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
