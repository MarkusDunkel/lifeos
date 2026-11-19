"""Facts from the owner's review comments flow into the master CV.

The writer returns :class:`MasterUpdate` items next to the draft: facts the owner stated
in a comment (a period, a new or corrected point, skills, a confirmation). Selections and
wording for one posting are not updates. Each update names the owner comment it comes
from; updates without a valid comment number are rejected, so research or posting texts
can never change the master.

Updates are applied to the raw YAML data (unknown fields stay), validated as a
:class:`MasterCv` and only then written back. Every changed entry, bullet or the skills
get ``origin`` (e.g. ``owner/repo#35 · CV F4 · 2026-11-19``).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from lifeos.modules.applications.model import MasterCv, SkillGroup

Op = Literal[
    "set_field",
    "set_period",
    "set_detail",
    "add_title_variant",
    "add_bullet",
    "edit_bullet",
    "remove_bullet",
    "confirm",
    "set_skills",
    "set_skill_groups",
    "add_position",
    "add_education",
]
SKILL_FIELDS = ("technical", "interests", "languages")
ENTRY_FIELDS = ("title", "company", "place", "period", "detail", "institution", "degree")


class MasterUpdate(BaseModel):
    comment: int = Field(description="number of the owner comment that states this fact")
    op: Op = Field(
        description="set_field: `entry`, `field` (title, company, place, period, detail,"
        " institution, degree) + `text` – any fact of a position or education entry;"
        " set_period / set_detail / add_title_variant: `entry` + `text`;"
        " add_bullet / edit_bullet: `entry`, `bullet` (id, new ids in kebab-case) and `text`;"
        " remove_bullet: `bullet`; confirm: `bullet` or `entry` (removes check);"
        " set_skills: `skill_field` (technical, interests, languages) + `text`;"
        " set_skill_groups: `groups`; add_position: `entry` (new id), `text` (title),"
        " `company`, `place`, `period`; add_education: `entry`, `text` (degree), `company`"
        " (institution), `period`"
    )
    entry: str = Field(default="", description="id of a position or education entry")
    bullet: str = Field(default="", description="id of a bullet")
    text: str = Field(default="", description="the new value (period, title, bullet text …)")
    field: str = Field(default="", description="for set_field: the field of the entry")
    skill_field: str = ""
    groups: list[SkillGroup] = Field(default_factory=list)
    company: str = ""
    place: str = ""
    period: str = ""

    def key(self) -> tuple[str, ...]:
        """Later updates of the same thing replace earlier ones."""
        return (self.op, self.entry, self.bullet, self.field, self.skill_field)


@dataclass
class Applied:
    data: dict[str, Any]
    master: MasterCv
    done: list[str] = field(default_factory=list)  # "<origin>: period of acme → …"
    rejected: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.done)


class UpdateError(ValueError):
    pass


def merge(earlier: list[MasterUpdate], later: list[MasterUpdate]) -> list[MasterUpdate]:
    """Updates of a revision replace those of earlier outputs for the same target."""
    out = {u.key(): u for u in earlier}
    out.update({u.key(): u for u in later})
    return list(out.values())


def _entries(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["id"]: e for kind in ("positions", "education") for e in data.get(kind) or []}


def _bullets(data: dict[str, Any]) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    return {b["id"]: (e, b) for e in _entries(data).values() for b in e.get("bullets") or []}


def _apply_one(data: dict[str, Any], u: MasterUpdate, origin: str) -> str:
    entries, bullets = _entries(data), _bullets(data)
    text = u.text.strip()

    def entry() -> dict[str, Any]:
        if u.entry not in entries:
            raise UpdateError(f"unknown entry {u.entry!r}")
        return entries[u.entry]

    def need_text() -> None:
        if not text:
            raise UpdateError("empty text")

    if u.op == "set_field":
        need_text()
        target = entry()
        if u.field not in ENTRY_FIELDS:
            raise UpdateError(f"unknown field {u.field!r}")
        is_position = "company" in target
        if u.field in (
            ("detail", "institution", "degree") if is_position else ("title", "company", "place")
        ):
            raise UpdateError(f"{u.entry!r} has no field {u.field!r}")
        old = target.get(u.field)
        if u.field == "title" and old and old != text:
            variants = target.setdefault("title_variants", [])
            if old not in variants:
                variants.append(old)  # the old title stays available
            if text in variants:
                variants.remove(text)
        target[u.field], target["origin"] = text, origin
        return f"{u.field} of {u.entry} → {text}"
    if u.op in ("set_period", "set_detail"):
        need_text()
        target, name = entry(), u.op.removeprefix("set_")
        if name == "detail" and "institution" not in target:
            raise UpdateError(f"{u.entry!r} is not an education entry")
        target[name], target["origin"] = text, origin
        return f"{name} of {u.entry} → {text}"
    if u.op == "add_title_variant":
        need_text()
        target = entry()
        if "company" not in target:
            raise UpdateError(f"{u.entry!r} is not a position")
        variants = target.setdefault("title_variants", [])
        if text != target.get("title") and text not in variants:
            variants.append(text)
        target["origin"] = origin
        return f"title variant of {u.entry}: {text}"
    if u.op in ("add_bullet", "edit_bullet"):
        need_text()
        if u.bullet in bullets:
            _, b = bullets[u.bullet]
            b.update({"text": text, "origin": origin})
            b.pop("check", None)  # the owner wrote it: confirmed
            return f"point {u.bullet} → {text}"
        if u.op == "edit_bullet" or not u.bullet:
            raise UpdateError(f"unknown point {u.bullet!r}")
        entry().setdefault("bullets", []).append({"id": u.bullet, "text": text, "origin": origin})
        return f"new point {u.bullet} in {u.entry}: {text}"
    if u.op == "remove_bullet":
        if u.bullet not in bullets:
            raise UpdateError(f"unknown point {u.bullet!r}")
        owner, b = bullets[u.bullet]
        owner["bullets"].remove(b)
        return f"point {u.bullet} removed"
    if u.op == "confirm":
        target = bullets[u.bullet][1] if u.bullet in bullets else entries.get(u.entry)
        if target is None:
            raise UpdateError(f"unknown point or entry {u.bullet or u.entry!r}")
        target.pop("check", None)
        target["origin"] = origin
        return f"{u.bullet or u.entry} confirmed"
    if u.op == "set_skills":
        need_text()
        if u.skill_field not in SKILL_FIELDS:
            raise UpdateError(f"unknown skills field {u.skill_field!r}")
        skills = data.setdefault("skills", {})
        skills[u.skill_field], skills["origin"] = text, origin
        return f"skills {u.skill_field} → {text}"
    if u.op == "set_skill_groups":
        if not u.groups:
            raise UpdateError("no groups")
        skills = data.setdefault("skills", {})
        skills["groups"] = [g.model_dump() for g in u.groups]
        skills["origin"] = origin
        return "skill groups → " + "; ".join(f"{g.label}: {g.items}" for g in u.groups)
    if u.op in ("add_position", "add_education"):
        if not u.entry or u.entry in entries:
            raise UpdateError(f"entry id {u.entry!r} is missing or exists already")
        if not (text and u.company.strip() and u.period.strip()):
            raise UpdateError("title/degree, company/institution and period are needed")
        if u.op == "add_position":
            new = {"id": u.entry, "title": text, "company": u.company.strip(),
                   "place": u.place.strip(), "period": u.period.strip(), "bullets": []}  # fmt: skip
            data.setdefault("positions", []).insert(0, new)
        else:
            new = {"id": u.entry, "institution": u.company.strip(), "degree": text,
                   "period": u.period.strip(), "bullets": []}  # fmt: skip
            data.setdefault("education", []).insert(0, new)
        new["origin"] = origin
        return f"new entry {u.entry}: {text}, {u.company.strip()}"
    raise UpdateError(f"unknown operation {u.op!r}")  # pragma: no cover - Literal guards it


def apply_updates(
    data: dict[str, Any],
    updates: list[MasterUpdate],
    origins: dict[int, str],
) -> Applied:
    """Apply updates to a copy of the raw master data.

    ``origins`` maps the numbers of the owner's comments to their origin label; an update
    citing any other number is rejected. A single invalid update is skipped, not fatal.
    """
    out = copy.deepcopy(data)
    done, rejected = [], []
    for u in updates:
        label = origins.get(u.comment)
        if label is None:
            rejected.append(
                f"{u.op} {u.entry or u.bullet}: comment {u.comment} is not from the owner"
            )
            continue
        trial = copy.deepcopy(out)
        try:
            what = _apply_one(trial, u, label)
            MasterCv.model_validate(trial)
        except (UpdateError, ValidationError, KeyError, TypeError) as exc:
            rejected.append(f"{u.op} {u.entry or u.bullet} ({label}): {exc}")
            continue
        out = trial
        done.append(f"{label}: {what}")
    return Applied(out, MasterCv.model_validate(out), done, rejected)


class _Dumper(yaml.SafeDumper):
    """Lists indented under their key, multi-line text as a block: easy to edit by hand."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        return super().increase_indent(flow, False)


def _text(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _text)


def dump(data: dict[str, Any]) -> str:
    """The master as YAML in a stable, readable form.

    Keys keep their order, lines are not wrapped, and a blank line separates the top-level
    sections and the entries of ``positions`` and ``education``.
    """
    text = yaml.dump(data, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=10_000)
    lines = text.splitlines()
    out: list[str] = []
    for i, line in enumerate(lines):
        top = i > 0 and line[:1].isalpha()
        entry = line.startswith("  - id:") and out and not out[-1].endswith(":")
        if top or entry:
            out.append("")
        out.append(line)
    return "\n".join(out) + "\n"
