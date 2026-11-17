"""Create the vault skeleton. Idempotent: existing files are never overwritten."""

from __future__ import annotations

import json
import subprocess
from importlib.resources import files
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
- Tickets: GitHub is the source of truth, `ready/tickets/` is the editable copy. Agents
  never write to GitHub themselves: they edit the copy (titles, text, labels, project
  fields, new tickets as `new-*.md` files) and the user reviews and sends the changes via
  "Changes" in LifeOS. Only the app writes directly, and only uncritical things: tickets
  the user creates with a button, status moves inside a process the user steers (e.g. an
  application from Active to In Review), closing/reopening as a consequence of the user's
  move, and notice comments with links. When an agent's commit belongs to a ticket, its
  message ends with `Refs <owner>/<repo>#<number>`.
"""

GITATTRIBUTES = """* text=auto eol=lf
*.png binary
*.jpg binary
*.pdf binary
*.pptx binary
*.docx binary
"""

# Claude Code in the vault: the /uni-tasks command may only run the task helpers and
# write ticket drafts and reports. Everything else still asks for permission.
TASKS_CMD = "docker compose -f ../app/compose.yml exec -T web lifeos tasks"
CLAUDE_SETTINGS = json.dumps(
    {
        "permissions": {
            "allow": [
                f"Bash({TASKS_CMD} input)",
                f"Bash({TASKS_CMD} check)",
                f"Bash({TASKS_CMD} done)",
                "Edit(ready/tickets/**)",
                "Write(ready/tickets/**)",
                "Write(ready/workspaces/**)",
            ],
            "deny": ["Bash(git push:*)", "Bash(git commit:*)", "Bash(gh:*)"],
        }
    },
    indent=2,
)


def _template(name: str) -> str:
    return files("lifeos.templates").joinpath(name).read_text(encoding="utf-8")


FILES = {
    ".gitignore": GITIGNORE,
    ".gitattributes": GITATTRIBUTES,
    "CLAUDE.md": CLAUDE_MD,
    ".claude/settings.json": CLAUDE_SETTINGS + "\n",
    ".claude/commands/uni-tasks.md": lambda: _template("commands/uni-tasks.md"),
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
            text = content() if callable(content) else content
            f.write_text(text, encoding="utf-8", newline="\n")
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
