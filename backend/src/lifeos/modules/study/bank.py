"""Read subjects and question banks from ``ready/knowledge/Studium``.

Layout per subject folder::

    Studium/<Subject>/_Fach.md          frontmatter: fach, kürzel, prüfung, prüfungsformat, sprache
    Studium/<Subject>/<Topic> - Prüfen.md   question bank (cards), frontmatter: thema, kürzel
                                        and optionally prüfung / prüfungsformat, which
                                        override the subject's exam for this topic
    Studium/<Subject>/<Topic>.md        learning text (tutorial)

A card is a bold id, a dash and a title, followed by ``- Key: value`` lines::

    **LE-VOC-01** – zero-sum game
    - Definition: a situation in which one person's gain is another's loss
    - Muss-Punkte: zero-sum game

A long title may wrap onto the next lines (until the first field); an indented line
continues the previous field's value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from lifeos.core.extract.text import front_matter
from lifeos.core.vault import KNOWLEDGE, Vault

STUDY_DIR = "Studium"
SUBJECT_FILE = "_Fach.md"
BANK_SUFFIX = " - Prüfen.md"
NOT_MATERIAL = {SUBJECT_FILE, "Fortschritt.md"}

CARD_RE = re.compile(r"^\*\*(?P<id>[A-Za-z0-9][A-Za-z0-9_-]*)\*\*\s*[–—-]\s*(?P<title>.+?)\s*$")
FIELD_RE = re.compile(r"^-\s+(?P<key>[^:]{1,40}):\s*(?P<value>.*?)\s*$")


@dataclass
class Card:
    id: str
    title: str
    topic: str
    fields: dict[str, str] = field(default_factory=dict)
    exam: datetime | None = None  # exam of the card's bank (or of the subject)

    def as_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "topic": self.topic, **self.fields}


@dataclass
class Bank:
    """One question bank file. Its own ``prüfung:`` overrides the subject's exam date."""

    topic: str
    file: str  # relative to ``ready/knowledge``, e.g. "Studium/Spanish/Verbs - Prüfen.md"
    code: str
    exam: datetime | None
    own_exam: bool
    exam_format: str
    cards: list[Card]

    def summary(self) -> dict:
        return {
            "topic": self.topic,
            "file": self.file,
            "code": self.code,
            "exam": iso(self.exam),
            "ownExam": self.own_exam,
            "examFormat": self.exam_format,
            "cards": len(self.cards),
        }


@dataclass
class Subject:
    key: str  # folder name, e.g. "Spanish"
    name: str
    code: str
    folder: str  # vault-relative, e.g. "ready/knowledge/Studium/Spanish"
    exam: datetime | None  # default exam date from ``_Fach.md``
    exam_format: str
    language: str
    banks: list[Bank] = field(default_factory=list)

    @property
    def cards(self) -> list[Card]:
        return [c for b in self.banks for c in b.cards]

    @property
    def knowledge_folder(self) -> str:
        """The folder relative to ``ready/knowledge`` (paths of the knowledge API)."""
        return self.folder.removeprefix(f"{KNOWLEDGE}/")

    def card(self, card_id: str) -> Card | None:
        return next((c for c in self.cards if c.id.lower() == card_id.strip().lower()), None)

    def exams(self, now: datetime) -> list[dict]:
        """Exam dates of the subject, each with the topics (banks) it covers, by date."""
        groups: dict[datetime, list[Bank]] = {}
        for bank in self.banks:
            if bank.exam is not None:
                groups.setdefault(bank.exam, []).append(bank)
        if not groups and self.exam is not None:
            groups[self.exam] = []
        result = []
        for when in sorted(groups):
            banks = groups[when]
            formats = list(dict.fromkeys(b.exam_format for b in banks if b.exam_format))
            result.append(
                {
                    "date": when.isoformat(),
                    "format": " · ".join(formats) or self.exam_format,
                    "topics": [b.topic for b in banks],
                    "past": when <= now,
                }
            )
        return result

    def next_exam(self, now: datetime) -> datetime | None:
        """The next upcoming exam, or the latest past one if none is ahead."""
        dates = sorted({b.exam for b in self.banks if b.exam} | ({self.exam} - {None}))
        ahead = [d for d in dates if d > now]
        if ahead:
            return ahead[0]
        return dates[-1] if dates else None

    def summary(self, now: datetime) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "exam": iso(self.next_exam(now)),
            "examFormat": self.exam_format,
            "language": self.language,
            "cards": len(self.cards),
            "topics": sorted({c.topic for c in self.cards}),
            "exams": self.exams(now),
            "banks": [b.summary() for b in self.banks],
        }


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def parse_cards(text: str, topic: str, exam: datetime | None = None) -> list[Card]:
    cards: list[Card] = []
    current: Card | None = None
    title_open = False  # the title may still continue on the next line
    last_key: str | None = None
    for line in text.split("\n"):
        match = CARD_RE.match(line)
        if match:
            current = Card(match["id"], match["title"], topic, exam=exam)
            cards.append(current)
            title_open, last_key = True, None
            continue
        if current is None:
            continue
        fmatch = FIELD_RE.match(line)
        if fmatch:
            last_key = fmatch["key"].strip()
            current.fields[last_key] = fmatch["value"]
            title_open = False
        elif line.startswith("#"):
            current = None  # a heading ends the card
        elif not line.strip():
            title_open = False
        elif title_open and not line.startswith(("-", ">", "|", " ", "\t")):
            current.title = f"{current.title} {line.strip()}"
        elif last_key is not None and line.startswith((" ", "\t")):
            current.fields[last_key] = f"{current.fields[last_key]} {line.strip()}".strip()
    return cards


def parse_exam(value: object, tz: ZoneInfo) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=tz)
    if isinstance(value, date):
        return datetime.combine(value, time(8, 0), tzinfo=tz)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)
    return None


def load_bank(
    path: Path, folder_rel: str, exam: datetime | None, exam_format: str, tz: ZoneInfo
) -> Bank:
    text = read_text(path)
    meta = front_matter(text)
    topic = str(meta.get("thema") or path.name[: -len(BANK_SUFFIX)])
    own = parse_exam(meta.get("prüfung"), tz)
    bank_exam = own or exam
    return Bank(
        topic=topic,
        file=f"{folder_rel}/{path.name}",
        code=str(meta.get("kürzel") or ""),
        exam=bank_exam,
        own_exam=own is not None,
        exam_format=str(meta.get("prüfungsformat") or exam_format),
        cards=parse_cards(text, topic, bank_exam),
    )


def load_subjects(vault: Vault, tz: ZoneInfo) -> list[Subject]:
    root = vault.resolve(STUDY_DIR, zone=KNOWLEDGE)
    if not root.is_dir():
        return []
    subjects = []
    for folder in sorted(
        p for p in root.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))
    ):
        meta_file = folder / SUBJECT_FILE
        if not meta_file.is_file():
            continue
        meta = front_matter(read_text(meta_file))
        exam = parse_exam(meta.get("prüfung"), tz)
        exam_format = str(meta.get("prüfungsformat") or "")
        rel = f"{STUDY_DIR}/{folder.name}"
        subjects.append(
            Subject(
                key=folder.name,
                name=str(meta.get("fach") or folder.name),
                code=str(meta.get("kürzel") or ""),
                folder=f"{KNOWLEDGE}/{rel}",
                exam=exam,
                exam_format=exam_format,
                language=str(meta.get("sprache") or "de"),
                banks=[
                    load_bank(bank, rel, exam, exam_format, tz)
                    for bank in sorted(folder.glob(f"*{BANK_SUFFIX}"))
                ],
            )
        )
    return subjects


def material_files(vault: Vault, subject: Subject) -> list[Path]:
    """Learning texts of a subject: every note except the subject file and question banks."""
    folder = vault.resolve(subject.folder)
    return sorted(
        p
        for p in folder.glob("*.md")
        if p.name not in NOT_MATERIAL and not p.name.endswith(BANK_SUFFIX)
    )


def sections(text: str) -> list[tuple[str, str]]:
    """Split Markdown into (heading, body) pairs at ``##`` headings (frontmatter removed)."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        text = text[end + 5 :] if end >= 0 else text
    result: list[tuple[str, str]] = []
    heading, body = "", []
    for line in text.split("\n"):
        if line.startswith("## "):
            if heading or "".join(body).strip():
                result.append((heading, "\n".join(body).strip()))
            heading, body = line[3:].strip(), []
        else:
            body.append(line)
    if heading or "".join(body).strip():
        result.append((heading, "\n".join(body).strip()))
    return result
