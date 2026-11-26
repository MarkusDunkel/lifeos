"""Goals: weekly quotas plus free-text rules, kept as plain Markdown in the vault.

``<planning_dir>/Goals.md`` holds

* a quota table, found by its first header cell (``Goal``/``Ziel``) with the
  columns goal, group, amount, unit, priority, note. ``amount`` is a number or
  ``auto`` (computed: study hours from upcoming exams). Unit: hours or sessions per
  week, or ``%`` for a goal inside a group;
* a ``## Rules`` / ``## Regeln`` section: soft rules the planner reads as text.

Groups (one level): a goal whose ``group`` names another top-level goal belongs to
it. The group's amount (hours) is the total; members have fixed hours, ``auto`` or a
percentage. Percentages share what is left of the total after the fixed and auto
members, so an exam that needs more study time shrinks the others, not the total.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from lifeos.modules.career import tables

GOALS_FILE = "Goals.md"
FIRST_CELLS = ("Goal", "Ziel")
RULE_HEADINGS = ("Rules", "Regeln")
HEADER = ["Ziel", "Gruppe", "Menge", "Einheit", "Priorität", "Notiz"]
AUTO = "auto"
HOURS = "hours"
SESSIONS = "sessions"
PERCENT = "percent"
UNIT_TEXT = {HOURS: "h/Woche", SESSIONS: "x/Woche", PERCENT: "%"}
PRIORITIES = ("hoch", "mittel", "niedrig")

TEMPLATE = """# Ziele

Wöchentliche Kontingente und Regeln für den Day planner. Menge `auto` = LifeOS
rechnet selbst (Uni: aus anstehenden Prüfungen und dem Lernstand). Ziele mit einer
Gruppe teilen sich deren Stunden: feste Stunden, `auto` oder `%` vom Rest.

## Kontingente

| Ziel | Gruppe | Menge | Einheit | Priorität | Notiz |
|---|---|---|---|---|---|

## Regeln

"""


@dataclass
class Goal:
    name: str
    amount: float | None  # None = auto
    unit: str  # hours | sessions | percent
    priority: str = "mittel"
    note: str = ""
    group: str = ""  # name of the parent goal; "" = top level

    @property
    def auto(self) -> bool:
        return self.amount is None

    @property
    def category(self) -> str:
        """Colour key of the goal's blocks."""
        return slug(self.name)


def parse_unit(text: str) -> str:
    t = text.strip().casefold()
    if t.startswith(("%", "prozent", "percent")):
        return PERCENT
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
            "group": table.column("Gruppe", "Group"),
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
                    amount=parse_amount(tables.cell(row, idx["amount"])),
                    unit=parse_unit(tables.cell(row, idx["unit"])),
                    priority=tables.cell(row, idx["priority"]) or "mittel",
                    note=tables.cell(row, idx["note"]),
                    group=tables.cell(row, idx["group"]),
                )
            )
    return goals, rules_of(text)


def structure(goals: list[Goal]) -> tuple[dict[str, list[Goal]], list[Goal]]:
    """Members per group name, and the top-level goals (groups and plain goals). A group
    reference to an unknown goal or to a member makes the goal top level (one level only)."""
    names = {g.name for g in goals}
    top_names = {g.name for g in goals if not g.group or g.group not in names}
    members: dict[str, list[Goal]] = {}
    top: list[Goal] = []
    for g in goals:
        if g.group and g.group in top_names and g.group != g.name:
            members.setdefault(g.group, []).append(g)
        else:
            top.append(g)
    return members, top


def resolve(goals: list[Goal], auto_hours: float) -> list[dict[str, Any]]:
    """Every goal with its weekly ``target`` (hours or sessions, percentages turned into
    hours), ``isGroup``, ``members`` and ``warnings``; each group followed by its members."""
    members, top = structure(goals)
    out: list[dict[str, Any]] = []
    for g in top:
        own = members.get(g.name, [])
        row = goal_dict(g) | {"isGroup": bool(own), "members": [m.name for m in own]}
        warnings: list[str] = []
        if not own:
            unit = SESSIONS if g.unit == SESSIONS else HOURS
            target = auto_hours if g.auto else (g.amount or 0)
            out.append(row | {"target": target, "targetUnit": unit, "warnings": warnings})
            continue
        fixed: dict[str, float] = {}
        shares: dict[str, float] = {}
        for m in own:
            if m.unit == PERCENT and not m.auto:
                shares[m.name] = m.amount or 0
            elif m.unit != SESSIONS:
                fixed[m.name] = auto_hours if m.auto else (m.amount or 0)
        fixed_sum = sum(fixed.values())
        if g.auto or g.unit != HOURS:
            warnings.append("a group needs a fixed number of hours")
            total = fixed_sum
        else:
            total = g.amount or 0
        rest = max(total - fixed_sum, 0)
        if fixed_sum > total:
            warnings.append(f"fixed and auto members need {fixed_sum:g} h, more than {total:g} h")
        pct = sum(shares.values())
        if shares and pct > 100:
            warnings.append(f"percentages add up to {pct:g} %, scaled down to 100 %")
        elif shares and pct < 100:
            warnings.append(f"only {pct:g} % of the remaining {rest:g} h are assigned")
        out.append(row | {"target": total, "targetUnit": HOURS, "warnings": warnings})
        for m in own:
            if m.name in shares:
                target, unit = round(rest * shares[m.name] / max(pct, 100), 2), HOURS
            elif m.unit == SESSIONS:
                target, unit = (m.amount or 0), SESSIONS
            else:
                target, unit = fixed[m.name], HOURS
            extra = {"target": target, "targetUnit": unit, "warnings": []}
            out.append(goal_dict(m) | {"isGroup": False, "members": []} | extra)
    return out


def _rules_span(text: str) -> tuple[int, int] | None:
    heads = "|".join(re.escape(h) for h in RULE_HEADINGS)
    # [ \t] not \s: the match must end on the heading line, or every save adds blank lines
    match = re.search(rf"^##[ \t]+({heads})[ \t]*$", text, re.M)
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
    """``text`` with the quota table (always the current header, members under their group)
    and the rules section replaced; everything else is kept."""
    if not text.strip():
        text = TEMPLATE
    members, top = structure(goals)
    ordered = [x for g in top for x in [g, *members.get(g.name, [])]]
    rows = [
        [g.name, g.group, _amount_text(g), UNIT_TEXT[g.unit], g.priority, g.note] for g in ordered
    ]
    rendered = tables.render(HEADER, rows)
    table = tables.find(text, *FIRST_CELLS)
    if table is not None:
        lines = text.splitlines()
        lines[table.start : table.end] = rendered.splitlines()
        text = "\n".join(lines) + "\n"
    else:
        text = text.rstrip() + "\n\n## Kontingente\n\n" + rendered + "\n"
    span = _rules_span(text)
    body = "\n\n" + rules.strip() + "\n\n" if rules.strip() else "\n\n"
    if span:
        text = text[: span[0]] + body + text[span[1] :].lstrip("\n")
    else:
        text = text.rstrip() + "\n\n## Regeln" + body
    return text.rstrip() + "\n"


def goal_dict(goal: Goal) -> dict[str, Any]:
    return asdict(goal) | {"auto": goal.auto, "category": goal.category}


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
