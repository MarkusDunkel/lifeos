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

Google Calendar is the second deliberate write exception: LifeOS reads the chosen input
calendars and writes only into the calendar "LifeOS" it created itself. The OAuth scope
`calendar.app.created` makes Google enforce this; all other calendars are read-only
(`calendar.events.readonly`). See "Planning" below.

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
| `calendar-sync` | every N seconds: input calendars → mirrors in "LifeOS", queue a re-plan when appointments in the horizon changed | `LIFEOS_CALENDAR_SYNC_INTERVAL` |
| `planner-queue` | every minute (with the calendar sync): run the day planner for queued wishes, "plan again", changed goals or appointments | `LIFEOS_CALENDAR_SYNC_INTERVAL` |
| `calendar-planner` | daily at a local time: re-plan the next 7 days | `LIFEOS_PLANNER_AT=17:30` |
| `git-sync` | every N seconds | `LIFEOS_GIT_SYNC_INTERVAL` |
| `control-report` | every N seconds: the AI supervisor explains yellow and red processes (calls the model only when the problems change) | `LIFEOS_CONTROL_REPORT_INTERVAL` |
| `lifeos-manager` | every N seconds: the watch of the LifeOS manager (code only); a repair run for unexplained lights and other events (cooldown), a daily ideas run | `LIFEOS_MANAGER_INTERVAL=120` |

The last run of every job (plus its last 20 runs) is kept in `state/jobs.json`
(`GET /api/jobs/status`) and saved after each job, so a restart – also in the middle of a
tick – neither repeats nor forgets a daily job. The worker runs one job at a time and writes
a heartbeat to `state/worker.json` (`{at, running: {job, since}}`), renewed every 30 s while
a job runs.

### Control room

`/control` shows every process that feeds LifeOS – worker jobs, the PC tasks, manual syncs
and the host deployer – as a flow diagram (sources → processes → datasets → actionables) and a table.
The catalog (`modules/control/catalog.py`) knows each process's place, trigger and
deadlines; the traffic lights are fixed rules evaluated on request (`GET
/api/control/status`): red for a failed run, a missing precondition (e.g. the Google token
lacks the Gmail permission) or a last success older than the deadline; yellow when late or
skipped. Interval jobs warn after 3 and fail after 6 intervals (at least 5/15 min), daily
jobs warn 1 h and fail 4 h after their slot. A stopped worker (no heartbeat for 10 min) turns
every worker job red. A long run does not: the job shows as running, and a job that was fine
when that run began is yellow ("waiting: the worker is busy with …") instead of overdue.

The status has to load when something else is broken: state files are read tolerantly, and a
part that cannot be read (agent settings, actionable inputs, manager notes) becomes a yellow
system check instead of an error. Numbers in an agent's front matter (`daily_limit`, …) are
checked when the file is read; a file with a bad one is reported on the agents page and left
out. The web app keeps a status for 15 s.

- PC tasks (`jobsearch autorun`, `teams-files sync`) report each run to
  `POST /api/control/report/<process>` with `LIFEOS_CONTROL_REPORT_TOKEN` (stored in
  `state/pc-jobs.json`); without the token they show as not set up.
- PC tasks update their own copy of the app first (`lifeos.core.self_update`, for
  `teams-files sync`, `jobsearch autorun`, `claude-usage report`): `git fetch` of
  `LIFEOS_DEPLOY_BRANCH` from their env file (default `main`) and a fast-forward, only when
  the checkout is on that branch without uncommitted changes or local commits; `uv sync
  --frozen --extra pc --inexact` first when `pyproject.toml`/`uv.lock` changed (on failure back to the old
  commit). After an update the task restarts itself on the new code. Otherwise it runs
  unchanged. Each report carries the commit and the update result; the control room shows
  it next to the server's commit (from the deployer heartbeat) and turns a task yellow when
  its update was skipped or failed. `LIFEOS_PC_SELF_UPDATE=false` switches it off.
- The details of a block open in a panel and are part of the address
  (`/control?sel=p:<process>`, `c:<system check>` or a node id), so other pages link to them.
- "Run now" on a worker job writes `state/job-requests.json`; the worker runs it at its next
  tick (after the job it is running). The status reports the open requests per process.
  Manual syncs call their own endpoints.
- The host deployer writes `state/deploy/heartbeat.json` on every run (time and the
  commit the server runs).
- The AI supervisor (`agents/control-room.md` in the vault: `model`, `billing`,
  `daily_limit`, `language`) only explains; it writes `state/control-report.json` and
  creates no tickets or notifications.
- Knowledge folders written by a mirror process carry a marker in the tree and a notice
  above each note: hand edits there are overwritten by the next sync.
- Every flow ends in an **actionable** (catalog node kind `actionable`: University board,
  day plan, job assessments, applications board, LifeOS Tasks). Its light is the light of
  the process making it, at least yellow when a process further upstream is late or failing
  (`modules/control/trust.py`). The panel lists what it is based on: when each input was
  last updated and how much new material its maker has not considered yet.
- Actionables are datasets too. Processes with `second_order=True` read them (and the
  control room itself) and make new actionables; they are drawn in a row below the main
  flow that starts in column 2 (under the vault box) and ends in the actionable column. A
  dataset with `derived_from` (e.g. `boards`, made of the board actionables) takes the worst
  light of its parts.
- Processes with `foundation=True` (vault sync, deployer, ticket copy) are drawn in a band
  below the flow: monitored like the rest, but they end in no actionable.
- **Burned tokens** (second tab below the flow, `GET /api/control/tokens?range=24h|7d|30d`):
  stacked bars of the tokens processed (input, cache reads and writes, output) in three modes.
  - `auto`: runs on the server on its own. These are scheduled worker jobs, including board
    agents that react to tickets.
  - `semi`: needs the owner. This covers "Run now", anything the web app starts, planner
    re-plans after a wish or changed goals, and PC tasks.
  - `interactive`: Claude Code sessions on the PC.

  Every model call (`AnthropicLlm` and `ClaudeCode.call`) appends one line to
  `state/tokens.jsonl` (`core/tokens.py`). Process and mode come from a context variable: the
  scheduler sets it per job, the web app defaults to `semi`, and the board agents add their
  name as detail. The PC task `claude-usage` (`lifeos claude-usage report`, registered with
  `install-autostart --env-file …`, every 15 min) reads `~/.claude/projects/**/*.jsonl` and
  counts each message id once. It only reads files that changed since its last run, and posts
  hourly totals per transcript to `POST /api/control/report/claude-usage/tokens` (report
  token). The server stores them in `state/tokens-interactive.json`, where a resent bucket
  replaces the old one. Only numbers, the model and the project's folder name leave the PC.
  LifeOS's own headless calls (`--no-session-persistence`, `lifeos-claude-*` working dirs)
  are not counted twice.

### LifeOS manager

`lifeos-manager` (`modules/manager/service.py`, agent file `agents/lifeos-manager.md`, needs
`billing: subscription`) looks after the whole system. It has a goal instead of a list of
cases: every yellow or red light in the control room is fixed, or explained by the manager
(verified cause plus a time by which it is green again), or covered by an open ticket for the
owner. The code builds the picture: the control room status, every item of every board
(`LIFEOS_GITHUB_PROJECTS`, applications, University) with fields and recent comments, the
manager's own earlier tickets and its earlier explanations. Two beats decide whether the
model runs:

- The **watch** (every check, code only) looks for work: a light that is yellow or red
  without a valid explanation (none yet, its time is up, it is worse than explained, or the
  ticket that covered it was closed), a ticket that became `AI = blocked`, a pull request
  that went live for an unfinished ticket (`.lifeos/agents/releases.json`; the run checks
  its follow-ups), an owner's comment on one of the manager's tickets. Any of these starts a
  **repair run**, at most one model run per `repair_cooldown_minutes` (agent file, default
  15); it may not create proposals.
- The **ideas run**, once a day from `ideas_at` (agent file, default 07:00 local time),
  looks at everything and may add proposals.

State in `.lifeos/agents/lifeos-manager.json` (`watch.explained`, `watch.attempts`); the
control room shows each explanation next to its process. Every run on a light that stays
yellow or red counts as an attempt; on the third (`MAX_ATTEMPTS`) the manager is told to open
a `needs_you` ticket, and the code opens one itself if it does not. A `needs_you` ticket lists
the processes it covers; while it is open they count as explained (for a day, then looked at
again), and closing it starts the attempts anew.

Before Claude Code runs, the code copies `.lifeos/agents/*.json` to
`.lifeos/backups/lifeos-manager/<time>/` (the last 20) and writes the run marker
`.lifeos/agents/runs-lifeos-manager.json`, so the host deployer does not restart the worker
under it. Claude Code runs with every tool directly in the vault (`access: full`), keeps its
cases in `agents/lifeos-manager/notes.md` and may fix vault files and state itself. It answers
with `explanations` and actions the code checks and applies: `unblock` (clears
`AI = blocked`), `run_now` (a worker job, via `state/job-requests.json`), `comments`,
`create_tickets` in the inbox (`needs_you` → Responsible = owner; `proposal` → the assistant,
capped by `max_open_proposals`), and `pull_requests` against the deploy branch: for each the
code adds a receipt ticket directly in the review column of the inbox board and hands it to
the release pipeline (`Releases.record`): tests, merge, deployment, rollback when the
deployment fails, undo when the owner moves the receipt to the discard column. It never
moves, closes or deletes tickets and skips tickets with `AI = running`. Changed files, the
run log `agents/lifeos-manager/runs.md` and the step log are committed as
`agent(lifeos-manager): …`.

### Study planner

`uni-planner` (`LIFEOS_UNI_PLANNER_INTERVAL`, agent file `agents/uni-planner.md`, board
`LIFEOS_UNIVERSITY_PROJECT`) reads new Moodle mirrors and facts, Teams messages and Teams
files (incrementally, by note revision, up to a character budget per run) plus every ticket
of the University board with the owner's comments, and lets the model create, change, close
or comment tickets (`modules/tasks/planner.py`). It never deletes. New tickets go to `New`;
each change gets a comment with its reason; comments of the owner are binding corrections.
It only calls the model when something is new, at most `daily_limit` times a day.
The model also sets a `Start` date per task (by kind and size, learning from an
`experience` list of earlier outcomes); the code, without a model call, moves a Todo ticket
to In Progress when its start is reached, and does not move it forward again after the
owner moved it back (the next run sets a new start). Tickets past their due date and
tickets without a start trigger a run once a day. Moodle syncs from the web app and the
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
the report lists what was taken over and what not. `set_field` corrects any fact of an entry
(company, place, title, period, degree …).

**Revisions** change content only where a comment asks for it; layout comments may change the
whole document. On every revision the research is checked and completed (with web search),
above all on what the comments ask about. The reviewer gets the previous draft, to see that
uncommented content stayed, and the rendered pages as images (API: image blocks; Claude Code:
PNG files and only the Read tool).

**The draft shapes the CV**: section titles (`sections`), the rows of the last section
(`more_rows`) and the layout (`layout.font_size`, `layout.fill_pages`). The template scales
every vertical gap by `spread`; the renderer bisects the largest spread that keeps the page
count, so the content is spread evenly and the last page is filled.

## AI teammates on the boards

AI agents work on the GitHub boards like team members. The configuration lives in the
vault, the code only executes it:

- `boards/<board>.md` – the **board contract**: columns with owner and description, fields,
  the `ai:` block (`pickup`, `working`, `review` columns, optionally `responsible_field`),
  an optional `inbox:` column and a `## README` section. The worker mirrors it to GitHub
  (status option descriptions, fields, project README and short description) – vault →
  GitHub only. If GitHub was edited by hand and the contract did not change, nothing is
  overwritten: the drift becomes one ticket in the inbox. The contract also names the
  project title and its **views** (`views:` – name, layout, filter, visible fields); views
  are matched by name, created when missing and updated only when their contract entry
  changes, so grouping and sorting set by hand on GitHub survive. The API cannot set
  grouping, sorting or roadmap dates: a view with a `manual:` hint creates one inbox ticket
  with that instruction when it is created.
- `agents/<name>.md` – the **agent definition**: front matter (`title`, `runner`,
  `workflow`, `board`, `model`, `billing`, …) and a human-readable body (summary, workflow, rules with
  origin, review checklist, history, rejected proposals).
- **Whose turn** (`agents/turn.py`): every column has `turn: owner | ai | responsible |
  responsible-ai | none`. After each round the turn is mirrored to the issue itself, because
  project views cannot span projects: the owner's turn = the owner is an assignee, the AI's
  turn = the label `🤖 KI dran`. `AI = blocked` always makes it the owner's turn; closed
  tickets are nobody's. Only assignees and labels set by LifeOS are removed again
  (`.lifeos/agents/turn.json`). Across boards, GitHub's issue dashboard ("Assigned to me",
  a saved search for the label) is the overview.
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
company with web search (capped by `max_searches`; checked and completed on a revision),
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

On a board whose contract names a discard column (`ai: {discard: Closed}`), everything an
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
a separate clone, refuses a change whose added lines contain one of the host's private
patterns (`LIFEOS_PRIVATE_PATTERNS`, one text per line; the file stays on the host, the result
names only file and pattern number), builds the Dockerfile's `test` stage and runs the suite
in a container without network, volumes or secrets. `deploy` accepts only the deploy branch,
waits while an agent run is in progress (a non-empty `runs-*.json` younger than an hour),
updates the checkout, runs `docker compose build && up -d`, waits for `/api/health` and – when
the worker ran before – for the worker to log its jobs and stay up
(`LIFEOS_WORKER_SECONDS`), and restores the previous commit if either fails. Install:

```bash
cp ops/deployer.env.example /srv/lifeos/deploy/deployer.env   # set LIFEOS_DEPLOY_BRANCH
sed "s/^User=.*/User=$USER/" ops/lifeos-deployer.service | sudo tee /etc/systemd/system/lifeos-deployer.service
sudo cp ops/lifeos-deployer.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now lifeos-deployer.timer
```

The app needs `LIFEOS_APP_REPO` and `LIFEOS_DEPLOY_BRANCH` (same branch as the deployer).

When the tests, the merge or the deployment fail, nothing went live: a ticket still in the
review column goes back to the working column with `AI = blocked` and the failure as the
reason (`Releases._back_to_work`). The LifeOS manager's watch sees the new block; its repair
run checks whether the change caused the failure and lifts the block so the agent tries again.

## Career: job profiles and the target curriculum

Job profiles are living documents in the vault (`Career/Profiles/<slug>/Profile.md`, each a
review package). A profile holds its own skills in a table (first header cell `Skill`) with a
share of core / secondary / edge. `Career/Profiles/Overview.md` belongs to the owner: the
profiles with a weight in percent and a status, and the owner's level 0–3 per skill (with
aliases). `modules/career` parses both and calculates the target curriculum
(`importance = Σ weight × share/3`, `priority = importance × (3 − level)/3`); the career page
shows it live, and a generated block between `<!-- lifeos:curriculum:* -->` markers in the
overview records it.

The **profile designer** (workflow `profile-designer`, an agent on LifeOS Tasks) drafts and
sharpens the profiles: research with web search → structured `DesignerOutput` → code checks
and a reviewer against the agent's checklist → at most `max_revisions` revisions → profile
files with a history line, new overview rows (never changing existing ones) and the
curriculum block. Its inputs are the master CV, the application guide, extra vault paths
from `sources:` in the agent file, past application folders and the skill/title counts of
the imported postings (`modules/career/market.py`). Sharpening goes through the review page:
comment, send feedback, move the ticket back to Todo.

**Prefilter** (`modules/career/prefilter.py`): per posting and active profile a fit 0–100 =
60 % skills (the profile's skills found in the posting's skill list and title, or at 0.7 in
the description, weighted core 3 / secondary 2 / edge 1, full at 12 points) + 40 % title
(best word share of a typical title). Location: distance to each home in `homes:` of
`Career/CV/personal.yaml` (label + radius); fully remote postings are detected from the text.
Salary is deliberately not used. A posting passes when it is near a home or remote and its
fit is at least 35. Results are cached in `.lifeos/jobsearch/fit.json` under a signature of
profiles, overview and homes, warmed in the background at start and after each import.
`GET /api/jobsearch/postings` takes `sort=fit`, `profile`, `home`, `reach` and `passed`;
`GET /api/jobsearch/fit/options` lists profiles and homes for the filters.

**AI job matcher** (`modules/career/matcher.py`, agent file `agents/job-matcher.md` with
`runner: job`): worker job `job-matcher` every 10 minutes (registered with the agents, at
most three postings per round). Queued requests ("Assess" in the job list, `manual_limit`
per day) come first, then the best prefiltered postings without an assessment up to
`daily_limit`. Per posting: research with web search, then a structured `Assessment`
(apply / maybe / no, fit per profile, strengths, gaps, red flags, questions, skills no
profile lists). Stored in `.lifeos/jobsearch/assessments/<source>/<id>.json` with a
signature of profiles and overview; changed profiles mark it outdated (not redone
automatically). Skills named missing in `propose_after` assessments become one inbox
proposal each. The job list sorts by `sort=match` (recommendation, then fit) and filters by
`recommended` / `assessed`; `POST /api/jobsearch/postings/{source}/{id}/assess` queues one,
`GET /api/jobsearch/matcher` shows today's counts.

**Commute** (`modules/career/commute.py`): worker job `commute` (hourly, only with
`LIFEOS_ORS_API_KEY`) fetches car and bike times from each home to the posting locations
within its radius via the OpenRouteService matrix API, cached per home and rounded
coordinate in `.lifeos/jobsearch/commute.json`. Rows get `commute` (times per home, the
quickest, a soft factor); sorting by fit or recommendation multiplies the score by the
factor: 1 up to the home's `comfort_minutes`, down to 0.5 at `max_minutes` (flagged beyond),
1 for unknown times and remote postings.

**Dashboard** (`modules/career/dashboard.py`, `GET /api/jobsearch/dashboard`, cached 5 min,
`?refresh=true`): tab "Dashboard" of the job list. Funnel (active → near/remote → prefilter →
assessed with verdicts → in application, backlog in days at the daily limit), demand per
profile (postings with fit ≥ 50, share vs. the profile's weight, new by publication date
this week vs. last), profile × place (homes, remote, top cities; median commute), AI quality
(hit rate, best-profile agreement, prefilter-vs-AI offset with a scatter plot, verdicts per
profile, stale/failed, tokens) and gaps (skills of fitting postings near the homes with the
own level, skills the AI names missing).

## Planning: calendars, goals, the AI day planner, journal

**Calendars** (`connectors/google`, `modules/calendar`): Google sign-in with the web OAuth
flow (token in `state/google-token.json`). The vault file `<planning>/Calendars.md` names the
input calendars and the target. Every sync fetches a rolling window (7 days back, 35 ahead)
of each input and makes the target hold a mirror of each event; mirrors and planned blocks
carry `extendedProperties.private.lifeos_kind` (`mirror` / `plan`). Events in the target
without that marker were added by hand: they are never touched and count as fixed
appointments. The collected events go to `.lifeos/calendar/events.json`.

**Goals** (`modules/planning/goals.py`): `<planning>/Goals.md` with a quota table (first
header cell `Ziel`/`Goal`; amount a number or `auto`, unit hours or sessions per week) and a
rules section (`Regeln`/`Rules`, free text for the planner). `auto` study hours come from the
study readiness (`minutesPerDay` × 7 over subjects with an upcoming exam).

**The day planner** (`modules/planning/planner.py`, agent file `agents/calendar-planner.md`,
`runner: job`, `daily_limit`): the code gathers appointments, free windows (06–23 h), goals
with this week's progress, rules, exams and Moodle deadlines, the last 7 journal days,
skipped blocks, open wishes and the current plan; the model returns the complete plan for
the open part of the 7-day horizon (`PlanResult`, structured output). The code validates
every block (no clash with appointments or running blocks, nothing before now, inside the
horizon), asks once more with the rejections, drops what still fails and writes the
difference to Google (kept ids are patched, new ones inserted, dropped ones deleted). What
the owner changed in Google wins: a moved plan event moves its block, a deleted one marks it
`removed` (`PlanStore.reconcile`). Plan state: `.lifeos/planning/plan.json`; runs:
`.lifeos/planning/runs.jsonl`. Rule proposals of the model become inbox tickets.

**Evening** (`modules/planning/router.py`, page `/plan`): check off today's blocks
(done / partly / skipped; unmarked past blocks count as done) → `<planning>/Log/<YYYY-MM>.jsonl`;
the journal → `<planning>/Journal/<year>/<date>.md`; tomorrow's blocks can be edited (title and
description = the Google event's title and notes; an edited block is the owner's and keeps its
text). The wishes API (`<planning>/Wishes.jsonl`) still exists but has no UI any more.

**Talking to the planner in the calendar.** Every sync makes sure today and the next seven days
have a *day thread*: an all-day, transparent, reminder-less event in the target
(`lifeos_kind=thread`, wording from `thread:` in `Calendars.md`). The owner writes above the
separator line, the planner answers below it (`CalendarSync.answer_thread` re-reads the event
right before writing, so text typed meanwhile is kept). Notes from yesterday to the end of the
horizon go into the prompt (`<day_notes>`, changed ones marked NEW); a changed note queues a
re-plan right away, and the model's `day_notes` answers are written to the threads of the days
they concern. Descriptions of appointments and all-day events reach the planner too. The plan
page shows each day's thread (Evening: today and tomorrow; Week: every day) and can write the
note (`PUT /api/planning/notes/{day}` with the note it showed as `base`; 409 and nothing written
if the calendar holds another text by now). A note saved in the app does *not* queue a run: it
updates `notes_signature`, so the sync does not take it for a phone note, and stays NEW until
"plan again" (`pendingNotes` in the status).

**Owner changes in Google.** The calendar sync reconciles after each run: a plan event moved by
hand moves its block and *pins* it (its time is fixed, it is planned around like a running
block); a deleted one marks the block removed; a changed title or description makes the block
the owner's. Moves and deletions queue a re-plan that waits until nothing changed for five
minutes, so moving several blocks gives one run. Each block carries `touched`, the time LifeOS
last changed it; a sync older than that is not applied to it.

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
| `mcp_server/` | MCP server (study tools) for ChatGPT and other assistants. The learner steers a voice session with control words (`fertig`, `bewerten`, `weiter`, `kenn ich`, `zählt als richtig`, `Schluss`, `Hilfe`) named in the instructions and repeated in every `coach_rule`; after grading, a free follow-up talk runs until `weiter`. The study page shows the same words (`features/study/controls.ts`, a test keeps both in sync) |
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
| `modules/career` | Job profiles and overview (Markdown tables), target curriculum, profile designer workflow, market statistics, posting prefilter (fit, homes, cache), AI job matcher |
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
