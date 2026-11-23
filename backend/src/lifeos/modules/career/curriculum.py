"""The target curriculum: what to learn first, derived from the weighted profiles.

For every skill of the active profiles::

    importance = Σ profile weight (normalised to 1) × share (core 3 / secondary 2 / edge 1) / 3
    gap        = (3 − own level) / 3          (a skill without a level counts as level 0)
    priority   = importance × gap

Skills are merged across profiles by name and by the aliases in the overview. The result
is deterministic and recalculated whenever a profile or the overview changes; it is shown
on the career page and written as a generated block into ``Overview.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lifeos.agents.comments import Texts
from lifeos.modules.career import tables
from lifeos.modules.career.profiles import (
    MAX_LEVEL,
    SHARE_NAMES,
    Overview,
    Profile,
    skill_key,
)

BLOCK_START = "<!-- lifeos:curriculum:start -->"
BLOCK_END = "<!-- lifeos:curriculum:end -->"
NOW, SOON = 0.35, 0.15  # priority thresholds of the buckets


@dataclass
class Source:
    slug: str
    title: str
    share: str  # core, secondary, edge


@dataclass
class Item:
    key: str
    name: str
    importance: float
    level: int | None
    priority: float
    bucket: str  # now, soon, later, covered
    sources: list[Source] = field(default_factory=list)


@dataclass
class WeightedProfile:
    slug: str
    title: str
    status: str
    weight: float  # as written (percent)
    share: float  # normalised over the active profiles, 0–1
    skills: int
    problems: list[str] = field(default_factory=list)


@dataclass
class Curriculum:
    items: list[Item]
    profiles: list[WeightedProfile]
    warnings: list[str] = field(default_factory=list)


def bucket(priority: float, level: int | None) -> str:
    if level is not None and level >= MAX_LEVEL:
        return "covered"
    if priority >= NOW:
        return "now"
    if priority >= SOON:
        return "soon"
    return "later"


def compute(profiles: list[Profile], overview: Overview) -> Curriculum:
    warnings = list(overview.problems)
    by_slug = {p.slug: p for p in profiles}
    weighted: list[WeightedProfile] = []
    for p in profiles:
        entry = overview.entry(p.slug)
        status = entry.status if entry else "draft"
        weighted.append(WeightedProfile(p.slug, p.title, status, entry.weight if entry else 0.0,
                                        0.0, len(p.skills), list(p.problems)))  # fmt: skip
        if entry is None:
            warnings.append(f"{p.title}: not in the overview yet (counts as draft)")
    for e in overview.entries:
        if e.slug not in by_slug:
            warnings.append(f"overview: no profile folder “{e.slug}”")
    active = [w for w in weighted if w.status == "active"]
    total = sum(w.weight for w in active)
    if active and total <= 0:
        for w in active:
            w.share = 1 / len(active)
        warnings.append("no weights set: the active profiles count equally")
    else:
        for w in active:
            w.share = w.weight / total if total else 0.0
        if active and abs(total - 100) > 0.5:
            warnings.append(f"the weights of the active profiles add up to {total:g} %, not 100 %")

    aliases = overview.aliases()
    levels = overview.level_of()
    items: dict[str, Item] = {}
    for w in active:
        for need in by_slug[w.slug].skills:
            key = aliases.get(skill_key(need.name), skill_key(need.name))
            item = items.get(key)
            if item is None:
                known = levels.get(key)
                item = Item(key, known.name if known else need.name, 0.0,
                            known.level if known else None, 0.0, "")  # fmt: skip
                items[key] = item
            item.importance += w.share * need.share / 3
            item.sources.append(Source(w.slug, w.title, SHARE_NAMES[need.share]))
    for item in items.values():
        level = item.level or 0
        item.importance = round(min(item.importance, 1.0), 4)
        item.priority = round(item.importance * (MAX_LEVEL - level) / MAX_LEVEL, 4)
        item.bucket = bucket(item.priority, item.level)
    ordered = sorted(items.values(), key=lambda i: (-i.priority, -i.importance, i.name.casefold()))
    missing = [i.name for i in ordered if i.level is None]
    if missing:
        warnings.append(f"no own level yet for {len(missing)} skill(s) (counted as 0)")
    return Curriculum(ordered, weighted, warnings)


# -- the generated block in Overview.md ------------------------------------------------------


def block(curriculum: Curriculum, texts: Texts, day: str) -> str:
    t = texts
    lines = [BLOCK_START, t("curriculum_heading"), "", t("curriculum_intro", date=day), ""]
    names = {"now": t("curriculum_now"), "soon": t("curriculum_soon"),
             "later": t("curriculum_later"), "covered": t("curriculum_covered")}  # fmt: skip
    for key in ("now", "soon", "later", "covered"):
        rows = [i for i in curriculum.items if i.bucket == key]
        if not rows:
            continue
        lines += [f"### {names[key]}", ""]
        header = [t("curriculum_col_rank"), t("curriculum_col_skill"),
                  t("curriculum_col_priority"), t("curriculum_col_importance"),
                  t("curriculum_col_level"), t("curriculum_col_profiles")]  # fmt: skip
        body = []
        for i in rows:
            rank = curriculum.items.index(i) + 1
            sources = ", ".join(f"{s.title} ({t('share_' + s.share)})" for s in i.sources)
            level = "–" if i.level is None else str(i.level)
            body.append([str(rank), i.name, f"{i.priority:.2f}", f"{i.importance:.2f}", level,
                         sources])  # fmt: skip
        lines += [tables.render(header, body), ""]
    if curriculum.warnings:
        lines += [t("curriculum_warnings"), ""] + [f"- {w}" for w in curriculum.warnings] + [""]
    lines.append(BLOCK_END)
    return "\n".join(lines)


def with_block(text: str, generated: str) -> str:
    """``text`` with its generated block replaced (or appended when there is none)."""
    pattern = re.compile(re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END), re.S)
    if pattern.search(text):
        return pattern.sub(lambda _: generated, text, count=1)
    return text.rstrip() + "\n\n" + generated + "\n"
