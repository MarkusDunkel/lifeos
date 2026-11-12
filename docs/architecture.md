# Architecture

## Three tiers of data sensitivity

| Folder | Contents | Versioning | Leaves the machine? |
|---|---|---|---|
| `app/` | Code only, generic agent templates, docs | git, public GitHub | yes (public) |
| `vault/` | All personal content | git, **no remote**; a pre-push hook rejects every push | only as encrypted backup |
| `deploy/` | `.env` (session secret, password hash, API keys), compose overrides | none | no |

The app never contains personal data, paths or tokens — everything specific comes from
`deploy/.env`. CI runs gitleaks to catch accidental secrets.

GitHub Issues are a deliberate exception: ticket content lives in a *private* GitHub
repository so the board can be used from anywhere and agents can be assigned work.
Highly sensitive material belongs in notes or the knowledge hub, not in issues.

## Runtime

```
Browser / PWA ──HTTPS── web (FastAPI + static React build) ──┐
                                                              ├── /vault (bind mount)
                        worker (jobs, schedules, agents) ─────┘
```

- **web** serves the API under `/api` and the single-page app for every other path, so
  client-side routes survive reloads.
- **worker** (from R4) runs background jobs and Claude agents from the same image.
- **SQLite** holds only derived data (search index, job state). Deleting it loses nothing.

## Backend layout (`backend/src/lifeos`)

| Module | Responsibility |
|---|---|
| `core/vault.py` | Path guard (no `..`, no absolute paths, no symlinks, per-module zones), atomic writes, optimistic locking via content revisions (HTTP 409 on conflict) |
| `core/auth.py` | Single-user login, argon2 password hash, signed HttpOnly session cookie |
| `core/vault_init.py` | Idempotent vault skeleton incl. git and push block |
| `modules/knowledge` | Tree, read/write, FTS5 search, wikilink backlinks |
| `modules/notes`, `workspaces`, `tickets`, `agents` | Upcoming review gates |
| `connectors/` | Ingest/digest adapters (GitHub first) |

## Editing safety

Every write sends the revision the client loaded. If the file changed in the meantime
(Obsidian, an agent, git), the server answers 409 and the UI offers to reload or to
overwrite deliberately. Writes go to a temp file and are atomically renamed.

## Deployment targets

The same `compose.yml` works locally and on a server. Two supported options:

1. **Home server + Tailscale:** data never leaves your network; access from the phone
   through the tailnet.
2. **VPS + reverse proxy (Caddy) with HTTPS:** reachable anywhere; set
   `LIFEOS_COOKIE_SECURE=true`.

A deployment guide follows in R6.
