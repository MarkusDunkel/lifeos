"""Command line helpers: ``lifeos serve``, ``lifeos hash-password``, ``lifeos init-vault``."""

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

    args = parser.parse_args(argv)
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


if __name__ == "__main__":
    raise SystemExit(main())
