"""Start an application: ticket on the board, folder in the vault, link at the posting.

Layout in the vault (private): ``ready/knowledge/Karriere/Bewerbungen/<date> <company> –
<title>/`` with ``Bewerbung.md`` (links and history) and ``Inserat.md`` (a snapshot, since
postings go offline). Which ticket belongs to which folder is kept in
``.lifeos/applications/index.json``.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from lifeos.core.extract.text import dump_front
from lifeos.core.vault import STATE, Vault, write_atomic
from lifeos.modules.applications.board import (
    COMPANY_FIELD,
    LABEL,
    NEW,
    POSTING_FIELD,
    STATUS,
    split_project,
)
from lifeos.modules.applications.profile import CAREER
from lifeos.modules.jobsearch.model import Posting
from lifeos.modules.jobsearch.store import JobStore

APPLICATIONS_DIR = f"{CAREER}/Bewerbungen"
STATE_DIR = f"{STATE}/applications"
INDEX_FILE = f"{STATE_DIR}/index.json"
BOARD_STATE_FILE = f"{STATE_DIR}/board.json"


class ApplicationError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class ManualPosting(BaseModel):
    """A posting from outside the import (pasted by the user)."""

    company: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    place: str = Field(default="", max_length=200)
    url: str = Field(default="", max_length=1000)
    text: str = Field(min_length=1, max_length=100_000)


def folder_name(day: str, company: str, title: str) -> str:
    """``2026-11-20 Firma – Stelle``, safe on every file system."""
    title = re.sub(r"\((?:[mwfdx]\s*/\s*){2}[mwfdx]\)|\(all genders?\)", "", title, flags=re.I)
    text = f"{day} {company.strip()} – {title.strip()}"
    text = re.sub(r'[\\/:*?"<>|#^\[\]]', " ", text)
    return re.sub(r"\s+", " ", text).strip()[:120].rstrip(" .")


def posting_markdown(posting: Posting, imported: str | None) -> str:
    front = {
        "firma": posting.company,
        "stelle": posting.title,
        "ort": posting.place(),
        "quelle": posting.url,
        "inserat": posting.key,
        "importiert": imported,
    }
    facts = []
    if posting.salary:
        unit = {"YEAR": "Jahr", "MONTH": "Monat", "HOUR": "Stunde"}.get(posting.salary.unit, "")
        facts.append(f"- Gehalt: ab {posting.salary.amount:,.0f} € / {unit}".replace(",", "."))
    if posting.employment_type:
        facts.append(f"- Anstellung: {', '.join(posting.employment_type)}")
    if posting.skills:
        facts.append(f"- Technologien: {', '.join(posting.skills)}")
    if posting.benefits:
        facts.append(f"- Benefits: {', '.join(posting.benefits)}")
    body = [f"# {posting.title} – {posting.company}", ""]
    if facts:
        body += facts + [""]
    body += [posting.description.strip(), ""]
    return dump_front({k: v for k, v in front.items() if v}) + "\n" + "\n".join(body)


def issue_body(posting: Posting, folder: str) -> str:
    lines = [
        f"**{posting.company}** – {posting.title}",
        "",
        f"- Ort: {posting.place() or '–'}",
        f"- Inserat: {posting.url}" if posting.url else "- Inserat: (manuell erfasst)",
    ]
    if posting.skills:
        lines.append(f"- Technologien: {', '.join(posting.skills)}")
    lines += [
        "",
        f"Unterlagen im Vault: `{folder}`",
        "",
        "Ziehe das Ticket auf **Aktiv**, damit LifeOS Anschreiben und Lebenslauf entwirft.",
    ]
    return "\n".join(lines)


class Applications:
    def __init__(
        self,
        vault: Vault,
        jobs: JobStore,
        github: Callable[[], Any] | None,
        repo: str | None,
        project_ref: str | None,
        committer: Callable[[list[str], str], None] | None = None,
        timezone: str = "UTC",
    ) -> None:
        self.vault = vault
        self.jobs = jobs
        self.github = github
        self.repo = repo
        self.project_ref = project_ref
        self.committer = committer
        self.tz = ZoneInfo(timezone)
        self._lock = threading.Lock()

    def missing(self) -> list[str]:
        missing = []
        if self.github is None:
            missing.append("LIFEOS_GITHUB_TOKEN")
        if not self.repo:
            missing.append("LIFEOS_APPLICATIONS_REPO (or LIFEOS_GITHUB_REPOS)")
        if not self.project_ref:
            missing.append("LIFEOS_APPLICATIONS_PROJECT")
        return missing

    def index(self) -> dict[str, dict[str, Any]]:
        path = self.vault.resolve(INDEX_FILE)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def list(self) -> list[dict[str, Any]]:
        rows = [{"issue": int(n), **entry} for n, entry in self.index().items()]
        return sorted(rows, key=lambda r: r.get("created", ""), reverse=True)

    def start(self, posting_key: str | None = None, manual: ManualPosting | None = None) -> dict:
        if self.missing():
            raise ApplicationError(f"not set up: {', '.join(self.missing())}", 409)
        if (posting_key is None) == (manual is None):
            raise ApplicationError("give either a posting or a manual posting")
        with self._lock:
            imported = None
            if posting_key is not None:
                posting = self.jobs.posting(posting_key)
                entry = self.jobs.index().get(posting_key)
                if posting is None or entry is None:
                    raise ApplicationError("posting not found", 404)
                if entry.get("application"):
                    raise ApplicationError("already in application: " + entry["application"], 409)
                imported = entry.get("firstSeen")
            else:
                assert manual is not None
                posting = Posting(
                    source="manual",
                    id=datetime.now(UTC).strftime("%Y%m%d%H%M%S"),
                    url=manual.url,
                    title=manual.title,
                    company=manual.company,
                    description=manual.text,
                )
                if manual.place:
                    from lifeos.modules.jobsearch.model import Location

                    posting.locations = [Location(city=manual.place)]

            day = datetime.now(self.tz).date().isoformat()
            folder = f"{APPLICATIONS_DIR}/{folder_name(day, posting.company, posting.title)}"
            issue = self._create_ticket(posting, folder)

            created = datetime.now(UTC).isoformat(timespec="seconds")
            write_atomic(
                self.vault.resolve(f"{folder}/Inserat.md"), posting_markdown(posting, imported)
            )
            front = {
                "firma": posting.company,
                "stelle": posting.title,
                "ticket": issue["url"],
                "inserat": posting.key,
                "erstellt": created,
            }
            text = (
                f"# Bewerbung: {posting.company} – {posting.title}\n\n"
                f"Ticket: {issue['url']}\n\nInserat: [[Inserat]]\n\n## Verlauf\n\n"
                f"- {day}: zur Bewerbung ausgewählt (Spalte {NEW})\n"
            )
            summary = self.vault.resolve(f"{folder}/Bewerbung.md")
            write_atomic(summary, dump_front(front) + "\n" + text)

            index = self.index()
            index[str(issue["number"])] = {
                "folder": folder,
                "title": posting.title,
                "company": posting.company,
                "posting": posting.key,
                "issue_id": issue["id"],
                "issue_url": issue["url"],
                "item_id": issue["item_id"],
                "created": created,
            }
            write_atomic(
                self.vault.resolve(INDEX_FILE), json.dumps(index, ensure_ascii=False, indent=1)
            )
            if posting_key is not None:
                self.jobs.mark(posting_key, application=issue["url"])
            if self.committer:
                self.committer([folder], f"bewerbung: neu – {posting.company}, {posting.title}\n")
            return {"issue": issue["number"], "url": issue["url"], "folder": folder}

    def _create_ticket(self, posting: Posting, folder: str) -> dict[str, Any]:
        assert self.github is not None and self.repo and self.project_ref
        gh = self.github()
        meta = gh.repo(self.repo)
        if LABEL not in meta.labels:
            raise ApplicationError(f"label '{LABEL}' missing; run lifeos applications setup-board")
        owner, number = split_project(self.project_ref)
        project = gh.project(owner, number)
        for name in (STATUS, COMPANY_FIELD, POSTING_FIELD):
            if name not in project.fields:
                raise ApplicationError(f"board field '{name}' missing; run setup-board", 409)
        issue = gh.create_issue(
            meta.id,
            {
                "title": f"{posting.company} – {posting.title}"[:250],
                "body": issue_body(posting, folder),
                "labelIds": [meta.labels[LABEL]],
            },
        )
        item_id = gh.add_to_project(project.id, issue["id"])
        gh.set_field(project.id, item_id, project.fields[STATUS], NEW)
        gh.set_field(project.id, item_id, project.fields[COMPANY_FIELD], posting.company)
        if posting.url:
            gh.set_field(project.id, item_id, project.fields[POSTING_FIELD], posting.url)
        return issue | {"item_id": item_id}
