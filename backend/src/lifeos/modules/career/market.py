"""What the imported postings say about the market: skill and title frequencies."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from lifeos.modules.jobsearch.store import is_primary

_WORD = re.compile(r"\(.*?\)|\s[|/–-]\s.*$|,.*$")


def title_stem(title: str) -> str:
    """'Senior Python Developer (m/w/d) – Wien' → 'senior python developer'."""
    return re.sub(r"\s+", " ", _WORD.sub("", title)).strip().casefold()


def market_stats(index: dict[str, dict[str, Any]], top: int = 50) -> dict[str, Any]:
    active = [e for e in index.values() if is_primary(e)]
    skills: Counter[str] = Counter()
    names: dict[str, str] = {}
    for e in active:
        seen: set[str] = set()
        for s in (s.strip() for s in e.get("skills") or [] if s and s.strip()):
            if s.casefold() in seen:
                continue
            seen.add(s.casefold())
            skills[s.casefold()] += 1
            names.setdefault(s.casefold(), s)
    titles = Counter(title_stem(e.get("title", "")) for e in active if e.get("title"))
    return {
        "postings": len(active),
        "skills": [(names[k], n) for k, n in skills.most_common(top)],
        "titles": [(t, n) for t, n in titles.most_common(top // 2) if t],
    }


def market_markdown(stats: dict[str, Any]) -> str:
    lines = [f"Active postings (without duplicates): {stats['postings']}", "",
             "Skills named in postings (count):"]  # fmt: skip
    lines += [f"- {name}: {n}" for name, n in stats["skills"]] or ["- (none)"]
    lines += ["", "Frequent job titles (count):"]
    lines += [f"- {t}: {n}" for t, n in stats["titles"]] or ["- (none)"]
    return "\n".join(lines)
