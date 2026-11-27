"""Create the vault skeleton. Idempotent: existing files are never overwritten."""

from __future__ import annotations

import subprocess
from pathlib import Path

DIRECTORIES = [
    "inbox",
    "ready/notes",
    "ready/knowledge",
    "ready/workspaces/aenderungsprotokoll",
    "ready/workspaces/archive",
    "agents",
    ".md4h/feedback",
    ".lifeos",
]

GITIGNORE = """\
# Raw ingest data and runtime state are reproducible and stay out of history.
/inbox/
/.lifeos/
*.tmp-*
.obsidian/workspace*.json
.DS_Store
"""

PRE_PUSH_HOOK = """\
#!/bin/sh
# The vault holds private data. Pushing is blocked unless the remote URL is allowed
# explicitly, e.g. a private sync repository:
#   git config --add lifeos.allowedPushUrl <url>
# $1 = remote name, $2 = remote URL
for url in $(git config --get-all lifeos.allowedPushUrl); do
  [ "$2" = "$url" ] && exit 0
done
echo "lifeos vault: pushing to '$2' is blocked (see git config lifeos.allowedPushUrl)." >&2
exit 1
"""

CLAUDE_MD = """\
# Vault rules for agents

This is a private vault, versioned with git. It is pushed only to its own private
sync repository (the pre-push hook blocks every other target).

## Layout
- `inbox/` raw ingest data per connector. Never edit by hand; reproducible.
- `ready/` the workbench ("AI ready"). All manual and AI edits happen here.
  - `notes/` notes as JSON Lines (one note per line).
  - `knowledge/` knowledge hub, an Obsidian-compatible Markdown vault.
  - `workspaces/` short-lived reports; `aenderungsprotokoll/` change logs; `archive/`.
- `.md4h/feedback/` feedback rounds on workspace files. Never modify.

## Rules
- Only write inside `ready/` unless a task explicitly says otherwise.
- Never commit, push or call external write APIs; the app and the user do that.
- Workspace files are never edited after creation; feedback lives in `.md4h/`.
- Markdown must render in "Markdown for Humans": no `==highlight==`, no footnotes,
  no raw HTML or angle brackets outside code, no lists mixing checkboxes and plain items.
- Knowledge hub: extend instead of duplicating, link related notes with [[wikilinks]],
  keep sources.
- Tickets live on GitHub only; there is no copy in the vault. Read them there (issue text
  and comments). The app writes to GitHub; agents do so only where the board rules allow
  it. When an agent's commit belongs to a ticket, its message ends with
  `Refs <owner>/<repo>#<number>`.
"""

GITATTRIBUTES = """* text=auto eol=lf
*.png binary
*.jpg binary
*.pdf binary
*.pptx binary
*.docx binary
"""

FILES = {
    ".gitignore": GITIGNORE,
    ".gitattributes": GITATTRIBUTES,
    "CLAUDE.md": CLAUDE_MD,
}


def init_vault(root: Path) -> list[str]:
    log: list[str] = []
    root.mkdir(parents=True, exist_ok=True)
    for rel in DIRECTORIES:
        d = root / rel
        if not d.exists():
            d.mkdir(parents=True)
            log.append(f"created {rel}/")
        keep = d / ".gitkeep"
        if not rel.startswith((".lifeos", "inbox")) and not any(d.iterdir()):
            keep.touch()
    for rel, content in FILES.items():
        f = root / rel
        if not f.exists():
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content, encoding="utf-8", newline="\n")
            log.append(f"created {rel}")

    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
        subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=root, check=True)
        log.append("initialised git repository")
    hook = root / ".git" / "hooks" / "pre-push"
    if not hook.exists():
        hook.write_text(PRE_PUSH_HOOK, encoding="utf-8", newline="\n")
        hook.chmod(0o755)
        log.append("installed pre-push hook (pushing blocked unless allowed)")
    return log or ["vault already initialised"]
