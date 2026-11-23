"""Writing the career documents: a profile from the designer's draft, the overview rows.

The profile is rendered from structured output, so its skills table always parses; its
history is kept from the existing file and extended by one line per change. The overview
belongs to the owner: the code only appends rows for new profiles and skills and rewrites
the generated curriculum block – existing rows are never changed.
"""

from __future__ import annotations

import re

import yaml

from lifeos.agents.comments import Texts
from lifeos.modules.career import tables
from lifeos.modules.career.profiles import PROFILE_FILE, parse_overview, skill_key
from lifeos.modules.career.schema import LevelProposal, ProfileDraft

SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    table = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
    return SLUG_RE.sub("-", value.casefold().translate(table)).strip("-")[:60] or "profile"


def history(text: str, texts: Texts) -> list[str]:
    """The list items under the history heading of an existing profile."""
    heading = texts("profile_history").strip()
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        return []
    out = []
    for line in lines[start + 1 :]:
        if line.startswith("#"):
            break
        if line.strip():
            out.append(line.rstrip())
    return out


def _list(items: list[str]) -> list[str]:
    return [f"- {i.strip()}" for i in items if i.strip()] or ["- –"]


def render_profile(draft: ProfileDraft, texts: Texts, past: list[str], entry: str) -> str:
    t = texts
    header = [t("profile_col_skill"), t("profile_col_share"), t("profile_col_reason")]
    rows = [[s.skill, t(f"share_{s.share}"), s.reason] for s in draft.skills]
    lines = [
        f"# {draft.title.strip()}", "",
        t("profile_summary"), "", draft.summary.strip(), "",
        t("profile_direction"), "", draft.direction.strip(), "",
        t("profile_fit"), "", *_list(draft.fit), "",
        t("profile_titles"), "", *_list(draft.titles), "",
        t("profile_exclusions"), "", *_list(draft.exclusions), "",
        t("profile_skills"), "", t("profile_skills_intro"), "", tables.render(header, rows), "",
        t("profile_market"), "", draft.market.strip() or "–", "",
        t("profile_gaps"), "", *_list(draft.gaps), "",
        t("profile_history"), "", *past,
    ]  # fmt: skip
    if entry:
        lines.append(entry)
    return "\n".join(lines).rstrip() + "\n"


def package_yaml(title: str, ticket: str | None) -> str:
    data: dict = {"title": title}
    if ticket:
        data["ticket"] = ticket
    data["documents"] = [{"label": title, "source": PROFILE_FILE, "view": {"type": "markdown"}}]
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def new_overview(texts: Texts) -> str:
    t = texts
    profiles = tables.render(
        [t("overview_col_profile"), t("overview_col_title"), t("overview_col_weight"),
         t("overview_col_status")], [])  # fmt: skip
    skills = tables.render(
        [t("overview_col_skill"), t("overview_col_level"), t("overview_col_aliases"),
         t("overview_col_note")], [])  # fmt: skip
    return "\n".join([
        t("overview_title"), "", t("overview_intro"), "",
        t("overview_profiles"), "", t("overview_statuses"), "", profiles, "",
        t("overview_skills"), "", t("overview_skills_intro"), "", skills, "",
    ])  # fmt: skip


def add_to_overview(
    text: str,
    texts: Texts,
    profiles: list[tuple[str, str, float]],  # (slug, title, weight %)
    levels: list[LevelProposal],
) -> str:
    """Rows for profiles and skills the overview does not have yet; nothing else changes."""
    text = text or new_overview(texts)
    overview = parse_overview(text)
    known = {e.slug for e in overview.entries}
    table = tables.find(text, "profile", "profil", "profiles")
    if table is not None:
        rows = [_row(table, {0: f"`{slug}`", "title": title, "weight": f"{weight:g}",
                             "status": texts("status_draft")})
                for slug, title, weight in profiles if slug not in known]  # fmt: skip
        text = tables.append_rows(text, table, rows)
    seen = set(overview.aliases())
    new = []
    for p in levels:
        key = skill_key(p.skill)
        if key in seen:
            continue
        seen.add(key)
        new.append({0: p.skill, "level": str(p.level),
                    "note": texts("level_proposed", reason=p.reason)})  # fmt: skip
    skills = tables.find(text, "skill", "skills", "kompetenz")
    if skills is not None:
        text = tables.append_rows(text, skills, [_row(skills, values) for values in new])
    return text


COLUMNS = {  # header names per value of an overview row
    "title": ("title", "titel", "name"),
    "weight": ("weight", "gewicht", "%"),
    "status": ("status",),
    "level": ("level", "stand", "stufe"),
    "note": ("note", "notiz", "notes", "anmerkung"),
}


def _row(table: tables.Table, values: dict) -> list[str]:
    """A row that fits the owner's table: each value in the column with its header."""
    row = [""] * len(table.header)
    row[0] = values.get(0, "")
    for name, value in values.items():
        if name == 0:
            continue
        col = table.column(*COLUMNS[name])
        if col is not None and col > 0:
            row[col] = value
    return row
