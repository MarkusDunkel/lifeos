"""One-way mirror: Moodle course materials → Markdown notes in the knowledge hub.

Moodle stays the source of truth; the vault holds only the text of the files plus an
overview note per course. ``.lifeos/moodle/state.json`` remembers what was taken over
(per file: ``timemodified`` and size), so a sync only downloads new or changed files.
The access token lives in ``LIFEOS_STATE_DIR`` (outside the vault).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from lifeos.connectors.moodle.client import (
    MoodleApi,
    MoodleClient,
    MoodleError,
    normalise_url,
    request_token,
)
from lifeos.core.extract import ExtractError, html_to_markdown, supported, to_markdown
from lifeos.core.extract.text import dump_front, front_matter, link, mark_deleted
from lifeos.core.locks import FileLock, LockBusy
from lifeos.core.vault import INBOX, KNOWLEDGE, STATE, Vault, VaultPathError, write_atomic

STATE_FILE = f"{STATE}/moodle/state.json"
LOCK_FILE = f"{STATE}/moodle/sync.lock"  # shared by the web app and the worker
TASKS_FILE = f"{INBOX}/moodle/tasks.json"
# Activity dates from core_course_get_contents (dates[].dataid) and their names in the overview.
DATE_LABELS = {
    "allowsubmissionsfromdate": "opens",
    "timeopen": "opens",
    "duedate": "Due",
    "timeclose": "Closes",
    "cutoffdate": "cut-off",
}
DEADLINE_DATES = ("duedate", "timeclose")
TOKEN_FILE = "moodle-token.json"
MAX_BYTES = 50 * 1024 * 1024
FILE_MODULES = {"resource", "folder"}
SKIP_MODULES = {"label"}  # rendered inline in the overview


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def iso(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_name(name: str, fallback: str = "untitled") -> str:
    """A file/folder name that works on Windows, in Obsidian and in wikilinks."""
    name = re.sub(r'[\\/:*?"<>|#^\[\]\x00-\x1f]', "-", name)
    name = re.sub(r"\s+", " ", name).strip(" .-")
    return name[:80].rstrip(" .-") or fallback


# -- access ------------------------------------------------------------------------


class MoodleAccess:
    """Token storage and connection (username/password are used once, never stored)."""

    def __init__(
        self,
        base_url: str,
        state_dir: Path,
        client_factory: Callable[[str, str], MoodleApi] = MoodleClient,
        token_factory: Callable[[str, str, str], str] | None = None,
    ):
        self.base_url = normalise_url(base_url)
        self.path = state_dir / TOKEN_FILE
        self._client_factory = client_factory
        self._token_factory = token_factory

    def _load(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        return json.loads(self.path.read_text(encoding="utf-8"))

    @property
    def connected(self) -> bool:
        data = self._load()
        return bool(data and data.get("url") == self.base_url)

    def info(self) -> dict[str, Any]:
        data = self._load() if self.connected else None
        return {k: v for k, v in (data or {}).items() if k != "token"}

    def connect(
        self, username: str | None = None, password: str | None = None, token: str | None = None
    ) -> dict[str, Any]:
        if not token:
            if not username or not password:
                raise MoodleError("enter username and password, or a token", 400)
            factory = self._token_factory or request_token
            token = factory(self.base_url, username, password)
        site = self._client_factory(self.base_url, token).site_info()
        data = {
            "url": self.base_url,
            "token": token,
            "site": site.get("sitename"),
            "user": site.get("fullname"),
            "userid": site.get("userid"),
        }
        write_atomic(self.path, json.dumps(data, indent=2))
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)
        return self.info()

    def disconnect(self) -> None:
        self.path.unlink(missing_ok=True)

    def client(self) -> tuple[MoodleApi, int]:
        data = self._load()
        if not data or data.get("url") != self.base_url:
            raise MoodleError("not connected to Moodle", 400)
        return self._client_factory(self.base_url, data["token"]), int(data["userid"])


# -- sync --------------------------------------------------------------------------


@dataclass
class SyncReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    renamed: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: int = 0
    skipped: list[str] = field(default_factory=list)  # not mirrored (type, size); new ones only
    errors: list[str] = field(default_factory=list)
    courses: int = 0
    tasks: int = 0  # assignments and quizzes in the task facts
    synced_at: str = ""


@dataclass
class Item:
    """One mirrored file of a course."""

    key: str
    title: str
    filename: str
    fileurl: str
    url: str  # the module page in Moodle (opens with the normal login)
    section: str
    rel: str  # note path relative to the target folder
    timemodified: int
    filesize: int


class MoodleSync:
    def __init__(
        self,
        vault: Vault,
        access: MoodleAccess | None,
        target_dir: str,
        clock: Callable[[], float] = time.time,
        timezone: str = "UTC",
    ):
        self.tz = ZoneInfo(timezone)
        self.vault = vault
        self.access = access
        self.target_dir = target_dir.strip().strip("/")
        self.zone = f"{KNOWLEDGE}/{self.target_dir}"
        vault.resolve(self.target_dir, zone=KNOWLEDGE)  # validates the setting early
        self._clock = clock
        self._lock = threading.Lock()

    # -- state -------------------------------------------------------------------

    def _state_path(self) -> Path:
        return self.vault.resolve(STATE_FILE)

    def load_state(self) -> dict[str, Any]:
        path = self._state_path()
        state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        state.setdefault("files", {})
        state.setdefault("courses", {})
        state.setdefault("selected", None)
        state.setdefault("last", None)
        return state

    def _save_state(self, state: dict[str, Any]) -> None:
        write_atomic(self._state_path(), json.dumps(state, indent=2, ensure_ascii=False))

    def _access(self) -> MoodleAccess:
        if self.access is None:
            raise MoodleError("Moodle is not set up (see the Moodle page)", 400)
        return self.access

    # -- courses -----------------------------------------------------------------

    def _in_progress(self, course: dict[str, Any]) -> bool:
        now = self._clock()
        if course.get("completed"):
            return False
        end = course.get("enddate") or 0
        start = course.get("startdate") or 0
        return (end == 0 or end > now) and start <= now + 14 * 86400

    def courses(self) -> list[dict[str, Any]]:
        api, userid = self._access().client()
        state = self.load_state()
        selected = state["selected"]
        out = []
        for c in api.courses(userid):
            in_progress = self._in_progress(c)
            out.append(
                {
                    "id": c["id"],
                    "name": c.get("fullname") or c.get("shortname") or str(c["id"]),
                    "shortname": c.get("shortname") or "",
                    "url": f"{self._access().base_url}/course/view.php?id={c['id']}",
                    "in_progress": in_progress,
                    "selected": c["id"] in selected if selected is not None else in_progress,
                }
            )
        return sorted(out, key=lambda c: (not c["in_progress"], c["name"].casefold()))

    def select(self, ids: list[int]) -> None:
        state = self.load_state()
        state["selected"] = sorted(set(ids))
        self._save_state(state)

    # -- paths -------------------------------------------------------------------

    def _file(self, rel: str) -> Path:
        return self.vault.resolve(rel, zone=self.zone)

    def knowledge_path(self, rel: str) -> str:
        return f"{self.target_dir}/{rel}"

    @staticmethod
    def _owned(path: Path, kind: str, key: str) -> bool:
        """A file may be overwritten only if it is our mirror of ``key``."""
        if not path.exists():
            return True
        meta = front_matter(path.read_text(encoding="utf-8"))
        return meta.get("type") == kind and str(meta.get("moodle_id")) == key

    # -- sync --------------------------------------------------------------------

    def sync(self) -> SyncReport:
        access = self._access()
        if not self._lock.acquire(blocking=False):
            raise MoodleError("a sync is already running", 409)
        try:
            with FileLock(self.vault.resolve(LOCK_FILE), blocking=False):
                api, userid = access.client()
                return self._sync(api, userid, access.base_url)
        except LockBusy as exc:
            raise MoodleError("a sync is already running", 409) from exc
        finally:
            self._lock.release()

    def _sync(self, api: MoodleApi, userid: int, base_url: str) -> SyncReport:
        report = SyncReport()
        state = self.load_state()
        selected = state["selected"]
        courses = [
            c
            for c in api.courses(userid)
            if (c["id"] in selected if selected is not None else self._in_progress(c))
        ]
        facts = self._facts(api, courses, base_url, report)
        used_dirs: set[str] = set()
        for course in sorted(courses, key=lambda c: c["id"]):
            try:
                deadlines = [f for f in facts if f["course_id"] == course["id"]]
                self._course(api, course, base_url, state, report, used_dirs, deadlines)
                report.courses += 1
            except (MoodleError, VaultPathError) as exc:
                name = course.get("fullname") or course["id"]
                report.errors.append(f"{name}: {exc}")
        report.synced_at = now_iso()
        state["last"] = asdict(report)
        self._save_state(state)
        return report

    def _course(
        self,
        api: MoodleApi,
        course: dict[str, Any],
        base_url: str,
        state: dict[str, Any],
        report: SyncReport,
        used_dirs: set[str],
        deadlines: list[dict[str, Any]],
    ) -> None:
        cid = str(course["id"])
        name = course.get("fullname") or course.get("shortname") or cid
        course_dir = safe_name(name, f"Course {cid}")
        if course_dir.casefold() in used_dirs:
            course_dir = f"{course_dir} ({cid})"
        used_dirs.add(course_dir.casefold())
        course_url = f"{base_url}/course/view.php?id={cid}"
        sections = api.contents(course["id"])

        items: list[Item] = []
        lines: list[str] = self._deadline_lines(deadlines)  # overview body
        used: set[str] = set()
        for index, section in enumerate(sections):
            title = (section.get("name") or "").strip() or f"Section {index}"
            sec_dir = f"{index:02d} {safe_name(title, f'Section {index}')}"
            lines += ["", f"## {title}"]
            summary = html_to_markdown(section.get("summary") or "").strip()
            if summary:
                lines += ["", summary]
            entries: list[str] = []
            for module in section.get("modules", []):
                entries += self._module(module, course_dir, sec_dir, title, items, used)
            if entries:
                lines += [""] + entries

        files = state["files"]
        seen: set[str] = set()
        for item in items:
            seen.add(item.key)
            try:
                self._item(api, item, name, cid, files, report)
            except (ExtractError, MoodleError, VaultPathError) as exc:
                report.errors.append(f"{name} / {item.filename}: {exc}")

        for key, entry in files.items():
            if entry.get("course") != cid or key in seen or entry.get("deleted"):
                continue
            if entry.get("note"):
                path = self._file(entry["note"])
                if path.is_file() and self._owned(path, "moodle-file", key):
                    write_atomic(path, mark_deleted(path.read_text(encoding="utf-8")))
                report.deleted.append(self.knowledge_path(entry["note"]))
            entry["deleted"] = True

        overview = self._overview(course, name, course_url, lines)
        self._write_overview(cid, f"{course_dir}/{course_dir}.md", overview, state)

    def _module(
        self,
        module: dict[str, Any],
        course_dir: str,
        sec_dir: str,
        section: str,
        items: list[Item],
        used: set[str],
    ) -> list[str]:
        """Overview lines for a module; mirrored files are added to ``items``."""
        modname = module.get("modname", "")
        title = (module.get("name") or "").strip() or modname
        url = module.get("url") or ""
        if modname in SKIP_MODULES:
            text = html_to_markdown(module.get("description") or "").strip()
            return (
                [f"- {line}" if i == 0 else f"  {line}" for i, line in enumerate(text.split("\n"))]
                if text
                else []
            )
        files = [c for c in module.get("contents") or [] if c.get("type") == "file"]
        if modname == "resource":
            files = files[:1]  # further files of a resource are embedded assets
        elif modname == "page":
            files = [c for c in files if c.get("filename") == "index.html"][:1]
        elif modname != "folder":
            line = f"- [{_label(title)}]({link(url)}) · {modname}" if url else f"- {title}"
            return [line + self._dates(module)]

        lines = []
        for content in files:
            filename = content["filename"]
            many = modname == "folder"
            stem = PurePosixPath(filename).stem if many else title
            base = f"{course_dir}/{sec_dir}" + (f"/{safe_name(title)}" if many else "")
            note = f"{base}/{safe_name(stem)}"
            if note.casefold() in used:
                note = f"{note} ({module['id']})"
            used.add(note.casefold())
            if modname == "page":
                filename = f"{safe_name(title)}.html"
            display = stem if many else title
            reason = _not_mirrored(filename, content.get("filesize", 0))
            if reason:
                lines.append(f"- [{_label(display)}]({link(url)}) · {filename} ({reason})")
                items.append(
                    Item(
                        key=f"{module['id']}:{content.get('filepath') or '/'}{content['filename']}",
                        title=display,
                        filename=filename,
                        fileurl="",
                        url=url,
                        section=section,
                        rel="",
                        timemodified=content.get("timemodified") or 0,
                        filesize=content.get("filesize") or 0,
                    )
                )
                continue
            rel = f"{note}.md"
            items.append(
                Item(
                    key=f"{module['id']}:{content.get('filepath') or '/'}{content['filename']}",
                    title=display,
                    filename=filename,
                    fileurl=content.get("fileurl") or "",
                    url=url,
                    section=section,
                    rel=rel,
                    timemodified=content.get("timemodified") or 0,
                    filesize=content.get("filesize") or 0,
                )
            )
            wikilink = self.knowledge_path(rel)[: -len(".md")]
            lines.append(f"- [[{wikilink}|{_label(display)}]] · [Moodle]({link(url)})")
        return lines

    def _item(
        self,
        api: MoodleApi,
        item: Item,
        course: str,
        cid: str,
        files: dict[str, dict[str, Any]],
        report: SyncReport,
    ) -> None:
        entry = files.get(item.key)
        if not item.rel:  # not mirrored: remember, report once
            if entry is None or entry.get("deleted"):
                reason = _not_mirrored(item.filename, item.filesize)
                report.skipped.append(f"{course} / {item.filename}: {reason}")
            files[item.key] = {"course": cid, "note": None, "skipped": True}
            return

        target = self._file(item.rel)
        if not self._owned(target, "moodle-file", item.key):
            raise VaultPathError(
                f"a different note already exists at {self.knowledge_path(item.rel)}"
            )

        moved = bool(entry and entry.get("note") and entry["note"] != item.rel)
        if moved:
            old = self._file(entry["note"])
            if old.is_file() and self._owned(old, "moodle-file", item.key) and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                old.replace(target)
            report.renamed.append(
                f"{self.knowledge_path(entry['note'])} → {self.knowledge_path(item.rel)}"
            )

        unchanged = (
            entry is not None
            and entry.get("timemodified") == item.timemodified
            and entry.get("filesize") == item.filesize
            and not entry.get("deleted")
            and not moved
            and target.is_file()
        )
        if unchanged:
            report.unchanged += 1
            return

        extracted = to_markdown(item.filename, api.download(item.fileurl))
        content = self._render(item, course, extracted)
        existed = target.is_file()
        if not existed or target.read_text(encoding="utf-8") != content:
            write_atomic(target, content)
            if not moved:
                (report.updated if existed else report.created).append(
                    self.knowledge_path(item.rel)
                )
        elif not moved:
            report.unchanged += 1
        files[item.key] = {
            "course": cid,
            "note": item.rel,
            "timemodified": item.timemodified,
            "filesize": item.filesize,
            "deleted": False,
        }

    @staticmethod
    def _render(item: Item, course: str, extracted) -> str:
        front: dict[str, Any] = {
            "type": "moodle-file",
            "source": "moodle",
            "title": item.title,
            "course": course,
            "section": item.section,
            "url": item.url,
            "file": item.filename,
            "moodle_id": item.key,
            "modified": iso(item.timemodified),
        }
        if extracted.pages is not None:
            front["pages"] = extracted.pages
        front["mirror"] = True
        notice = (
            f"> Mirror of [{_label(item.title)}]({link(item.url)}) in Moodle ({course}),"
            " generated by LifeOS. Changes here are overwritten on the next sync."
        )
        body = extracted.body
        if extracted.empty:
            body = "> No text found in this file (a scanned document?). Open it in Moodle.\n"
        return f"{dump_front(front)}\n# {item.title}\n\n{notice}\n\n{body}".rstrip() + "\n"

    @staticmethod
    def _overview(course: dict[str, Any], name: str, url: str, lines: list[str]) -> str:
        front = {
            "type": "moodle-course",
            "source": "moodle",
            "title": name,
            "shortname": course.get("shortname") or "",
            "url": url,
            "moodle_id": str(course["id"]),
            "mirror": True,
        }
        notice = (
            f"> Overview of [{_label(name)}]({link(url)}) in Moodle, generated by LifeOS."
            " Changes here are overwritten on the next sync."
        )
        body = "\n".join(lines).strip()
        return f"{dump_front(front)}\n# {name}\n\n{notice}\n\n{body}".rstrip() + "\n"

    def _write_overview(self, cid: str, rel: str, content: str, state: dict[str, Any]) -> None:
        course_state = state["courses"].setdefault(cid, {})
        old_rel = course_state.get("overview")
        target = self._file(rel)
        if not self._owned(target, "moodle-course", cid):
            raise VaultPathError(f"a different note already exists at {self.knowledge_path(rel)}")
        if old_rel and old_rel != rel:
            old = self._file(old_rel)
            if old.is_file() and self._owned(old, "moodle-course", cid):
                old.unlink()
        if not target.is_file() or target.read_text(encoding="utf-8") != content:
            write_atomic(target, content)
        course_state["overview"] = rel

    # -- dates and task facts ------------------------------------------------------

    def _when(self, ts: int) -> str:
        return datetime.fromtimestamp(ts, self.tz).strftime("%d.%m.%Y %H:%M")

    def _iso(self, ts: int | None) -> str | None:
        if not ts:
            return None
        return datetime.fromtimestamp(ts, self.tz).isoformat(timespec="minutes")

    def _dates(self, module: dict[str, Any]) -> str:
        """`` · **Due: 03.10.2026 23:59** (cut-off …, opens …)`` from the activity dates."""
        main, extra = [], []
        for d in module.get("dates") or []:
            ts, dataid = d.get("timestamp"), d.get("dataid", "")
            if not ts:
                continue
            label = DATE_LABELS.get(dataid) or str(d.get("label", "")).rstrip(": ") or dataid
            if dataid in DEADLINE_DATES:
                main.append(f"**{label}: {self._when(ts)}**")
            else:
                extra.append(f"{label} {self._when(ts)}")
        text = " · ".join(main)
        if extra:
            text += f" ({', '.join(extra)})" if text else ", ".join(extra)
        return f" · {text}" if text else ""

    def _facts(
        self, api: MoodleApi, courses: list[dict[str, Any]], base_url: str, report: SyncReport
    ) -> list[dict[str, Any]]:
        """Assignments and quizzes of the synced courses → ``inbox/moodle/tasks.json``."""
        if not courses:
            return []
        names = {c["id"]: c for c in courses}
        ids = sorted(names)
        try:
            assignments = api.assignments(ids)
        except MoodleError as exc:
            report.errors.append(f"assignments: {exc}")
            assignments = []
        try:
            quizzes = api.quizzes(ids)
        except MoodleError as exc:
            report.errors.append(f"quizzes: {exc}")
            quizzes = []

        def fact(kind, item, due, opens, cutoff, done) -> dict[str, Any]:
            course = names.get(item["course"], {})
            try:
                submitted = done(item["id"])
            except MoodleError:
                submitted = None
            cmid = item.get("cmid") or item.get("coursemodule")
            return {
                "key": f"moodle:{kind}:{item['id']}",
                "kind": kind,
                "course": course.get("fullname") or course.get("shortname") or "",
                "course_id": item["course"],
                "shortname": course.get("shortname") or "",
                "title": (item.get("name") or "").strip(),
                "due": self._iso(due),
                "opens": self._iso(opens),
                "cutoff": self._iso(cutoff),
                "submitted": submitted,
                "url": f"{base_url}/mod/{kind}/view.php?id={cmid}",
                "description": html_to_markdown(item.get("intro") or "").strip()[:4000],
            }

        facts = [
            fact(
                "assign",
                a,
                a.get("duedate"),
                a.get("allowsubmissionsfromdate"),
                a.get("cutoffdate"),
                api.assignment_submitted,
            )
            for a in assignments
        ]
        facts += [
            fact("quiz", q, q.get("timeclose"), q.get("timeopen"), None, api.quiz_finished)
            for q in quizzes
        ]
        facts.sort(key=lambda f: (f["due"] is None, f["due"] or "", f["key"]))
        path = self.vault.resolve(TASKS_FILE)
        previous = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if previous.get("tasks") != facts:  # unchanged facts keep the file (and git) quiet
            payload = {"timezone": str(self.tz), "generated_at": now_iso(), "tasks": facts}
            write_atomic(path, json.dumps(payload, indent=2, ensure_ascii=False))
        report.tasks = len(facts)
        return facts

    def _deadline_lines(self, facts: list[dict[str, Any]]) -> list[str]:
        dated = [f for f in facts if f["due"]]
        if not dated:
            return []
        lines = ["", "## Deadlines", ""]
        for f in dated:
            due = datetime.fromisoformat(f["due"]).strftime("%d.%m.%Y %H:%M")
            done = " · submitted ✓" if f["submitted"] else ""
            title = _label(f["title"])
            lines.append(f"- **{due}** · [{title}]({link(f['url'])}) · {f['kind']}{done}")
        return lines

    # -- status ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        state = self.load_state()
        files = state["files"].values()
        return {
            "folder": f"{self.target_dir}/",
            "files": sum(1 for e in files if e.get("note") and not e.get("deleted")),
            "selected": state["selected"],
            "last": state["last"],
        }


def _not_mirrored(filename: str, size: int) -> str | None:
    if not supported(filename):
        suffix = PurePosixPath(filename).suffix.lower() or "no extension"
        return f"{suffix} is not mirrored"
    if size > MAX_BYTES:
        return f"larger than {MAX_BYTES // (1024 * 1024)} MB"
    return None


def _label(text: str) -> str:
    return text.replace("[", "(").replace("]", ")").replace("|", "-")
