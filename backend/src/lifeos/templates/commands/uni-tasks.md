---
description: Find university tasks in the Moodle mirrors and draft tickets for review
---

# University tasks

You turn course material into task tickets. You only **draft**: the user reviews them and
creates the issues in LifeOS (Tickets → Changes → create plan → Apply). Never commit, push,
call GitHub or delete files.

Extra instructions from the user (may be empty): $ARGUMENTS

## 1. Get the input

Run `docker compose -f ../app/compose.yml exec -T web lifeos tasks input` (from the vault
folder). The JSON contains:

- `board`: `repo`, `draft_dir`, `label`, `status_options`, `label_exists`, `has_due_field`
- `notes.changed` / `notes.removed`: mirror notes that are new or changed since the last run.
  Only these need reading (on `first_run` that is everything).
- `facts`: Moodle assignments and quizzes with `due`, `submitted`, `url`, `description`,
  their `status` (new / changed / unchanged) and linked `tickets`. Facts are authoritative
  for due dates and submission status.
- `untracked_facts`: open facts without a ticket yet.
- `tickets`: existing tickets with the label or a task marker (`markers`, `status`, `due`).
- `hints`: deterministic updates to apply (see step 5).

If `label_exists` or `has_due_field` is false, tell the user to run a ticket sync in LifeOS
before creating the plan, and continue.

## 2. Read

- Every note in `notes.changed`: course overviews (`type: moodle-course`, with activity dates
  and a Deadlines section) and file mirrors (`type: moodle-file`: slides, scripts, pages).
- The facts, especially `untracked_facts` and those with status new or changed.
- The existing tickets, to avoid duplicates. Open a ticket file when unsure.

## 3. Decide what is a task

A task is something the student must do or must not miss:

- submissions and homework (individual or group): documents, presentations, videos, reports
- exams, tests and quizzes, including registration deadlines
- mandatory reading or preparation for a session
- organisational items: registrations, group formation, attendance requirements, deadlines

Not a task: general content, optional material, deadlines more than 14 days in the past,
items already submitted, anything already covered by an existing ticket (same marker or
clearly the same task).

Check relevance against the context: is it mandatory, does it apply to this student (their
group, their part of the course), is it still open? If unsure, do **not** create a ticket;
list it in the report under "Uncertain". One ticket per task; sub-steps become a checklist.

## 4. Write drafts

For each new task create `<draft_dir>/new-<slug>-<4 random hex digits>.md`:

````markdown
---
title: '[<course short name>] <task>'
state: open
labels: [university]
assignees: []
project:
  Status: Todo
  Due: 'YYYY-MM-DD'
repo: <board.repo>
---

**Course:** <course> · **Due:** <date and time> (source: Moodle | slide 12 of …, estimated) · [Open in Moodle](<url>)

**What to do:** …

**Why it matters:** …

- [ ] first step
- [ ] next step

**Source:** `<vault path of the note>` — "<short quote>"

<!-- lifeos:task <source key> -->
````

- Write title and text in the language of the course material.
- Source key: the fact key for Moodle activities (`moodle:assign:123`); for tasks found in
  notes `note:<moodle_id of the note>:<short stable id such as slide-12>`. Exactly one
  marker per ticket, never reuse a marker of another ticket.
- `Due` is the local date of the deadline; leave it out if there is no date. Prefer the
  date from the facts; a date read from slides is marked "estimated" in the text.
- Use the label from `board.label` and a `Status` from `board.status_options`.
- Do not add personal data that is not in the source.

## 5. Apply hints

- `due_changed`: set `project.Due` of that ticket file to `to`. Change nothing else.
- `submitted`: set `project.Status` to `Done` in that ticket file. Change nothing else.

Do not touch any other existing ticket.

## 6. Check and record

Run `docker compose -f ../app/compose.yml exec -T web lifeos tasks check`. Fix every error
and run it again until `ok` is true. Then run
`docker compose -f ../app/compose.yml exec -T web lifeos tasks done`.

## 7. Report

Write `ready/workspaces/uni-tasks-<YYYY-MM-DD>.md` in plain Markdown (no HTML, no
footnotes, no angle brackets outside code):

- Summary with counts
- New drafts: title, due date, source, one-line reason
- Updated tickets (due date, status)
- Discarded: item and reason
- Uncertain, please decide: item, source, open question
- Processed: number of notes and facts

End with a short summary in the chat and remind the user: review in LifeOS → Tickets →
Changes → create plan → Apply.
