# Architecture

## Three tiers of data sensitivity

| Folder | Contents | Versioning | Leaves the machine? |
|---|---|---|---|
| `app/` | Code only, generic agent templates, docs | git, public GitHub | yes (public) |
| `vault/` | All personal content | git; pushed **only** to its own private sync repository (a pre-push hook rejects every other target) | only to that private repository |
| `deploy/` | `.env` (session secret, password hash, API keys), `state/` (connector tokens), `ssh/` (vault deploy key), compose overrides | none | no |

The app never contains personal data, paths or tokens — everything specific comes from
`deploy/.env`. CI runs gitleaks to catch accidental secrets.

GitHub Issues are a deliberate exception: ticket content lives in a *private* GitHub
repository so the board can be used from anywhere and agents can be assigned work.
Highly sensitive material belongs in notes or the knowledge hub, not in issues.

PowerPoint files live in the user's OneDrive (so they open in PowerPoint for the web);
the vault only mirrors their text. LifeOS reads OneDrive and never writes to it.
Moodle course materials follow the same rule: Moodle keeps the files, the vault their text.

## Runtime

```
Browser / PWA ──HTTPS── web (FastAPI + static React build) ──┐
                                                              ├── /vault (bind mount)
                        worker (jobs, schedules, agents) ─────┘
```

- **web** serves the API under `/api` and the single-page app for every other path, so
  client-side routes survive reloads.
- **worker** (`lifeos worker`, compose profile `worker`) runs scheduled jobs from the same
  image (see below). Planned: more connector syncs and Claude agents.
- **SQLite** holds only derived data (search index, job state). Deleting it loses nothing.

## Vault sync

The hosted app and a laptop work on separate clones of the vault. A private git
repository is the hub between them and doubles as off-site backup:

```
server: web + worker ── vault clone ──auto──▶ private vault repo ◀──pull/push── laptop clone
```

- The worker runs `GitSync` every `LIFEOS_GIT_SYNC_INTERVAL` seconds: commit everything,
  `git fetch`, rebase onto the remote if it moved, push. Status goes to
  `state/git-sync.json`; `GET /api/vault-sync/status` reads it, `POST /api/vault-sync/run`
  syncs now. `lifeos git-sync` does one run from the shell.
- Conflicts stay rare because every file has a single writer (the user or the app). If a
  rebase conflicts anyway, it is aborted, nothing is pushed and the status stays
  `conflict` until a human resolves it on the server.
- On the laptop the interval stays 0; the user pulls before and pushes after working.
- The pre-push hook (`init-vault`) blocks every push unless the URL is allowed explicitly:
  `git config --add lifeos.allowedPushUrl <url>`.

### Commit messages

Writers that know what they changed commit their own paths right away
(`GitSync.commit_paths`), so the vault history shows who changed what:

| Prefix | Writer |
|---|---|
| `moodle:` | daily Moodle job, e.g. `moodle: 3 new, 2 updated` with courses and files in the body |
| `study:` | end of a study session, e.g. `study: Spanish – 15 answers, 12 correct`; unfinished sessions are committed by the sync job as `study: … (session not ended)` |
| `lifeos: auto-sync` | the periodic sync, collecting everything else (e.g. edits in the web app) |

Future writers (OneDrive, tickets, study progress, agents) follow the same pattern.
`git log --grep "^moodle:"` lists only the Moodle updates.

### Worker jobs

| Job | Schedule | Setting |
|---|---|---|
| `moodle-sync` | daily at a local time; missed runs are caught up, failures retried hourly (max. 3 per day) | `LIFEOS_MOODLE_SYNC_AT=05:00`, `LIFEOS_TIMEZONE` |
| `git-sync` | every N seconds | `LIFEOS_GIT_SYNC_INTERVAL` |

The last run of every job is kept in `state/jobs.json` (`GET /api/jobs/status`), so a
restart neither repeats nor forgets a daily job. Moodle syncs from the web app and the
worker exclude each other through a file lock. Only one machine should sync Moodle (the
server): the mirror's state (`.lifeos/moodle/state.json`) is not versioned.

Server setup: create a deploy key with write access for the private repository only, put
it at `deploy/ssh/id_ed25519` with GitHub's host key in `deploy/ssh/known_hosts`, use an
SSH remote URL (`git@github.com:<owner>/<repo>.git`), set `LIFEOS_GIT_SYNC_INTERVAL` in
`deploy/.env` and start with `COMPOSE_PROFILES=worker docker compose up -d`.

## Job search

Job postings come from devjobs.at. It blocks data-centre IPs, so the **importer runs on
the user's PC** (`lifeos jobsearch import`) and sends postings to the server:

1. Fetch `robots.txt` (stop if posting pages are disallowed) and `sitemap-job.xml`, which
   lists every active posting. Search pages (`/jobs/search?`) are never requested.
2. `POST /api/jobsearch/import/diff` with all ids: the server answers with the unknown ones
   and marks known postings that left the sitemap as gone.
3. Fetch only the unknown posting pages (`/job/<id>`), at least one second or twice the
   response time apart, and parse their JSON-LD `JobPosting`. Requests go through `curl`
   with an honest User-Agent; if devjobs.at refuses them, the import stops and reports it.
4. `POST /import/postings` in batches, then `POST /import/finish` with the run's stats.

The import routes accept `Authorization: Bearer <LIFEOS_JOBSEARCH_IMPORT_TOKEN>` (and only
they do); the PC reads `LIFEOS_JOBSEARCH_SERVER_URL` and the same token. The server keeps
postings and their state in `.lifeos/jobsearch/` (not versioned). An interrupted import
continues on the next run because the server knows what it already has. Network trouble
and a restarting server (502–504) are retried with growing pauses; a refusal is not.

**Duplicates.** Company and title are normalised (legal forms, gender suffixes such as
`(m/w/d)` or `:in`, punctuation). A new posting that matches an active one in a shared city
gets `duplicateOf` (the earliest active posting of the job); one that matches only offline
postings gets `repostOf`. When the first posting of a job goes offline, its earliest
duplicate takes over. Postings offline for 90 days are deleted at the end of an import.

**Daily run on the PC.** `lifeos jobsearch install-autostart --env-file <deploy/.env>`
registers a Windows scheduled task (logon, wake from sleep, hourly; current user, no admin,
`pythonw` without console) that runs `lifeos jobsearch autorun`. The autorun stays quiet
if another run is active, the user snoozed it, the server is unreachable or today's import
is complete; otherwise it asks with a notification (Import now / In 1 hour / Not today;
`win11toast` from the optional `pc` extra, a message box as fallback) and reports the
result. If devjobs.at refuses the importer it stops for the day. Log and state live in
`%LOCALAPPDATA%\LifeOS\jobsearch`. `uninstall-autostart` removes the task.

The **Jobs** page of the web app shows the import status and the postings (search, new
since, duplicates and offline postings on request, details with the full text).

## Backend layout (`backend/src/lifeos`)

| Module | Responsibility |
|---|---|
| `core/vault.py` | Path guard (no `..`, no absolute paths, no symlinks, per-module zones), atomic writes, optimistic locking via content revisions (HTTP 409 on conflict) |
| `core/auth.py` | Single-user login, argon2 password hash, signed HttpOnly session cookie |
| `core/vault_init.py` | Idempotent vault skeleton incl. git and push block (allow-list) |
| `core/git_sync.py` | Vault sync with its private remote: commit, rebase, push; conflict-safe |
| `worker/` | `lifeos worker`: scheduler (`scheduler.py`) and jobs (`jobs.py`) |
| `core/locks.py` | File locks shared by the web and worker processes |
| `modules/study` | Question banks in `Studium/`, append-only progress log, exam-aware repetition, readiness |
| `mcp_server/` | MCP server (study tools) for ChatGPT and other assistants |
| `modules/vault_sync` | Sync status and "sync now" endpoints |
| `modules/knowledge` | Tree, read/write, FTS5 search, wikilink backlinks |
| `modules/notes` | JSON Lines store with per-note revisions, column schema (inferred or `schema.json`) |
| `modules/workspaces` | Read-only reports, archive, change logs; md4h feedback rounds (draft → sealed) |
| `modules/tickets` | Ticket board/list and editor over `ready/tickets`, conflict resolution |
| `modules/agents` | Upcoming review gate |
| `connectors/github` | GraphQL client, ingest → 3-way merge, write-back plans and apply |
| `connectors/onedrive` | Microsoft device-code sign-in, read-only Graph client, `.pptx` text extraction, one-way mirror into the knowledge hub |
| `connectors/moodle` | Moodle web service client (read-only), token sign-in, incremental mirror of course materials and overviews |
| `core/extract` | Text extraction to Markdown: PDF (pypdf), PowerPoint, Word, HTML, plain text |
| `modules/tasks` | Deterministic helpers for the AI task run: changed input, draft checks, run log (`lifeos tasks …`) |
| `templates/` | Agent commands installed into the vault by `init-vault` (e.g. `/uni-tasks`) |
| `modules/moodle` | Status, connect, course selection and sync endpoints |
| `modules/presentations` | Status, connect/disconnect and sync endpoints for the mirror |
| `connectors/devjobs` | devjobs.at sitemap and posting pages (JSON-LD), polite client over `curl` |
| `modules/jobsearch` | Posting model, store in `.lifeos/jobsearch/` with duplicate detection, import API, PC importer, autorun and Windows autostart |

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
