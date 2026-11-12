"""Notes stored as JSON Lines in ``ready/notes/*.jsonl``.

Each line is one note: ``{"id": ..., "order": ..., "cells": {...}}``. ``cells`` is an
open set of fields; the app never drops keys it does not know.

Writes rewrite only the affected line (keys sorted, UTF-8 kept as is), so untouched
notes stay byte-identical and the vault's git history shows minimal diffs. The
revision of a note is a hash of its line, which gives optimistic locking per note:
editing two different notes concurrently never conflicts.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lifeos.core.vault import NOTES, RevisionConflict, Vault, VaultPathError, write_atomic

CREATED = "created"
CHANGED = "lastChanged"
SYSTEM_FIELDS = (CREATED, CHANGED)

Scalar = str | int | float | bool | None


@dataclass
class Note:
    id: str
    file: str
    order: float | None
    cells: dict[str, Any]
    revision: str


def encode(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True)


def line_revision(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()[:16]


def parse_line(line: str) -> dict[str, Any] | None:
    """A note record, or ``None`` for blank or foreign lines (which are kept untouched)."""
    if not line.strip():
        return None
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if (
        isinstance(record, dict)
        and isinstance(record.get("id"), str)
        and isinstance(record.get("cells"), dict)
    ):
        return record
    return None


def _order(record: dict[str, Any]) -> float | None:
    order = record.get("order")
    return order if isinstance(order, int | float) and not isinstance(order, bool) else None


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def clean_cells(cells: dict[str, Any]) -> dict[str, Scalar]:
    cleaned: dict[str, Scalar] = {}
    for key, value in cells.items():
        if not isinstance(key, str) or not key.strip():
            raise VaultPathError("note fields need a non-empty name")
        if value is not None and not isinstance(value, str | int | float | bool):
            raise VaultPathError(f"field {key!r} must be a scalar value")
        cleaned[key] = None if value == "" else value
    return cleaned


class NotesStore:
    def __init__(self, vault: Vault, clock: Callable[[], str] = now_iso):
        self.vault = vault
        self.root = vault.root / NOTES
        self.root.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.Lock()

    # -- reading -----------------------------------------------------------------

    def _files(self) -> list[Path]:
        names = sorted(
            p.name for p in self.root.glob("*.jsonl") if p.is_file() and not p.name.startswith(".")
        )
        return [self.vault.resolve(name, zone=NOTES) for name in names]

    @staticmethod
    def _lines(path: Path) -> list[str]:
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def _all(self) -> list[Note]:
        notes: list[Note] = []
        seen: set[str] = set()
        for path in self._files():
            for line in self._lines(path):
                record = parse_line(line)
                if record is None or record["id"] in seen:
                    continue
                seen.add(record["id"])
                notes.append(self._note(path, line, record))
        return notes

    def list(self) -> list[Note]:
        with self._lock:
            return self._all()

    def _locate(self, note_id: str) -> tuple[Path, list[str], int, dict[str, Any]]:
        for path in self._files():
            lines = self._lines(path)
            for i, line in enumerate(lines):
                record = parse_line(line)
                if record is not None and record["id"] == note_id:
                    return path, lines, i, record
        raise FileNotFoundError(f"note {note_id}")

    @staticmethod
    def _write(path: Path, lines: list[str]) -> None:
        write_atomic(path, "\n".join(lines) + "\n" if lines else "")

    @staticmethod
    def _note(path: Path, line: str, record: dict[str, Any]) -> Note:
        return Note(
            id=record["id"],
            file=path.name,
            order=_order(record),
            cells=record["cells"],
            revision=line_revision(line),
        )

    # -- writing -----------------------------------------------------------------

    def create(self, cells: dict[str, Any]) -> Note:
        with self._lock:
            now = self._clock()
            orders = [n.order for n in self._all() if n.order is not None]
            record = {
                "id": str(uuid.uuid4()),
                "order": int(max(orders, default=-1)) + 1,
                "cells": {**clean_cells(cells), CREATED: now, CHANGED: now},
            }
            path = self.vault.resolve(f"notes-{now[:4]}.jsonl", zone=NOTES)
            lines = self._lines(path)
            line = encode(record)
            lines.append(line)
            self._write(path, lines)
            return self._note(path, line, record)

    def update(self, note_id: str, cells: dict[str, Any], revision: str) -> Note:
        with self._lock:
            path, lines, i, record = self._locate(note_id)
            current = line_revision(lines[i])
            if current != revision:
                raise RevisionConflict(current)
            new_cells: dict[str, Any] = clean_cells(cells)
            if CREATED in record["cells"]:
                new_cells[CREATED] = record["cells"][CREATED]
            else:
                new_cells.pop(CREATED, None)
            new_cells[CHANGED] = self._clock()
            record["cells"] = new_cells
            lines[i] = encode(record)
            self._write(path, lines)
            return self._note(path, lines[i], record)

    def delete(self, note_id: str, revision: str) -> None:
        with self._lock:
            path, lines, i, _ = self._locate(note_id)
            current = line_revision(lines[i])
            if current != revision:
                raise RevisionConflict(current)
            del lines[i]
            self._write(path, lines)
