# Vault specification

```
vault/
├── inbox/                 raw ingest data per connector (gitignored, reproducible)
├── ready/                 the workbench ("AI ready") — the only place humans and agents edit
│   ├── notes/             notes as JSON Lines
│   ├── knowledge/         knowledge hub, an Obsidian-compatible Markdown vault
│   │   ├── Presentations/ text mirrors of OneDrive PowerPoints (read-only)
│   │   └── Moodle/        text mirrors of Moodle course materials (read-only)
│   ├── tickets/           GitHub issue mirror: <owner>/<repo>/<number>-<slug>.md
│   └── workspaces/        short-lived AI reports (flat)
│       ├── aenderungsprotokoll/   change log per processed report
│       └── archive/               processed reports, moved unchanged
├── outbox/                digest plans and results per connector
├── agents/                personal agent overrides and context
├── .md4h/feedback/        feedback rounds on workspace files (md4h v2 format)
├── .lifeos/               runtime state: indexes, merge bases (gitignored)
└── CLAUDE.md              rules for agents working in the vault
```

## Data flow

```
source ──ingest──▶ inbox/ ──normalise/merge──▶ ready/ ◀── humans, agents
                                                 │
                                   digest plan ──▶ outbox/<connector>/<ts>_plan.md
                                                 │   (reviewed by the user)
                                   apply ────────▶ source, result in outbox/
```

## Notes

`ready/notes/*.jsonl` holds one note per line:

```json
{"cells": {"body": "…", "created": "2026-01-02T10:00:00.000Z", "lastChanged": "…", "type": "Task"}, "id": "<uuid>", "order": 3}
```

- `cells` is an open set of fields; the app keeps fields it does not know.
- `created` and `lastChanged` are maintained by the app (ISO 8601, UTC).
- New notes go to `notes-<year>.jsonl`. Saving rewrites only the changed line (keys
  sorted, UTF-8 unescaped), so diffs stay minimal. Lines that are not notes are kept.
- The revision of a note is a hash of its line: concurrent edits of different notes
  never conflict; a stale edit of the same note gets HTTP 409.
- The grid infers column types from the data (long text, category, date, timestamp).
  An optional `ready/notes/schema.json` sets labels, options, order and hidden fields:

```json
{"fields": [
  {"key": "type", "label": "Type", "kind": "select", "options": ["Task", "Info"]},
  {"key": "source", "hidden": true}
]}
```

`kind` is one of `text`, `select`, `string`, `date`, `datetime`. Select fields still
accept new values.

## Tickets (GitHub)

```
GitHub ──sync──▶ inbox/github/<owner>/<repo>/issues.json        raw, reproducible
               ▶ ready/tickets/<owner>/<repo>/<number>-<slug>.md  3-way merge
               ▶ .lifeos/github/base/…/<number>.json             merge base = GitHub state seen last
ready/tickets ──plan──▶ outbox/github/<ts>_plan.md   reviewed by the user
              ──apply─▶ GitHub, outbox/github/<ts>_result.md, then sync again
```

- Ticket files hold `title`, `state`, `labels`, `assignees`, `project` (Projects v2 fields
  of type single select, text, number, date) and the issue text; `number`, `repo`, `url`
  and `updated` are maintained by the sync. A file without `number` is a new ticket.
- Sync merges per field: a change on one side wins, labels and assignees merge as sets,
  the text merges line by line (`git merge-file`). If both sides changed the same field,
  the local value stays, the field is listed under `conflicts`, and plans are blocked
  until it is resolved ("Use GitHub" or "Keep mine").
- A plan lists every local change with a diff and ends with a machine-readable block.
  Applying it skips an operation if the file or the issue changed after the plan.
- Comments on issues are not mirrored yet.

## Presentations (OneDrive)

```
OneDrive/<LIFEOS_ONEDRIVE_FOLDER>/**/*.pptx ──sync──▶ ready/knowledge/<LIFEOS_PRESENTATIONS_DIR>/**/<name>.md
                                                     .lifeos/onedrive/presentations.json   drive item id → note, content tag
```

- OneDrive is the source of truth for the presentations; the vault holds only their text,
  so the vault's git history stays small and diffs show which slide changed. LifeOS only
  reads from OneDrive (`Files.Read`).
- Each mirror note has the frontmatter `type: presentation`, `url` (opens the file in
  PowerPoint for the web), `onedrive_path`, `drive_item_id`, `modified`, `slides` and
  `mirror: true`, followed by one `## Slide n · Title` section per slide with its text,
  tables and speaker notes. Images and layout are not mirrored.
- Mirror notes are read-only: the next change of the presentation overwrites them. Write
  your own thoughts in a separate note that links to the mirror.
- Files are tracked by their drive item id: a renamed or moved presentation moves its
  note. A presentation that disappears keeps its note with `status: deleted`. A note at the
  target path that is not the mirror of that file is never overwritten (reported instead).

### Setup

1. Register an app at <https://entra.microsoft.com> → App registrations → New registration:
   supported account types "Personal Microsoft accounts only". Under Authentication
   enable "Allow public client flows". Under API permissions add Microsoft Graph
   (delegated) `Files.Read` and `offline_access`. No client secret is needed.
2. In `deploy/.env` set `LIFEOS_MS_CLIENT_ID` (the application id) and
   `LIFEOS_ONEDRIVE_FOLDER` (e.g. `Presentations`); optionally `LIFEOS_PRESENTATIONS_DIR`.
3. Create `deploy/state/` (Docker mounts it as `/state`; `LIFEOS_STATE_DIR` points there).
   The Microsoft refresh token is stored there — outside the vault, never in git.
4. In the app: Settings → Presentations → "Connect Microsoft account", enter the code
   shown at microsoft.com/devicelogin, then "Sync presentations".

## Moodle

```
Moodle ──sync──▶ ready/knowledge/<LIFEOS_MOODLE_DIR>/<Course>/<Course>.md          overview per course
               ▶ ready/knowledge/<LIFEOS_MOODLE_DIR>/<Course>/<NN Section>/<File>.md  text mirror per file
               ▶ .lifeos/moodle/state.json   what was taken over: per file timemodified + size
```

- Moodle is the source of truth; files stay there. The vault gets the text of PDFs, `.pptx`,
  `.docx`, Moodle pages (HTML) and text files, each with frontmatter `type: moodle-file`,
  `course`, `section`, `url` (the activity in Moodle), `file`, `moodle_id`, `modified`,
  `pages` and `mirror: true`. Other files (videos, archives, …) and files over 50 MB are only
  linked from the course overview. A PDF without text (a scan) gets a note saying so.
- The course overview (`type: moodle-course`) lists every section with its description,
  links to the mirrored files (`[[wikilinks]]`) and to all other activities in Moodle.
- A sync only downloads files whose `timemodified` or size changed. Moved or renamed files
  move their note; removed ones keep it with `status: deleted`. Notes that are not the mirror
  of that file are never overwritten. Only the selected courses are synced (by default the
  current ones; see the Moodle page).
- LifeOS only reads from Moodle. Sign-in: username and password are sent once to
  `login/token.php` (service `moodle_mobile_app`); only the resulting token is stored, in
  `LIFEOS_STATE_DIR/moodle-token.json` outside the vault. If the site blocks this, paste a
  token from Moodle → Preferences → Security keys instead.

- Activity dates from Moodle (opens, due, cut-off, closes) are shown next to each activity in
  the course overview, plus a "Deadlines" section. Assignments and quizzes of the synced
  courses (due date, submission status, description, link) are written to
  `inbox/moodle/tasks.json` as facts for the task run below. Dates use `LIFEOS_TIMEZONE`.

Setup: set `LIFEOS_MOODLE_URL` (e.g. `https://moodle.example.edu`) and optionally
`LIFEOS_MOODLE_DIR` in `deploy/.env`, restart, then connect on the Moodle page.

Daily sync: with `LIFEOS_MOODLE_SYNC_AT=05:00` the worker syncs every day at that time and
commits the mirror right away as `moodle: 3 new, 2 updated` (courses and files in the
commit body), then pushes. Let only one machine sync Moodle (the server); the selection and
sync state in `.lifeos/moodle/state.json` are not versioned, copy them once when moving.

## University tasks (AI drafts, reviewed by you)

```
/uni-tasks (Claude Code, run in the vault folder)
  lifeos tasks input  → changed mirror notes since the last run, Moodle task facts,
                        existing tickets with their task markers, hints
  AI                  → ticket drafts ready/tickets/<owner>/<board>/new-*.md (label university)
                        + report ready/workspaces/uni-tasks-<date>.md
  lifeos tasks check  → validates the drafts;  lifeos tasks done → run log
you: Tickets → Changes → create plan → Apply
```

- The command is installed by `lifeos init-vault` as `.claude/commands/uni-tasks.md` (only if
  missing — afterwards it is the vault's own copy and can be refined). `.claude/settings.json`
  allows exactly the three helper commands and writes to `ready/tickets` and
  `ready/workspaces`; git commits, pushes and `gh` are denied.
- Every drafted ticket carries one marker `<!-- lifeos:task <source> -->` in its text:
  `moodle:assign:<id>`, `moodle:quiz:<id>` or `note:<moodle_id>:<short id>`. It survives on
  GitHub, so a task is recognised after its issue was created and never drafted twice.
- The run log `.lifeos/agents/uni-tasks.json` stores the revision of every processed mirror
  note and the facts seen, so the next run only reads what changed. Hints: a due date that
  changed in Moodle (`due_changed`) and a submission in Moodle (`submitted`, suggested once).
  A due date you changed by hand is kept.
- The helpers run inside the container: `docker compose -f ../app/compose.yml exec -T web
  lifeos tasks input|check|done`. `lifeos moodle-sync` runs the Moodle sync from the shell.

## Study (question banks, progress, MCP)

```
ready/knowledge/Studium/<Subject>/_Fach.md            subject: fach, kürzel, prüfung (ISO date/time), prüfungsformat, sprache
ready/knowledge/Studium/<Subject>/<Topic> - Prüfen.md  question bank (cards)
ready/knowledge/Studium/<Subject>/<Topic>.md           learning text, read section by section (## headings)
ready/knowledge/Studium/<Subject>/lernstand.jsonl      progress log, one line per answer (append-only)
```

- A card is `**ID** – title` followed by `- Key: value` lines (e.g. `Definition`,
  `Lückensatz`, `Funktion`, `Muss-Punkte`, `Gruppe`). LifeOS only reads the banks.
- Each answer is appended to `lernstand.jsonl` (`ts`, `card`, `result` = correct, partial or
  wrong, `mode`, `session`, `note`). Card states are computed from it: Leitner boxes 0–4,
  intervals 0 / 4 h / 1 d / 2 d / 4 d capped at half the time left before the exam; secure =
  box 2+ with a correct last answer. Missed cards return within the same session.
- Readiness (`GET /api/study/<subject>/progress`): secure share, new and due cards, weak
  cards, minutes still needed and per day until the exam.
- The MCP server (`LIFEOS_MCP_PATH_TOKEN`, see `docker/env.example`) offers the same as
  tools for ChatGPT and other assistants: `list_subjects`, `start_session`, `next_question`,
  `record_answer`, `end_session`, `progress`, `read_material`. Its instructions carry the
  tutor rules (one question at a time, varied question types, strict grading, short turns).
- `end_session` commits the log as `study: <Subject> – N answers, M correct`.

## Workspace lifecycle

1. **Create** — an agent writes exactly one Markdown file into `ready/workspaces/`.
2. **Review** — the user highlights passages and comments; each round is stored as
   `.md4h/feedback/ready/workspaces/<file>--<round>/feedback.md` and sealed together
   with the SHA-256 of the reviewed file.
3. **Clean up** — an agent applies every feedback item to its target (notes, tickets,
   knowledge), always writes `aenderungsprotokoll/<file>`, and moves the report to
   `archive/` once no question is left open.

Workspace files are never edited after creation, and `.md4h/` is never modified by agents.

### Feedback rounds in the app

The app writes rounds in the `md4h-feedback/v2` format of the
[Markdown for Humans](https://github.com/concretios/markdown-for-humans/blob/main/docs/FEEDBACK.md)
editor extension:

- A round starts as `state: draft` with the SHA-256 of the report. If the report changes,
  the draft becomes stale and can only be discarded.
- Comments target either whole blocks (the exact source lines are embedded, fidelity
  "Frozen source") or selected text (the rendered text, fidelity "Exact rendered text").
  IDs (`F1`, `F2`, …) are never reused; `next_id` keeps the high-water mark.
- **Finish & copy** seals the round (`state: sealed`, `sealed_at`) and copies the
  standard agent prompt. Sealed rounds are never changed or deleted by the app.
- Compatibility: agents process app rounds exactly like extension rounds. The
  extension's `md4h-target-v2` anchors depend on its internal editor model and are not
  written, so the extension cannot reopen an app round with highlights. Rounds started
  in the extension are shown read-only in the app.

## Conventions

- UTF-8, LF line endings (`.gitattributes` enforces it).
- Markdown must render in "Markdown for Humans": no `==highlight==`, no footnotes, no raw
  HTML outside code, no lists that mix checkboxes and plain items.
