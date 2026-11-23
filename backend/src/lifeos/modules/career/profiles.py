"""Job profiles and their overview, read from the vault.

Layout below ``ready/knowledge/Career/Profiles/``:

- ``<slug>/Profile.md`` – one profile, a review package (``review.yaml`` beside it). The
  document holds its own skills in a table whose first header cell is ``Skill``::

      | Skill | Share | Reason |
      |---|---|---|
      | Python | core | most postings ask for it |

  Share is ``core`` / ``secondary`` / ``edge`` (also ``Kern`` / ``Neben`` / ``Rand``).
- ``Overview.md`` – maintained by the owner: the profiles with their weight in percent and
  status (first header cell ``Profile``), the owner's own level per skill 0–3 with aliases
  (first header cell ``Skill``), and a generated curriculum block between markers.

The documents are written in the owner's language; only the first header cells and the
share/status words are read, in English or German.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lifeos.core.vault import KNOWLEDGE, Vault
from lifeos.modules.career import tables

CAREER = f"{KNOWLEDGE}/Career"
PROFILES_DIR = f"{CAREER}/Profiles"
PROFILE_FILE = "Profile.md"
OVERVIEW_FILE = f"{PROFILES_DIR}/Overview.md"

SHARES = {  # share of a skill in a profile → points
    "core": 3, "kern": 3, "haupt": 3, "main": 3,
    "secondary": 2, "neben": 2,
    "edge": 1, "rand": 1, "minor": 1,
}  # fmt: skip
SHARE_NAMES = {3: "core", 2: "secondary", 1: "edge"}
STATUSES = {
    "active": "active", "aktiv": "active",
    "paused": "paused", "pausiert": "paused",
    "draft": "draft", "entwurf": "draft",
    "dropped": "dropped", "verworfen": "dropped",
}  # fmt: skip
MAX_LEVEL = 3


def skill_key(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().strip("`*")).casefold()


@dataclass
class SkillNeed:
    name: str
    share: int  # 1 edge, 2 secondary, 3 core
    reason: str = ""


@dataclass
class Profile:
    slug: str
    title: str
    path: str  # vault-relative Profile.md
    skills: list[SkillNeed] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


@dataclass
class Entry:
    slug: str
    weight: float  # percent as written
    status: str  # active, paused, draft, dropped


@dataclass
class SkillLevel:
    name: str
    level: int | None  # 0–3; None when not filled in
    aliases: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class Overview:
    entries: list[Entry] = field(default_factory=list)
    levels: list[SkillLevel] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    exists: bool = False

    def entry(self, slug: str) -> Entry | None:
        return next((e for e in self.entries if e.slug == slug), None)

    def aliases(self) -> dict[str, str]:
        """Every spelling of a skill → its canonical key."""
        out: dict[str, str] = {}
        for lvl in self.levels:
            key = skill_key(lvl.name)
            out[key] = key
            for alias in lvl.aliases:
                out.setdefault(skill_key(alias), key)
        return out

    def level_of(self) -> dict[str, SkillLevel]:
        return {skill_key(lvl.name): lvl for lvl in self.levels}


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def parse_profile(text: str, slug: str, path: str) -> Profile:
    profile = Profile(slug=slug, title=_title(text, slug), path=path)
    table = tables.find(text, "skill", "skills", "kompetenz")
    if table is None:
        profile.problems.append("no skills table (first header cell “Skill”)")
        return profile
    share_col = table.column("share", "anteil", "gewicht")
    share_col = 1 if share_col is None else share_col
    reason_col = table.column("reason", "begründung", "beleg", "why", "warum")
    reason_col = 2 if reason_col is None and len(table.header) > 2 else reason_col
    for row in table.rows:
        name = tables.wiki_label(tables.cell(row, 0))
        if not name:
            continue
        raw = tables.cell(row, share_col).casefold().strip("*` ")
        share = SHARES.get(raw.split()[0] if raw else "")
        if share is None:
            profile.problems.append(f"{name}: unknown share “{tables.cell(row, share_col)}”")
            continue
        profile.skills.append(SkillNeed(name, share, tables.cell(row, reason_col)))
    return profile


def _number(value: str) -> float | None:
    match = re.search(r"\d+(?:[.,]\d+)?", value)
    return float(match.group(0).replace(",", ".")) if match else None


def parse_overview(text: str) -> Overview:
    overview = Overview(exists=True)
    table = tables.find(text, "profile", "profil", "profiles", "profile (ordner)")
    if table is None:
        overview.problems.append("no profile table (first header cell “Profile”)")
    else:
        weight_col = table.column("weight", "gewicht", "%")
        status_col = table.column("status")
        for row in table.rows:
            target = tables.wiki_target(tables.cell(row, 0))
            slug = target.rstrip("/").split("/")[-1]
            if slug.casefold() == PROFILE_FILE.casefold().removesuffix(".md"):
                slug = target.rstrip("/").split("/")[-2] if "/" in target else slug
            if not slug:
                continue
            weight = _number(tables.cell(row, 1 if weight_col is None else weight_col))
            raw = tables.cell(row, 2 if status_col is None else status_col).casefold()
            status = STATUSES.get(raw.strip("*` ").split()[0] if raw.strip() else "", "active")
            overview.entries.append(Entry(slug, weight or 0.0, status))
    skills = tables.find(text, "skill", "skills", "kompetenz")
    if skills is not None:
        level_col = skills.column("level", "stand", "stufe")
        alias_col = skills.column("aliases", "synonyme", "auch", "alias")
        note_col = skills.column("note", "notiz", "notes", "anmerkung")
        for row in skills.rows:
            name = tables.wiki_label(tables.cell(row, 0))
            if not name:
                continue
            raw = tables.cell(row, 1 if level_col is None else level_col)
            match = re.search(r"\d", raw)
            level = min(int(match.group(0)), MAX_LEVEL) if match else None
            aliases = [a.strip() for a in tables.cell(row, alias_col).split(",") if a.strip()]
            overview.levels.append(SkillLevel(name, level, aliases, tables.cell(row, note_col)))
    return overview


class CareerFiles:
    """Reads the profiles and the overview from the vault."""

    def __init__(self, vault: Vault) -> None:
        self.vault = vault

    def profiles(self) -> list[Profile]:
        root = self.vault.resolve(PROFILES_DIR)
        if not root.is_dir():
            return []
        out = []
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            file = folder / PROFILE_FILE
            if not file.is_file():
                continue
            rel = f"{PROFILES_DIR}/{folder.name}/{PROFILE_FILE}"
            out.append(parse_profile(file.read_text(encoding="utf-8"), folder.name, rel))
        return out

    def overview_text(self) -> str:
        path = self.vault.resolve(OVERVIEW_FILE)
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def overview(self) -> Overview:
        text = self.overview_text()
        return parse_overview(text) if text else Overview()
