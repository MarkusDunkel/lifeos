# LifeOS

A self-hosted personal operating system: a web app over a plain Markdown vault where
I work together with AI agents — study notes, a knowledge hub, short-lived AI reports
("workspaces") with a feedback loop, and tasks synced with GitHub Issues & Projects.

> Status: early development, built in reviewable increments (see [roadmap](#roadmap)).

## Ideas behind it

- **Files are the source of truth.** All content lives in a vault of Markdown/JSONL
  files, versioned with git. The app, Obsidian, VS Code and AI agents all work on the
  same files; a small SQLite index exists only for search and job state.
- **Data is separated from code by sensitivity.** This repository contains only code.
  The vault is a separate git repository that is never pushed; secrets live in a third
  folder outside of any repository.
- **Ingest → AI-ready → digest.** Connectors import data (`inbox/`) into the workbench
  (`ready/`), where humans and agents edit it; digests write changes back to their
  source (`outbox/` holds the plans and results) — always reviewed before applying.
- **Agents are reviewable.** Every agent run is committed to the vault's git history, so
  each change can be inspected and reverted.

## Features

| Area | Status |
|---|---|
| Login, routing, mobile layout (installable PWA) | ✅ |
| Knowledge hub: tree, Markdown view/edit, `[[wikilinks]]`, backlinks, full-text search | ✅ |
| Notes: tasks & info in a filterable grid | planned |
| Workspaces: AI reports with in-app feedback rounds (md4h-compatible) | planned |
| Agents: Claude Agent SDK runner, schedules, issue-triggered jobs | planned |
| Tickets: GitHub Issues + Projects v2 mirror with 3-way merge and reviewed write-back | planned |

## Architecture

```
lifeos/
├── app/      ← this repo (public): FastAPI backend, React frontend, Docker
├── vault/    ← private data, git without remote (pre-push hook blocks pushing)
└── deploy/   ← .env with secrets, not versioned
```

- **Backend:** Python 3.12+, FastAPI, Pydantic, SQLite (FTS5) — `backend/`
- **Frontend:** React 19, TypeScript, Vite, TanStack Router & Query, Tailwind CSS — `frontend/`
- **Runtime:** one Docker image; the vault is bind-mounted.

More in [docs/architecture.md](docs/architecture.md) and [docs/vault.md](docs/vault.md).

## Getting started

Requirements: Docker, or Python 3.12+ with [uv](https://docs.astral.sh/uv/) and Node 22+.

```bash
mkdir lifeos && cd lifeos
git clone https://github.com/<you>/lifeos.git app
mkdir deploy && cp app/docker/env.example deploy/.env

cd app/backend
uv run lifeos init-vault ../../vault      # vault skeleton + git + push block
uv run lifeos secret                      # -> LIFEOS_SESSION_SECRET
uv run lifeos hash-password               # -> LIFEOS_PASSWORD_HASH

cd .. && docker compose up --build        # http://127.0.0.1:8000
```

### Development

```bash
# backend (http://127.0.0.1:8000)
cd backend
export LIFEOS_VAULT_ROOT=../../vault LIFEOS_SESSION_SECRET=dev-secret-dev-secret-dev-secret-dev LIFEOS_AUTH_DISABLED=true
uv run lifeos serve --reload

# frontend with hot reload (http://localhost:5173, proxies /api)
cd frontend && npm install && npm run dev

# tests
cd backend && uv run pytest
cd frontend && npm test
```

## Roadmap

Development happens in review gates; each gate is usable on its own.

1. **R1** Foundation + knowledge hub
2. **R2** Notes
3. **R3** Workspaces with in-app feedback
4. **R4** Agents (Claude Agent SDK)
5. **R5** GitHub Issues & Projects sync
6. **R6** 24/7 operation: schedules, backups, deployment guide

## License

MIT
