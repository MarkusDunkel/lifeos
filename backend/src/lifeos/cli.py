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

    tasks = sub.add_parser("tasks", help="helpers for the AI task run (/uni-tasks)")
    tasks.add_argument("action", choices=["input", "check", "done"])
    tasks.add_argument("--vault", type=Path, default=None, help="default: LIFEOS_VAULT_ROOT")
    tasks.add_argument("--repo", default=None, help="board repository (owner/name)")
    tasks.add_argument("--label", default="university")

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

    sub.add_parser("moodle-sync", help="sync Moodle now (same as the button in the app)")
    sub.add_parser("worker", help="run background jobs (vault git sync)")
    sub.add_parser("git-sync", help="sync the vault with its git remote once")

    args = parser.parse_args(argv)
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
    if args.cmd == "tasks":
        return _tasks(args)
    if args.cmd == "moodle-sync":
        return _moodle_sync()
    if args.cmd == "jobsearch":
        return _jobsearch(args)
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


def _print(data) -> None:
    import json

    text = json.dumps(data, indent=2, ensure_ascii=False)
    sys.stdout.buffer.write(text.encode("utf-8") + b"\n")


def _tasks(args) -> int:
    import os

    from lifeos.core.vault import Vault
    from lifeos.modules.tasks.service import TasksError, UniTasks

    root = args.vault or os.environ.get("LIFEOS_VAULT_ROOT")
    if not root:
        print("set --vault or LIFEOS_VAULT_ROOT", file=sys.stderr)
        return 2
    try:
        tool = UniTasks(Vault.open(Path(root)), args.repo, args.label)
        if args.action == "input":
            _print(tool.input())
        elif args.action == "check":
            result = tool.check()
            _print(result)
            return 0 if result["ok"] else 1
        else:
            _print(tool.done())
    except TasksError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


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
        from lifeos.modules.jobsearch.autorun import (
            app_dir,
            autorun,
            default_notifier,
            setup_logging,
        )

        setup_logging(app_dir())
        try:
            outcome = autorun(settings, default_notifier(), app_dir())
        except Exception:  # no console: make sure the log tells what happened
            import logging

            logging.getLogger("lifeos.jobsearch.autorun").exception("autorun failed")
            return 1
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

    try:
        result = run_import(server, devjobs, args.limit, progress)
    except (DevjobsError, ServerError) as exc:
        print(f"\nimport stopped: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nimport interrupted; the next run continues", file=sys.stderr)
        return 130
    finally:
        devjobs.close()
        server.close()
    print(f"\n{result.listed} postings listed on devjobs.at; {result.summary()}")
    return 0


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
