"""Read subjects and question banks from ``ready/knowledge/Studium``.

Layout per subject folder::

    Studium/<Subject>/_Fach.md          frontmatter: fach, kürzel, prüfung, prüfungsformat, sprache
    Studium/<Subject>/<Topic> - Prüfen.md   question bank (cards)
    Studium/<Subject>/<Topic>.md        learning text (tutorial)

A card is a bold id, a dash and a title, followed by ``- Key: value`` lines::

    **LE-VOC-01** – zero-sum game
    - Definition: a situation in which one person's gain is another's loss
    - Muss-Punkte: zero-sum game
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

    def as_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "topic": self.topic, **self.fields}


@dataclass
class Subject:
    key: str  # folder name, e.g. "Spanish"
    name: str
    code: str
    folder: str  # vault-relative, e.g. "ready/knowledge/Studium/Spanish"
    exam: datetime | None
    exam_format: str
    language: str
    cards: list[Card]

    def card(self, card_id: str) -> Card | None:
        return next((c for c in self.cards if c.id.lower() == card_id.strip().lower()), None)

    def summary(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "exam": self.exam.isoformat() if self.exam else None,
            "examFormat": self.exam_format,
            "language": self.language,
            "cards": len(self.cards),
            "topics": sorted({c.topic for c in self.cards}),
        }


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def parse_cards(text: str, topic: str) -> list[Card]:
    cards: list[Card] = []
    current: Card | None = None
    for line in text.split("\n"):
        match = CARD_RE.match(line)
        if match:
            current = Card(match["id"], match["title"], topic)
            cards.append(current)
            continue
        if current is None:
            continue
        fmatch = FIELD_RE.match(line)
        if fmatch:
            current.fields[fmatch["key"].strip()] = fmatch["value"]
        elif line.startswith("#"):
            current = None  # a heading ends the card
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
        cards: list[Card] = []
        for bank in sorted(folder.glob(f"*{BANK_SUFFIX}")):
            text = read_text(bank)
            topic = str(front_matter(text).get("thema") or bank.name[: -len(BANK_SUFFIX)])
            cards.extend(parse_cards(text, topic))
        subjects.append(
            Subject(
                key=folder.name,
                name=str(meta.get("fach") or folder.name),
                code=str(meta.get("kürzel") or ""),
                folder=f"{KNOWLEDGE}/{STUDY_DIR}/{folder.name}",
                exam=parse_exam(meta.get("prüfung"), tz),
                exam_format=str(meta.get("prüfungsformat") or ""),
                language=str(meta.get("sprache") or "de"),
                cards=cards,
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
