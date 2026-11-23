"""Goals: weekly quotas plus free-text rules, kept as plain Markdown in the vault.

``<planning_dir>/Goals.md`` holds

* a quota table, found by its first header cell (``Goal``/``Ziel``) with the
  columns goal, category, amount, unit, priority, note. ``amount`` is a number
  or ``auto`` (computed, e.g. study hours from upcoming exams);
* a ``## Rules`` / ``## Regeln`` section: soft rules the planner reads as text.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from lifeos.modules.career import tables

GOALS_FILE = "Goals.md"
FIRST_CELLS = ("Goal", "Ziel")
RULE_HEADINGS = ("Rules", "Regeln")
HEADER = ["Ziel", "Kategorie", "Menge", "Einheit", "Priorität", "Notiz"]
AUTO = "auto"
HOURS = "hours"
SESSIONS = "sessions"
UNIT_TEXT = {HOURS: "h/Woche", SESSIONS: "x/Woche"}
PRIORITIES = ("hoch", "mittel", "niedrig")

TEMPLATE = """# Ziele

Wöchentliche Kontingente und Regeln für den Tagesplaner. Menge `auto` = LifeOS
rechnet selbst (Uni: aus anstehenden Prüfungen und dem Lernstand).

## Kontingente

| Ziel | Kategorie | Menge | Einheit | Priorität | Notiz |
|---|---|---|---|---|---|

## Regeln

"""


@dataclass
class Goal:
    name: str
    category: str
    amount: float | None  # None = auto
    unit: str  # hours | sessions
    priority: str = "mittel"
    note: str = ""

    @property
    def auto(self) -> bool:
        return self.amount is None


def parse_unit(text: str) -> str:
    t = text.strip().casefold()
    if t.startswith(("x", "mal", "sessions", "einheit", "count")):
        return SESSIONS
    return HOURS


def parse_amount(text: str) -> float | None:
    t = text.strip().casefold().replace(",", ".")
    if not t or t == AUTO:
        return None
    match = re.search(r"\d+(\.\d+)?", t)
    return float(match.group()) if match else None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9äöüß]+", "-", text.strip().casefold()).strip("-") or "other"


def parse(text: str) -> tuple[list[Goal], str]:
    goals: list[Goal] = []
    table = tables.find(text, *FIRST_CELLS)
    if table is not None:
        idx = {
            "category": table.column("Kategorie", "Category"),
            "amount": table.column("Menge", "Amount"),
            "unit": table.column("Einheit", "Unit"),
            "priority": table.column("Priorität", "Priority"),
            "note": table.column("Notiz", "Note"),
        }
        for row in table.rows:
            name = tables.cell(row, 0)
            if not name:
                continue
            goals.append(
                Goal(
                    name=name,
                    category=slug(tables.cell(row, idx["category"]) or name),
                    amount=parse_amount(tables.cell(row, idx["amount"])),
                    unit=parse_unit(tables.cell(row, idx["unit"])),
                    priority=tables.cell(row, idx["priority"]) or "mittel",
                    note=tables.cell(row, idx["note"]),
                )
            )
    return goals, rules_of(text)


def _rules_span(text: str) -> tuple[int, int] | None:
    heads = "|".join(re.escape(h) for h in RULE_HEADINGS)
    match = re.search(rf"^##\s+({heads})\s*$", text, re.M)
    if not match:
        return None
    rest = text[match.end() :]
    nxt = re.search(r"^##\s", rest, re.M)
    end = match.end() + (nxt.start() if nxt else len(rest))
    return match.end(), end


def rules_of(text: str) -> str:
    span = _rules_span(text)
    return text[span[0] : span[1]].strip() if span else ""


def _amount_text(goal: Goal) -> str:
    if goal.amount is None:
        return AUTO
    return f"{goal.amount:g}"


def render(text: str, goals: list[Goal], rules: str) -> str:
    """``text`` with the quota table and the rules section replaced (rest kept)."""
    if not text.strip():
        text = TEMPLATE
    rows = [
        [g.name, g.category, _amount_text(g), UNIT_TEXT[g.unit], g.priority, g.note] for g in goals
    ]
    table = tables.find(text, *FIRST_CELLS)
    rendered = tables.render(table.header if table else HEADER, rows)
    if table is not None and len(table.header) == len(HEADER):
        lines = text.splitlines()
        lines[table.start : table.end] = rendered.splitlines()
        text = "\n".join(lines) + "\n"
    else:
        if table is not None:  # unknown layout: rewrite with the standard header
            lines = text.splitlines()
            lines[table.start : table.end] = tables.render(HEADER, rows).splitlines()
            text = "\n".join(lines) + "\n"
        else:
            text = text.rstrip() + "\n\n## Kontingente\n\n" + tables.render(HEADER, rows) + "\n"
    span = _rules_span(text)
    body = "\n\n" + rules.strip() + "\n\n" if rules.strip() else "\n\n"
    if span:
        text = text[: span[0]] + body + text[span[1] :].lstrip("\n")
    else:
        text = text.rstrip() + "\n\n## Regeln" + body
    return text.rstrip() + "\n"


def goal_dict(goal: Goal) -> dict[str, Any]:
    return asdict(goal) | {"auto": goal.auto}


def auto_study_hours(subjects: list[dict[str, Any]]) -> tuple[float, list[dict[str, Any]]]:
    """Hours per week the study goal should get, from the study overview: each subject with
    an upcoming exam contributes its ``minutesPerDay`` × 7 (rounded to half hours)."""
    exams = []
    total_minutes = 0.0
    for s in subjects:
        progress = s.get("progress") or {}
        days = progress.get("examInDays")
        per_day = progress.get("minutesPerDay")
        if not progress.get("nextExam") or days is None or days <= 0:
            continue
        minutes = (per_day or 0) * 7
        total_minutes += minutes
        exams.append(
            {
                "subject": s.get("name") or s.get("key"),
                "exam": progress["nextExam"],
                "inDays": days,
                "readiness": progress.get("readiness"),
                "minutesPerDay": per_day,
            }
        )
    hours = round(total_minutes / 60 * 2) / 2
    return hours, sorted(exams, key=lambda e: e["inDays"])
