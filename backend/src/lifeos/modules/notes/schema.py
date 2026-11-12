"""Column schema for the notes grid.

The schema is inferred from the data, so the app works with any field names. An
optional ``ready/notes/schema.json`` in the vault refines it::

    {"fields": [
        {"key": "type", "label": "Type", "kind": "select", "options": ["Task", "Info"]},
        {"key": "source", "hidden": true}
    ]}

Fields listed there come first, in that order; every other field follows as inferred.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from lifeos.modules.notes.store import SYSTEM_FIELDS, Note

log = logging.getLogger(__name__)

Kind = Literal["text", "select", "string", "date", "datetime"]
KINDS: tuple[Kind, ...] = ("text", "select", "string", "date", "datetime")
SCHEMA_FILE = "schema.json"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LONG_TEXT = 120
MAX_OPTIONS = 20


@dataclass
class Field:
    key: str
    label: str
    kind: Kind
    options: list[str] = field(default_factory=list)
    readonly: bool = False
    hidden: bool = False


def label_for(key: str) -> str:
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key).replace("_", " ").split()
    text = " ".join(words).lower()
    return text[:1].upper() + text[1:]


def _kind_of(key: str, values: list[Any]) -> Kind:
    if key in SYSTEM_FIELDS:
        return "datetime"
    strings = [str(v) for v in values]
    if not strings:
        return "string"
    if all(DATE_RE.match(s) for s in strings):
        return "date"
    if any("\n" in s or len(s) > LONG_TEXT for s in strings):
        return "text"
    distinct = set(strings)
    # A short value that repeats is a category. Select fields still accept new values.
    if len(distinct) <= MAX_OPTIONS and (len(distinct) < len(strings) or len(strings) <= 2):
        return "select"
    return "string"


def infer(notes: list[Note]) -> list[Field]:
    values: dict[str, list[Any]] = {}
    for note in notes:
        for key, value in note.cells.items():
            bucket = values.setdefault(key, [])
            if value is not None and value != "":
                bucket.append(value)

    fields: list[Field] = []
    for key, vals in values.items():
        kind = _kind_of(key, vals)
        options = sorted({str(v) for v in vals}, key=str.casefold) if kind == "select" else []
        fields.append(
            Field(
                key=key,
                label=label_for(key),
                kind=kind,
                options=options,
                readonly=key in SYSTEM_FIELDS,
            )
        )
    fields.sort(key=lambda f: KINDS.index(f.kind))  # stable: keeps first-seen order per kind
    return fields


def load_overrides(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data["fields"]
        if not isinstance(entries, list):
            raise TypeError("'fields' must be a list")
        return [e for e in entries if isinstance(e, dict) and isinstance(e.get("key"), str)]
    except (ValueError, KeyError, TypeError) as exc:
        log.warning("ignoring invalid %s: %s", path.name, exc)
        return []


def build_schema(notes: list[Note], overrides: list[dict[str, Any]]) -> list[Field]:
    inferred = {f.key: f for f in infer(notes)}
    result: list[Field] = []
    for entry in overrides:
        key = entry["key"]
        base = inferred.pop(key, None) or Field(key=key, label=label_for(key), kind="string")
        kind = entry.get("kind", base.kind)
        if kind not in KINDS:
            kind = base.kind
        configured = [str(o) for o in entry.get("options") or []]
        extra = [o for o in base.options if o not in configured]
        result.append(
            Field(
                key=key,
                label=str(entry.get("label") or base.label),
                kind=kind,
                options=configured + extra if kind == "select" else [],
                readonly=base.readonly,
                hidden=bool(entry.get("hidden", False)),
            )
        )
    result.extend(inferred.values())
    return result
