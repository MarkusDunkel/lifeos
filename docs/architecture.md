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
| `application:` | applications: new ticket folder, abandoned applications |
| `agent(<name>):` | a run of an AI teammate: its run log and outputs, e.g. `agent(application-writer): #12 done – Company`, body `Refs owner/repo#12` |
| `lifeos: auto-sync` | the periodic sync, collecting everything else (e.g. edits in the web app) |

Future writers (OneDrive, tickets, study progress, agents) follow the same pattern.
`git log --grep "^moodle:"` lists only the Moodle updates.

### Worker jobs

| Job | Schedule | Setting |
|---|---|---|
| `moodle-sync` | daily at a local time; missed runs are caught up, failures retried hourly (max. 3 per day) | `LIFEOS_MOODLE_SYNC_AT=05:00`, `LIFEOS_TIMEZONE` |
| `applications-board` | every N seconds: close/reopen on outcome columns, roll back deleted tickets | `LIFEOS_APPLICATIONS_INTERVAL` |
| `agents` | every N seconds: mirror board contracts, AI teammates take their tickets | `LIFEOS_AGENTS_INTERVAL` |
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
   response time apart, and parse their JSON-LD `JobPosting`. devjobs.at's Cloudflare
   challenges non-browser clients, so requests go through a real Edge window (Playwright,
   off-screen, persistent profile under `%LOCALAPPDATA%\LifeOS\jobsearch\browser-profile`;
   headless Edge is stopped by the challenge). Without the `pc` extra it falls back to
   `curl`. If devjobs.at still refuses, the import stops and reports it.
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
duplicate takes over. Postings offline for a year (365 days) are deleted at the end of an import.

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

## Tickets: one repository, several projects

All tickets are issues in **one** private repository (`LIFEOS_GITHUB_REPOS`). Projects v2
boards group them by purpose (`LIFEOS_GITHUB_PROJECTS`, e.g. "LifeOS Tasks"; the
applications board is added automatically). An issue lives in exactly one repository and
can be on any number of boards; every board has its own fields (e.g. its own `Status`).

The mirror (`connectors/github`) writes each issue to `ready/tickets/<owner>/<repo>/` with
`projects: {<board title>: {<field>: <value>}}` in the frontmatter (a flat `project:` is read
as the primary board, the first configured). The Tickets page lists them with filters by
project, that project's status, state, label and text; there is no board view, because
statuses differ per project. The editor groups fields per project and can add a ticket
to another project; removing it from a project is done on GitHub.

**Add directly, never change.** GitHub is the source of truth for tickets;
`ready/tickets/` is a read copy (context for agents, search, history). The control over
AI work is the board itself: a ticket an AI creates lands in an inbox column and nothing
happens until the user moves it on. So the rule for every agent – worker or interactive –
is:

| Change | Path to GitHub |
|---|---|
| New ticket in the inbox column (`New` on the inbox board): proposals, notices | direct |
| Comments: start, question, report, reply saying how a comment of the user was read | direct |
| The `AI` field; moving a ticket the agent works on to its working column and to review | direct |
| Tickets the user creates with a button (e.g. **Apply**), mechanical consequences of the user's moves (close/reopen on outcome columns) | direct |
| Titles, texts, labels or fields the user set; closing, deleting; moving beyond review | **never** – the agent comments or proposes instead |
| A batch change the user explicitly asks for in a session | copy → **Changes** → plan → apply |

The **Changes** page stays for such batch changes. Commits of agents that belong to a
ticket end with `Refs <owner>/<repo>#<number>`, which links them in the ticket's timeline.

## Applications

A job application is a ticket on the board "LifeOS Applications"
(`LIFEOS_APPLICATIONS_PROJECT`, created or migrated by `lifeos applications setup-board`):
issues in the board repository with the label `application`, a single-select `Status` with
the columns New, Active, In Review, Sent, 1st/2nd/3rd+ Interview, Offer, Accepted,
Rejected, Declined, and the fields `Interview round`, `Company`, `Posting`. **Apply** on the
Jobs page (`POST /api/applications`) creates the ticket in New, a folder
`ready/knowledge/Career/Applications/<date> <company> – <title>/` with `Application.md` and a
`Posting.md` snapshot, and marks the posting (commit `application: new – …`). Moving the
ticket to **Active** hands it to the AI teammate named in the board contract (see
*AI teammates on the boards*).
`lifeos applications migrate-vault [--commit]` moves the first, German layout
(`Karriere/…`) to the English one.

Deleting an application's issue on GitHub rolls the application back (worker job
`applications-board`): the posting is released for a new application, and the folder moves
to `Career/Applications/Archive/` with `status: abandoned` and a notice in `Application.md`.
Only `Career/Applications/Examples/` serves as examples for new cover letters and
`Career/Applications/Examples/CV/` for new CVs (the rendered CV plus its selection of master
ids); both get there when the ticket reaches **Sent**, i.e. after the user's last review.
The writer reads the three newest of each, the newest as the starting point. Archived drafts
never do. Issues that are only taken off the board are left alone.

The worker job `applications-board` (`LIFEOS_APPLICATIONS_INTERVAL`) polls the board for the
mechanical consequences of the user's moves (state in `.lifeos/applications/board.json`):
outcome columns close the issue; moving it back reopens it.

Documents are rendered with Typst (`typst` Python package) from `templates/typst/` and the
bundled XCharter font; the master CV (`Career/CV/cv.yaml`) and personal data
(`Career/CV/personal.yaml`) live in the vault; `Career/Application-Guide.md` collects rules
and lessons, `Career/Applications/Examples/` earlier cover letters and CVs. A draft may only
select master entries and bullets by id and titles from the allowed variants; companies and
periods always come from the master. `check: true` marks master points still to be confirmed,
`origin` where a fact came from. Technical skills can be grouped (`skills.groups`, one line
per group in the PDF). The cover letter must fit one page, the CV two.

**The owner's comments win.** Every comment of the owner is applied, also against the master
CV or the guide; the reviewer sees the comments too and never asks to undo them. Facts the
owner states in a comment (a period, a new or corrected point, skills, a confirmation) come
back from the writer as `master_updates` (`modules/applications/master.py`): the code applies
them to the master CV *before* the draft is checked and rendered, so the CV uses them in the
same run, and writes `cv.yaml` back with `origin: <ticket> · <comment> · <date>` on every
changed item. An update must cite the number of an owner comment, otherwise it is rejected;
the report lists what was taken over and what not.

## AI teammates on the boards

AI agents work on the GitHub boards like team members. The configuration lives in the
vault, the code only executes it:

- `boards/<board>.md` – the **board contract**: columns with owner and description, fields,
  the `ai:` block (`pickup`, `working`, `review` columns, optionally `responsible_field`),
  an optional `inbox:` column and a `## README` section. The worker mirrors it to GitHub
  (status option descriptions, fields, project README and short description) – vault →
  GitHub only. If GitHub was edited by hand and the contract did not change, nothing is
  overwritten: the drift becomes one ticket in the inbox.
- `agents/<name>.md` – the **agent definition**: front matter (`title`, `runner`,
  `workflow`, `board`, `model`, `billing`, …) and a human-readable body (summary, workflow, rules with
  origin, review checklist, history, rejected proposals).
- `agents/texts.yaml` – the wording of agent comments and run logs (defaults in English).
- `agents/<name>/runs.md` – the run log, appended by every run.

**Taking tickets by state, not by events.** A ticket belongs to an agent when it is open,
sits in the pickup or working column, its `AI` field is empty and – on boards with a
`Responsible` field – that field says `AI: <title>` (on other boards the column names the
agent). A round of the `agents` job (`LIFEOS_AGENTS_INTERVAL`, also `lifeos agents run`):

1. mirror every contract (`lifeos agents sync` does only this),
2. for each such ticket: `AI = running`, move to the working column, start comment (what
   the column means, the numbered workflow steps, link to the agent file at its git
   revision),
3. run the workflow (`agents/workflows.py`, by the definition's `workflow` name),
4. report and move to the review column with `AI` cleared – or, on a question
   (`AgentBlocked`) or an error, comment and set `AI = blocked`; the agent waits until the
   user clears the field,
5. append to the run log and commit `agent(<name>): #N <outcome> – …` with `Refs`.

**The application writer** (`modules/applications/writer.py`, workflow
`application-writer`) is a fixed sequence of model calls with Claude: research the
company with web search (capped by `max_searches`, reused on a revision),
write a `Draft` plus notes as structured output, check it (ids against the master CV and
page limits via Typst, then a separate reviewer call that answers the agent's checklist),
revise at most `max_revisions` times (no further round once `budget_eur` is spent), and
write the PDFs, `draft.json`, research and notes into the application folder. On a
revision it reads the owner's comments since its last run and the previous draft. The
model never acts: board writes, files and commits are done by the code.

**API or subscription.** `billing:` in the agent definition chooses how the model calls
are paid; the workflow is the same:

- `api` (default) – Messages API with prompt caching and server-side refusal fallbacks;
  needs `ANTHROPIC_API_KEY`. Reports show the cost in euro; `budget_eur` caps revisions.
- `subscription` – Claude Code (`claude -p`, `agents/claude_code.py`) on the user's Claude
  plan; needs `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`). Every call runs in an
  empty temporary directory without settings, MCP servers or session files; research may
  only use web search and fetch, writing and reviewing get no tools and a JSON schema. The
  call removes every other `ANTHROPIC_*`/`CLAUDE*` variable and stops as soon as Claude
  Code reports a credential other than the subscription, so it never bills an API key.
  Reports show tokens, the API price of the same calls and the plan's usage; a reached
  usage limit blocks the ticket, and the report warns two weeks before the token expires.
  The image pins the Claude Code version (`CLAUDE_CODE_VERSION` in the Dockerfile).

A ticket left at `running` for 30 minutes (worker restarted mid-run) becomes `blocked`.
Agents write with the user's token, so every agent comment starts with an invisible
marker (`<!-- lifeos-agent: <name> -->`); comments without it are the user's and are the
only instructions an agent follows. State: `.lifeos/agents/` (`boards.json`, `runs-*.json`,
`assistant.json`).

**Comments: feedback and work orders.** One rule for every board, so a new process needs
no rules of its own:

- A comment of the user **without** `KI:` is feedback on the ticket. The responsible agent
  reads it on its next run and reports how it read each comment (for this ticket, or
  general). A general point may come back as a `Proposal` in the `RunResult`; the team
  files it as a ticket in the inbox (`New`), responsible: the assistant.
- A comment **starting with `KI:`** is a work order. The **assistant** (`agents/assistant.py`,
  definition `agents/assistant.md`) answers it within a round on any board: it reads the
  ticket, its recent comments, the agents' summaries and the open inbox tickets, and
  returns a `WorkOrder` (structured output). The code then creates a ticket in the inbox –
  or adds to an open ticket on the same topic – and replies on the original ticket how the
  order was read, where it went and what it means for that ticket. `@KI` is avoided on
  purpose: GitHub would notify an account of that name.

Every new ticket waits in `New` until the user moves it to Todo. Only comments of the
account behind the token count, only those written after the assistant first ran, and
comments are fetched only for issues whose `updatedAt` changed. Without
`agents/assistant.md` no work orders are answered.

**Approved tickets.** A ticket with `Responsible = AI: <assistant>` moved to Todo runs the
`assistant` workflow (`agents/assistant_work.py`, needs `billing: subscription`): Claude
Code works with file tools only (no shell) in a temporary copy of `CLAUDE.md`, `agents/`,
`boards/` and `ready/knowledge/`. The code then applies only allowed changes – agent
definitions and `agents/texts.yaml`, board contracts (only if they still parse), files
below `ready/knowledge/` – and never deletions or files that changed in the vault
meanwhile; everything else is listed in the report. A question blocks the ticket and
applies nothing. The app's code is out of reach on purpose.

With `access: full` in the definition the assistant instead gets every tool (shell, web,
files) without permission prompts and works directly in the vault, inside the worker
container: no root, no Docker, no host – but everything the worker can reach, including
its environment with the user's tokens (a deliberate, temporary choice of the user). The
code finds the changed files with `git status` (files pending before the run are left
alone), commits them and writes a log of every tool call to
`agents/<name>/logs/<date>-<ticket>.md` (values of secret-looking environment variables
redacted). App code changes are asked for as a branch and pull request, never a push to the
main or deployed branch.

### Releasing the assistant's changes – and undoing them

On a board whose contract names a discard column (`ai: {discard: New}`), everything an
agent hands over to review is **live**, and moving the ticket to the discard column undoes
it (`agents/releases.py`):

- **Vault changes** are live once the team committed them. The report links the commit.
- **Pull requests** the agent lists (`RunResult.pull_requests`) in `LIFEOS_APP_REPO` against
  `LIFEOS_DEPLOY_BRANCH` are tested, merged and deployed: the worker queues a `test` request,
  merges the pull request (merge commit, `Refs` the ticket) when the tests pass and queues a
  `deploy` request. Each step is reported on the ticket; a failed deployment rolls back to
  the previous version and undoes the merge on the branch.
- What went live is remembered per ticket (`.lifeos/agents/releases.json`, plus an invisible
  `lifeos-release` marker in the report). A ticket in the discard column whose record is
  `live` is undone once: vault commits with `git revert`, merged pull requests with a revert
  commit on the deploy branch that is deployed. A revert that conflicts (a later change built
  on it) is reported instead of half done. Tickets without a record are left alone.
- Every commit names the ticket (`Refs owner/repo#N`: agent commits, merge and revert
  commits; the pull request gets a "Belongs to" comment), so the ticket's timeline lists all
  of them.

**Host deployer.** The worker has no Docker, so `ops/deployer.py` (standard library only)
runs on the host as the user that owns the checkout, every 20 seconds via systemd. It reads
`<state>/deploy/requests/*.json` and writes `results/*.json`. `test` checks the commit out in
a separate clone, builds the Dockerfile's `test` stage and runs the suite in a container
without network, volumes or secrets. `deploy` accepts only the deploy branch, waits while an
agent run is in progress, updates the checkout, runs `docker compose build && up -d`, waits
for `/api/health` and restores the previous commit if the app does not come up. Install:

```bash
cp ops/deployer.env.example /srv/lifeos/deploy/deployer.env   # set LIFEOS_DEPLOY_BRANCH
sed "s/^User=.*/User=$USER/" ops/lifeos-deployer.service | sudo tee /etc/systemd/system/lifeos-deployer.service
sudo cp ops/lifeos-deployer.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now lifeos-deployer.timer
```

The app needs `LIFEOS_APP_REPO` and `LIFEOS_DEPLOY_BRANCH` (same branch as the deployer).

## Backend layout (`backend/src/lifeos`)

| Module | Responsibility |
|---|---|
| `core/vault.py` | Path guard (no `..`, no absolute paths, no symlinks, per-module zones), atomic writes, optimistic locking via content revisions (HTTP 409 on conflict) |
| `core/auth.py` | Single-user login, argon2 password hash, signed HttpOnly session cookie |
| `core/vault_init.py` | Idempotent vault skeleton incl. git and push block (allow-list) |
| `core/git_sync.py` | Vault sync with its private remote: commit, rebase, push; conflict-safe |
| `worker/` | `lifeos worker`: scheduler (`scheduler.py`) and jobs (`jobs.py`) |
| `core/locks.py` | File locks shared by the web and worker processes |
| `modules/study` | Question banks in `Studium/` (exam date per bank), append-only progress log, exam-aware repetition, readiness, detail API for the study page |
| `mcp_server/` | MCP server (study tools) for ChatGPT and other assistants |
| `modules/vault_sync` | Sync status and "sync now" endpoints |
| `modules/knowledge` | Tree, read/write, FTS5 search, wikilink backlinks |
| `modules/notes` | JSON Lines store with per-note revisions, column schema (inferred or `schema.json`) |
| `modules/workspaces` | Read-only reports, archive, change logs; md4h feedback rounds (draft → sealed) |
| `modules/tickets` | Ticket list (filters by project/status/label) and editor over `ready/tickets`, conflict resolution |
| `agents/` | AI teammates: board contracts and agent definitions (`config.py`), mirror to GitHub (`mirror.py`), taking tickets by state (`team.py`), work orders from `KI:` comments (`assistant.py`), inbox tickets (`inbox.py`), model access API or subscription (`llm.py`, `claude_code.py`), comments and run logs, workflows, one round (`service.py`), agents page API (`admin.py`, `router.py`) |
| `connectors/github` | GraphQL client, ingest → 3-way merge, write-back plans and apply |
| `connectors/onedrive` | Microsoft device-code sign-in, read-only Graph client, `.pptx` text extraction, one-way mirror into the knowledge hub |
| `connectors/moodle` | Moodle web service client (read-only), token sign-in, incremental mirror of course materials and overviews |
| `core/extract` | Text extraction to Markdown: PDF (pypdf), PowerPoint, Word, HTML, plain text |
| `modules/tasks` | Deterministic helpers for the AI task run: changed input, draft checks, run log (`lifeos tasks …`) |
| `templates/` | Agent commands installed into the vault by `init-vault` (e.g. `/uni-tasks`) |
| `modules/moodle` | Status, connect, course selection and sync endpoints |
| `modules/presentations` | Status, connect/disconnect and sync endpoints for the mirror |
| `connectors/devjobs` | devjobs.at sitemap and posting pages (JSON-LD), polite client over Edge (Playwright) or `curl` |
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
