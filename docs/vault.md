# Vault specification

```
vault/
├── inbox/                 raw ingest data per connector (gitignored, reproducible)
├── ready/                 the workbench ("AI ready") — the only place humans and agents edit
│   ├── notes/             notes as JSON Lines
│   ├── knowledge/         knowledge hub, an Obsidian-compatible Markdown vault
│   │   └── Presentations/ text mirrors of OneDrive PowerPoints (read-only)
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
