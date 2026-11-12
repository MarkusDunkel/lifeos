"""Create the vault skeleton. Idempotent: existing files are never overwritten."""

from __future__ import annotations

import subprocess
from pathlib import Path

DIRECTORIES = [
    "inbox",
    "ready/notes",
    "ready/knowledge",
    "ready/tickets",
    "ready/workspaces/aenderungsprotokoll",
    "ready/workspaces/archive",
    "outbox",
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
# The vault holds private data and must never be pushed anywhere.
echo "lifeos vault: pushing is disabled (private data stays local)." >&2
exit 1
"""

CLAUDE_MD = """\
# Vault rules for agents

This is a private vault. It is versioned locally with git and never pushed.

## Layout
- `inbox/` raw ingest data per connector. Never edit by hand; reproducible.
- `ready/` the workbench ("AI ready"). All manual and AI edits happen here.
  - `notes/` notes as JSON Lines (one note per line).
  - `knowledge/` knowledge hub, an Obsidian-compatible Markdown vault.
  - `tickets/` mirror of GitHub issues (YAML frontmatter + Markdown body).
  - `workspaces/` short-lived reports; `aenderungsprotokoll/` change logs; `archive/`.
- `outbox/` digest plans and results (what will be / was written back).
- `.md4h/feedback/` feedback rounds on workspace files. Never modify.

## Rules
- Only write inside `ready/` unless a task explicitly says otherwise.
- Never commit, push or call external write APIs; the app and the user do that.
- Workspace files are never edited after creation; feedback lives in `.md4h/`.
- Markdown must render in "Markdown for Humans": no `==highlight==`, no footnotes,
  no raw HTML or angle brackets outside code, no lists mixing checkboxes and plain items.
- Knowledge hub: extend instead of duplicating, link related notes with [[wikilinks]],
  keep sources.
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
        log.append("installed pre-push hook (pushing disabled)")
    return log or ["vault already initialised"]
