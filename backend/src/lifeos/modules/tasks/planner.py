"""The study planner (``agents/uni-planner.md``): keeps the University board up to date.

A worker job. Each run reads what is new since the last run – Moodle mirrors and Moodle
facts (due dates, submissions), Teams messages and Teams files – together with every ticket
of the board and the owner's comments on them, and lets the model decide what to create,
change, close or comment. Then it applies that with the GitHub client.

Rules (``boards/university.md``, decided by the owner):
- new tasks go to the column ``New``; the owner approves by moving them on;
- the planner may change anything on a university ticket, and explains every change in a
  comment; it never deletes, it closes;
- a comment of the owner on a ticket is a binding correction.

Start dates: the model sets a ``Start`` date per task from its kind and size (a short
submission a few days before, an exam weeks before), learning from how the owner corrected
earlier ones. The code then moves a Todo ticket to In Progress when its start is reached –
it becomes the owner's turn. If the owner moves it back to Todo, the code does not move it
forward again until the start date changes, and the next run sets a new one.

Large backlogs (the first run) are worked off over several runs: every run takes notes up
to a character budget; the rest stays pending. ``.lifeos/agents/uni-planner.json`` keeps
what was considered, for the control room.

Question banks (``… - Prüfen.md`` under ``knowledge/Studium/``) are written by hand in an
interactive session, never by this job. The planner only reminds: an exam ticket whose start
is reached or that is due within four weeks is shown to the model with the existing banks
and the Moodle mirror paths; without a matching bank it adds a hint to the ticket (no bank
yet, a session is due, known sources), once a bank exists it links it and removes the hint.
A ticket is shown again only when it enters that window or the set of banks changes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from lifeos.agents import comments
from lifeos.agents.comments import Texts
from lifeos.agents.config import AgentDef, load_config
from lifeos.agents.llm import Llm
from lifeos.agents.mirror import split_project
from lifeos.connectors.github.client import GitHubError, item_values, project_item
from lifeos.core.extract.text import front_matter
from lifeos.core.vault import KNOWLEDGE, STATE, Vault, write_atomic
from lifeos.modules.tasks.service import LOG_FILE, MARKER, UniTasks

log = logging.getLogger("lifeos.uni-planner")

AGENT = "uni-planner"
STATE_FILE = f"{STATE}/agents/uni-planner.json"
NEW = "New"
TODO = "Todo"
IN_PROGRESS = "In Progress"
DONE = "Done"
STATUS = "Status"
START = "Start"
EXPERIENCE_KEEP = 60  # remembered start-date outcomes the model learns from
# note type → input category shown in the control room
CATEGORIES = {
    "moodle-course": "moodle",
    "moodle-file": "moodle",
    "teams-messages": "teams-messages",
    "folder-file": "teams-files",
}
BUDGET = 120_000  # characters of notes per run
NOTE_CAP = 12_000  # characters per note; chats keep their newest part
KEEP_CLOSED_DAYS = 30
STUDIES = "Studium"  # under knowledge/: subjects with their question banks
BANK_SUFFIX = " - Prüfen.md"
BANK_WINDOW_DAYS = 28  # remind about a missing question bank this long before the exam
EXAM = re.compile(r"(?i)test|klausur|prüfung|exam|quiz")  # kind of ticket worth a bank


class NewTask(BaseModel):
    title: str = Field(description="'[<course short name>] <task>' in the course language")
    body: str = Field(description="Markdown: course, due, what to do, why, checklist, source")
    due: str | None = Field(default=None, description="YYYY-MM-DD local date, or null")
    start: str | None = Field(default=None, description="YYYY-MM-DD: when to start working")
    start_reason: str = Field(default="", description="why this start date, one sentence")
    course: str = ""
    source: str = Field(description="stable source key, e.g. moodle:assign:123 or note:<id>:<part>")


class Change(BaseModel):
    number: int
    reason: str = Field(description="why, with the source; becomes the ticket comment")
    title: str | None = None
    body: str | None = None
    due: str | None = Field(default=None, description="YYYY-MM-DD; 'none' clears it")
    start: str | None = Field(default=None, description="YYYY-MM-DD; 'none' clears it")
    start_reason: str | None = Field(default=None, description="why this start date")
    course: str | None = None
    status: str | None = Field(default=None, description="New | Todo | In Progress | Done")
    close: bool = False
    reopen: bool = False


class Remark(BaseModel):
    number: int
    text: str


class Plan(BaseModel):
    summary: str
    create: list[NewTask] = Field(default_factory=list)
    update: list[Change] = Field(default_factory=list)
    comment: list[Remark] = Field(default_factory=list)
    uncertain: list[str] = Field(default_factory=list)


SYSTEM = """You keep a student's University board on GitHub up to date. You get new or changed
course material (Moodle mirrors, Moodle facts with due dates and submission status, Teams
messages, Teams files) and every ticket of the board with the owner's comments.

A task is something the student must do or must not miss: submissions and homework (also in
groups), exams, tests and quizzes including registration deadlines, mandatory reading or
preparation, organisational items (registrations, group formation, attendance). Not a task:
general content, optional material, deadlines more than 14 days in the past, anything
already submitted, anything an existing ticket covers (same source key or clearly the same
task). Check relevance: mandatory, applies to this student (their group, their part), still
open? If unsure, do not create a ticket; list it under "uncertain".

You may change any ticket of the board: title, text, due date, course, column. Keep them
correct and current (moved deadlines, new details, submitted work → Done, cancelled → close).
Never delete. Give every change a reason with its source. Moodle facts are authoritative for
due dates and submission status.

Start dates: give every open ticket a start date (field "start"): the day the student should
begin working on it so it is done in time without stress. Decide from the kind and size of
the task, not from a fixed rule: a short submission or upload maybe 1-3 days before, a
presentation about a week, studying for an exam often 2-3 weeks. Learn from "experience"
(how the owner reacted to earlier start dates: moved back = too early, comments like "erst ab
…") and from the owner's comments; a date the owner names wins. Always give a one-sentence
start_reason. Tickets in "movedBack" were moved back to Todo by the owner: set a later start
(from their comment if there is one; otherwise estimate and ask in a comment). Tickets in
"needsStart" have none yet. When the start date is reached, the code moves the ticket to In
Progress itself – do not move tickets to In Progress for that reason.

Past and finished: move a ticket to Done when it is over – the deadline or exam date has
passed, Moodle reports it submitted, or messages say it is done or cancelled (then close it).
Say in the reason what showed it; if a submission is past due but not confirmed as
submitted, say so plainly. Tickets in "overdue" are past their due date and still open.

Question banks: tickets in "questionBank" are tests, exams or quizzes whose start is reached
or that are due within four weeks (ignore any that are not an exam, e.g. a registration).
Match each against "questionBanks" (subject, topic, exam date, path) – by course, topic and
exam date. If no bank covers the exam, add a short hint section to the ticket text (keep
everything else): "**Fragenbank:** noch keine – eine Sitzung dafür steht an." followed by a
list of the relevant source paths from "moodleFiles" (vault paths in backticks, only those
that fit the exam topic) to start that session with. Never write questions, drafts or files
yourself. If a bank exists, link it in the ticket text as [[<file name without .md>]] and
remove the hint. If you are unsure whether a bank belongs to the exam, say so in the hint.

The owner's comments on a ticket are binding corrections, with the reliability the owner
states. Follow them; question them only for very urgent reasons, and then only in a comment,
without overriding them.

New tickets: one per task, sub-steps as a checklist, written in the language of the course
material, no personal data that is not in the source. Body layout:
**Course:** … · **Due:** … (source: Moodle | slide 12, estimated) · [Open in Moodle](url)
**What to do:** … / **Why it matters:** … / checklist / **Source:** `<vault path>` — "<quote>"
{language}"""


@dataclass
class Ticket:
    number: int
    issue_id: str
    item_id: str | None
    title: str
    body: str
    state: str
    values: dict[str, Any]
    updated: str
    url: str
    markers: list[str] = field(default_factory=list)


def _cap(text: str, kind: str) -> str:
    if len(text) <= NOTE_CAP:
        return text
    if kind == "teams-messages":  # newest messages are at the end
        return "[… older messages left out …]\n" + text[-NOTE_CAP:]
    return text[:NOTE_CAP] + "\n[… cut …]"


class UniPlanner:
    def __init__(
        self,
        vault: Vault,
        gh_factory: Callable[[], Any] | None,
        repo: str | None,
        project_ref: str | None,
        timezone: str = "UTC",
        label: str = "university",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.vault = vault
        self.gh_factory = gh_factory
        self.repo = repo
        self.project_ref = project_ref
        self.label = label
        self.tz = ZoneInfo(timezone)
        self.clock = clock

    # -- state ----------------------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        try:
            return json.loads(self.vault.resolve(STATE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        path = self.vault.resolve(STATE_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, json.dumps(data, indent=1, ensure_ascii=False))

    def _tasks(self) -> UniTasks:
        return UniTasks(self.vault)

    # -- input ----------------------------------------------------------------------------

    def pending(self) -> tuple[list[tuple[str, str]], list[str], dict[str, Any], dict[str, int]]:
        """Notes still to read ``[(path, type)]``, removed notes, changed facts, totals per
        category."""
        tasks = self._tasks()
        notes = tasks.mirror_types(set(CATEGORIES))
        done = tasks._log()["notes"]
        todo = [(rel, kind) for rel, (rev, kind) in notes.items() if done.get(rel) != rev]
        removed = [rel for rel in done if rel not in notes]
        seen = tasks._log()["facts"]
        facts = {
            f["key"]: f
            for f in tasks.facts()
            if (seen.get(f["key"]) or {}) != {k: f.get(k) for k in ("due", "submitted", "title")}
        }
        totals: dict[str, int] = {}
        for _, kind in notes.values():
            totals[CATEGORIES[kind]] = totals.get(CATEGORIES[kind], 0) + 1
        return todo, removed, facts, totals

    def tickets(self, gh: Any, project: Any) -> list[Ticket]:
        """University tickets: open ones and those closed in the last 30 days."""
        assert self.repo
        cutoff = (self.clock() - timedelta(days=KEEP_CLOSED_DAYS)).isoformat()
        out = []
        for issue in gh.issues(self.repo):
            labels = [lbl["name"] for lbl in issue["labels"]["nodes"]]
            item = project_item(issue, project.id)
            if self.label not in labels and item is None:
                continue
            if issue["state"] != "OPEN" and issue["updatedAt"] < cutoff:
                continue
            out.append(Ticket(
                issue["number"], issue["id"], item["id"] if item else None, issue["title"],
                issue["body"] or "", issue["state"], item_values(item, project.fields),
                issue["updatedAt"], issue["url"], MARKER.findall(issue["body"] or ""),
            ))  # fmt: skip
        return out

    def owner_comments(self, gh: Any, ticket: Ticket) -> list[dict[str, str]]:
        return [
            {"at": c["createdAt"], "text": c["body"]}
            for c in gh.issue_comments(ticket.issue_id)
            if comments.agent_of(c["body"]) is None
        ]

    # -- one run --------------------------------------------------------------------------

    def run(self, llm_for: Callable[[AgentDef], Llm], force: bool = False) -> tuple[str, str]:
        """``(status, message)`` for the worker."""
        if self.gh_factory is None or not self.repo or not self.project_ref:
            return "skipped", "GitHub token, repository or LIFEOS_UNIVERSITY_PROJECT missing"
        agent = load_config(self.vault).agents.get(AGENT)
        if agent is None:
            return "skipped", f"no agents/{AGENT}.md in the vault"
        now = self.clock()
        today = now.astimezone(self.tz).date().isoformat()
        state = self.state()
        runs_today = (state.get("runs") or {}).get(today, 0)
        limit = int(agent.raw.get("daily_limit", 24))

        gh = self.gh_factory()
        owner, number = split_project(self.project_ref)
        project = gh.project(owner, number)
        tickets = self.tickets(gh, project)
        kept, moved, signals = self.housekeep(gh, project, tickets, state, today)
        if kept:  # our comments changed the tickets: do not count that as news
            tickets = self.tickets(gh, project)
            known = state.setdefault("updated", {})
            for t in tickets:
                if str(t.number) in kept:
                    known[str(t.number)] = t.updated
        done_note = f"; {'; '.join(moved)}" if moved else ""
        if runs_today >= limit:
            self._save(state)
            return "skipped", f"daily limit of {limit} runs reached{done_note}"

        banks = self.question_banks()
        signals["questionBank"] = self.bank_check(tickets, state, today, banks)

        todo, removed, facts, totals = self.pending()
        known = state.get("updated") or {}
        touched = [t for t in tickets if known.get(str(t.number)) != t.updated]
        if not (todo or removed or facts or touched or force or any(signals.values())):
            self._save(state)
            return ("ok" if moved else "skipped"), ("nothing new" + done_note)

        batch, used = [], 0
        for rel, kind in sorted(todo, key=lambda x: (x[1] != "moodle-course", x[0])):
            text = _cap(self.vault.resolve(rel).read_text(encoding="utf-8", errors="replace"), kind)
            if batch and used + len(text) > BUDGET:
                break
            batch.append((rel, kind, text))
            used += len(text)

        ticket_data = []
        for t in tickets:
            entry = {
                "number": t.number, "title": t.title, "state": t.state.lower(),
                "status": t.values.get(STATUS), "due": t.values.get("Due"),
                "start": t.values.get(START),
                "course": t.values.get("Course"), "sources": t.markers,
                "text": t.body[:2500],
            }  # fmt: skip
            if t in touched or t.state == "OPEN":
                notes = self.owner_comments(gh, t)
                if notes:
                    entry["ownerCorrections"] = notes
            ticket_data.append(entry)

        language = agent.raw.get("language")
        system = SYSTEM.format(language=f"Write comments in {language}." if language else "")
        prompt = json.dumps({
            "today": today,
            "columns": list(project.fields[STATUS].options),
            "tickets": ticket_data,
            "moodleFactsChanged": list(facts.values()),
            "removedNotes": removed,
            "notes": [{"path": rel, "type": kind, "text": text} for rel, kind, text in batch],
            "moreNotesPending": len(todo) - len(batch),
            "needsStart": signals["needsStart"],
            "movedBack": signals["movedBack"],
            "overdue": signals["overdue"],
            "questionBank": signals["questionBank"],
            "questionBanks": banks,
            "moodleFiles": self.moodle_files() if signals["questionBank"] else [],
            "experience": (state.get("experience") or [])[-EXPERIENCE_KEEP:],
            "rules": agent.section("Regeln") or "",
            "corrections": agent.section("Korrekturen") or "",
        }, ensure_ascii=False, indent=1)  # fmt: skip
        effort = agent.raw.get("effort") or "medium"
        if isinstance(effort, dict):
            effort = effort.get("plan", "medium")
        try:
            plan, usage = llm_for(agent).parse(system, prompt, Plan, str(effort))
        except Exception as exc:  # recorded by the worker; the next run tries again
            return "error", f"planning failed: {type(exc).__name__}: {exc}"

        events = moved + self.apply(gh, project, tickets, plan, state)
        for n in signals["questionBank"]:  # asked again when the window or the banks change
            state.setdefault("bankChecked", {})[str(n)] = _bank_key(banks)
        for key in ("needsStart", "movedBack", "overdue"):  # asked once per day per ticket
            asked = state.setdefault("asked", {}).setdefault(key, {})
            for n in signals[key]:
                asked[str(n)] = today
            if key == "movedBack":
                for n in signals[key]:
                    state.setdefault("movedBackOpen", {}).pop(str(n), None)
        self._record(state, today, now, batch, todo, facts, totals, plan, events, gh, project)
        failed = [e for e in events if e.startswith("failed")]
        message = (
            f"{len(plan.create)} new, {len(plan.update)} changed, {len(plan.comment)} comment(s);"
            f" read {len(batch)} note(s), {len(todo) - len(batch)} pending"
        )
        return ("error" if failed else "ok"), message + (f"; {'; '.join(failed)}" if failed else "")

    # -- question banks ---------------------------------------------------------------

    def question_banks(self) -> list[dict[str, Any]]:
        """Every hand-written question bank: path, subject, topic and exam date."""
        root = self.vault.resolve(f"{KNOWLEDGE}/{STUDIES}")
        out = []
        for path in sorted(root.rglob(f"*{BANK_SUFFIX}")) if root.is_dir() else []:
            meta = front_matter(path.read_text(encoding="utf-8", errors="replace"))
            subject = path.parent / "_Fach.md"
            course = (
                front_matter(subject.read_text(encoding="utf-8", errors="replace"))
                if subject.is_file() else {}
            )  # fmt: skip
            out.append({
                "path": path.relative_to(self.vault.root).as_posix(),
                "subject": str(meta.get("fach") or course.get("fach") or path.parent.name),
                "topic": str(meta.get("thema") or path.name[: -len(BANK_SUFFIX)]),
                "exam": str(meta.get("prüfung") or course.get("prüfung") or "") or None,
            })  # fmt: skip
        return out

    def moodle_files(self) -> list[str]:
        """Paths of the Moodle mirror notes (sources to start a question-bank session)."""
        types = {k for k, v in CATEGORIES.items() if v == "moodle"}
        return sorted(self._tasks().mirror_types(types))

    def bank_check(
        self, tickets: list[Ticket], state: dict[str, Any], today: str,
        banks: list[dict[str, Any]],
    ) -> list[int]:  # fmt: skip
        """Open exam tickets in the reminder window not yet checked against these banks."""
        checked: dict[str, str] = state.setdefault("bankChecked", {})
        limit = (date.fromisoformat(today) + timedelta(days=BANK_WINDOW_DAYS)).isoformat()
        out = []
        for t in tickets:
            n = str(t.number)
            status, start, due = t.values.get(STATUS), t.values.get(START), t.values.get("Due")
            if t.state != "OPEN" or status == DONE or not EXAM.search(t.title):
                checked.pop(n, None)
                continue
            due_soon = bool(due) and today <= str(due) <= limit
            started = bool(start) and str(start) <= today and (not due or str(due) >= today)
            if not (due_soon or started):
                checked.pop(n, None)  # outside the window: check again when it enters
                continue
            if checked.get(n) != _bank_key(banks):
                out.append(t.number)
        return out

    # -- start dates (code only) --------------------------------------------------------

    def housekeep(
        self, gh: Any, project: Any, tickets: list[Ticket], state: dict[str, Any], today: str
    ) -> tuple[set[str], list[str], dict[str, list[int]]]:
        """Move Todo tickets whose start is reached to In Progress, notice tickets the owner
        moved back, remember outcomes. Returns the tickets we commented on, what was done
        and the signals for the model (``needsStart``, ``movedBack``, ``overdue``)."""
        signals: dict[str, list[int]] = {"needsStart": [], "movedBack": [], "overdue": []}
        kept: set[str] = set()
        moved: list[str] = []
        if START not in project.fields:
            return kept, moved, signals
        t = Texts.load(self.vault)
        promoted: dict[str, str] = state.setdefault("promoted", {})  # number -> start
        held: dict[str, str | None] = state.setdefault("held", {})  # number -> start then
        reasons: dict[str, str] = state.setdefault("startReasons", {})
        finished: dict[str, str] = state.setdefault("finished", {})
        moved_back: dict[str, str] = state.setdefault("movedBackOpen", {})
        asked: dict[str, dict[str, str]] = state.setdefault("asked", {})
        experience: list[dict[str, Any]] = state.setdefault("experience", [])
        for tk in tickets:
            n = str(tk.number)
            status, start, due = tk.values.get(STATUS), tk.values.get(START), tk.values.get("Due")
            if tk.state != "OPEN" or status == DONE:
                if n not in finished:
                    finished[n] = today
                    experience.append({"ticket": tk.title, "start": start, "due": due,
                                       "event": "done", "date": today})  # fmt: skip
                promoted.pop(n, None)
                held.pop(n, None)
                continue
            finished.pop(n, None)
            if n in promoted and status == TODO:  # the owner moved it back
                promoted.pop(n)
                held[n] = start
                moved_back[n] = today
                experience.append({"ticket": tk.title, "start": start, "due": due,
                                   "event": "owner moved back to Todo", "date": today})  # fmt: skip
                self._comment(gh, tk.issue_id, t("uni_held"))
                kept.add(n)
                moved.append(f"#{tk.number} moved back by the owner")
                continue
            if status == TODO and start and start <= today and (n not in held or held[n] != start):
                if tk.item_id:
                    self._set(gh, project, tk.item_id, STATUS, IN_PROGRESS)
                    reason = reasons.get(n, "")
                    self._comment(gh, tk.issue_id, t("uni_started", start=start, reason=reason))
                    promoted[n] = start
                    held.pop(n, None)
                    kept.add(n)
                    moved.append(f"#{tk.number} → In Progress (start {start})")
                continue
            if (
                status in (NEW, TODO)
                and not start
                and (asked.get("needsStart") or {}).get(n) != today
            ):
                signals["needsStart"].append(tk.number)
            if due and due < today and (asked.get("overdue") or {}).get(n) != today:
                signals["overdue"].append(tk.number)
        signals["movedBack"] = sorted(int(n) for n in moved_back)
        del experience[:-EXPERIENCE_KEEP]
        return kept, moved, signals

    # -- apply ----------------------------------------------------------------------------

    def _comment(self, gh: Any, issue_id: str, text: str) -> None:
        gh.add_comment(issue_id, f"{comments.marker(AGENT)}\n🤖 **Study planner:** {text}")

    def _set(self, gh: Any, project: Any, item_id: str, name: str, value: str | None) -> None:
        f = project.fields.get(name)
        if f is not None:
            gh.set_field(project.id, item_id, f, value)

    def apply(
        self, gh: Any, project: Any, tickets: list[Ticket], plan: Plan,
        state: dict[str, Any] | None = None,
    ) -> list[str]:  # fmt: skip
        events: list[str] = []
        reasons = (state if state is not None else {}).setdefault("startReasons", {})
        by_number = {t.number: t for t in tickets}
        used = {m for t in tickets for m in t.markers}
        columns = list(project.fields[STATUS].options)
        meta = gh.repo(self.repo)
        for task in plan.create:
            if task.source in used:
                events.append(f"skipped duplicate {task.source}")
                continue
            try:
                body = f"{task.body.strip()}\n\n<!-- lifeos:task {task.source} -->"
                changes: dict[str, Any] = {"title": task.title[:250], "body": body}
                if self.label in meta.labels:
                    changes["labelIds"] = [meta.labels[self.label]]
                issue = gh.create_issue(meta.id, changes)
                item = gh.add_to_project(project.id, issue["id"])
                self._set(gh, project, item, STATUS, NEW if NEW in columns else None)
                if task.due and _is_date(task.due):
                    self._set(gh, project, item, "Due", task.due)
                if task.start and _is_date(task.start):
                    self._set(gh, project, item, START, task.start)
                    reasons[str(issue["number"])] = task.start_reason
                if task.course:
                    self._set(gh, project, item, "Course", task.course)
                used.add(task.source)
                events.append(f"created #{issue['number']}")
            except GitHubError as exc:
                events.append(f"failed to create '{task.title}': {exc}")
        for change in plan.update:
            t = by_number.get(change.number)
            if t is None:  # only tickets of this board
                events.append(f"skipped unknown #{change.number}")
                continue
            try:
                events += self._change(gh, project, t, change, columns)
                if change.start and change.start_reason:
                    reasons[str(t.number)] = change.start_reason
            except GitHubError as exc:
                events.append(f"failed to change #{t.number}: {exc}")
        for remark in plan.comment:
            t = by_number.get(remark.number)
            if t is not None:
                try:
                    self._comment(gh, t.issue_id, remark.text)
                    events.append(f"commented #{t.number}")
                except GitHubError as exc:
                    events.append(f"failed to comment #{t.number}: {exc}")
        return events

    def _change(self, gh: Any, project: Any, t: Ticket, c: Change, columns: list[str]) -> list[str]:
        done: list[str] = []
        issue: dict[str, Any] = {}
        if c.title and c.title != t.title:
            issue["title"] = c.title[:250]
            done.append("title")
        if c.body is not None and c.body.strip() and c.body.strip() != t.body.strip():
            markers = "".join(f"\n\n<!-- lifeos:task {m} -->" for m in t.markers
                              if f"lifeos:task {m}" not in c.body)  # fmt: skip
            issue["body"] = c.body.strip() + markers  # the source markers always stay
            done.append("text")
        if c.close and t.state == "OPEN":
            issue["state"] = "CLOSED"
            done.append("closed")
        elif c.reopen and t.state != "OPEN":
            issue["state"] = "OPEN"
            done.append("reopened")
        if issue:
            gh.update_issue(t.issue_id, issue)
        item = t.item_id or gh.add_to_project(project.id, t.issue_id)
        if c.due is not None and c.due != t.values.get("Due"):
            if c.due == "none":
                self._set(gh, project, item, "Due", None)
                done.append("due cleared")
            elif _is_date(c.due):
                self._set(gh, project, item, "Due", c.due)
                done.append(f"due {c.due}")
        if c.start is not None and c.start != t.values.get(START) and START in project.fields:
            if c.start == "none":
                self._set(gh, project, item, START, None)
                done.append("start cleared")
            elif _is_date(c.start):
                self._set(gh, project, item, START, c.start)
                done.append(f"start {c.start}")
        if c.course is not None and c.course != t.values.get("Course"):
            self._set(gh, project, item, "Course", c.course)
            done.append("course")
        if c.status and c.status in columns and c.status != t.values.get(STATUS):
            self._set(gh, project, item, STATUS, c.status)
            done.append(f"→ {c.status}")
        if not done:
            return []
        why = c.reason
        if c.start_reason and c.start_reason not in why:
            why = f"{why} {c.start_reason}".strip()
        self._comment(gh, t.issue_id, f"{', '.join(done)}. {why}")
        return [f"changed #{t.number} ({', '.join(done)})"]

    # -- record ---------------------------------------------------------------------------

    def _record(
        self, state: dict, today: str, now: datetime, batch: list, todo: list, facts: dict,
        totals: dict[str, int], plan: Plan, events: list[str], gh: Any, project: Any,
    ) -> None:  # fmt: skip
        tasks = self._tasks()
        log_data = tasks._log()
        notes = tasks.mirror_types(set(CATEGORIES))
        for rel, _kind, _text in batch:
            if rel in notes:
                log_data["notes"][rel] = notes[rel][0]
        log_data["notes"] = {rel: rev for rel, rev in log_data["notes"].items() if rel in notes}
        for f in tasks.facts():
            log_data["facts"][f["key"]] = {k: f.get(k) for k in ("due", "submitted", "title")}
        log_data["last_run"] = now.isoformat(timespec="seconds")
        write_atomic(self.vault.resolve(LOG_FILE),
                     json.dumps(log_data, indent=2, ensure_ascii=False))  # fmt: skip

        read = {rel for rel, _kind, _text in batch}
        considered: dict[str, int] = {}
        for _rel, kind, _text in batch:
            considered[CATEGORIES[kind]] = considered.get(CATEGORIES[kind], 0) + 1
        pending: dict[str, int] = {}
        for rel, kind in todo:
            if rel not in read:
                pending[CATEGORIES[kind]] = pending.get(CATEGORIES[kind], 0) + 1
        # remember the tickets as they are now, so our own changes do not trigger a run
        updated = {str(t.number): t.updated for t in self.tickets(gh, project)}
        runs = {d: n for d, n in (state.get("runs") or {}).items() if d >= _days_ago(today, 7)}
        runs[today] = runs.get(today, 0) + 1
        self._save({
            "lastRun": now.isoformat(timespec="seconds"),
            "runs": runs,
            "updated": updated,
            "considered": considered,
            "factsChanged": len(facts),
            "pending": pending,
            "totals": totals,
            "summary": plan.summary,
            "uncertain": plan.uncertain,
            "events": events,
            **{k: state[k] for k in ("promoted", "held", "startReasons", "finished",
                                     "movedBackOpen", "asked", "experience", "bankChecked")
               if k in state},
        })  # fmt: skip


def _is_date(value: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))


def _days_ago(today: str, days: int) -> str:
    return (date.fromisoformat(today) - timedelta(days=days)).isoformat()


def _bank_key(banks: list[dict[str, Any]]) -> str:
    """Fingerprint of the set of question banks: a new or removed bank means check again."""
    paths = "\n".join(sorted(b["path"] for b in banks))
    return hashlib.sha256(paths.encode()).hexdigest()[:16]
