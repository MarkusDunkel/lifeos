"""The evening journal, check-ins of planned blocks and change wishes, all in the vault.

* ``<planning_dir>/Journal/<year>/<YYYY-MM-DD>.md`` – one note per day: scales in the
  front matter (mood, energy 1–5, sleep hours, pain 0–10 with where), free text in
  sections. Readable and editable in any Markdown editor.
* ``<planning_dir>/Log/<YYYY-MM>.jsonl`` – one line per check-in of a block (append
  only, the last line per block wins); the basis for "what did I really do".
* ``<planning_dir>/Wishes.jsonl`` – change wishes for a day and how the planner
  answered them.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from lifeos.core.extract.text import dump_front, front_matter
from lifeos.core.vault import KNOWLEDGE, Vault, write_atomic

SECTIONS = {"good": "Gut", "bad": "Schlecht", "notes": "Notizen"}
SCALES = {
    "mood": "stimmung",
    "energy": "energie",
    "sleep": "schlaf",
    "pain": "schmerz",
    "pain_where": "schmerz_ort",
}
OPEN = "open"
DONE = "done"
DECLINED = "declined"


@dataclass
class Entry:
    date: str
    mood: int | None = None
    energy: int | None = None
    sleep: float | None = None
    pain: int | None = None
    pain_where: str = ""
    good: str = ""
    bad: str = ""
    notes: str = ""


def _section(body: str, heading: str) -> str:
    match = re.search(rf"^##\s+{re.escape(heading)}\s*$", body, re.M)
    if not match:
        return ""
    rest = body[match.end() :]
    nxt = re.search(r"^##\s", rest, re.M)
    return (rest[: nxt.start()] if nxt else rest).strip()


class Journal:
    def __init__(self, vault: Vault, planning_dir: str = "Planning") -> None:
        self.vault = vault
        self.dir = planning_dir
        self._lock = threading.Lock()

    def _path(self, rel: str) -> Path:
        return self.vault.resolve(f"{self.dir}/{rel}", zone=KNOWLEDGE)

    # -- journal ---------------------------------------------------------------------

    def entry_path(self, day: str) -> Path:
        d = date.fromisoformat(day)
        return self._path(f"Journal/{d.year}/{d.isoformat()}.md")

    def read(self, day: str) -> Entry:
        path = self.entry_path(day)
        if not path.is_file():
            return Entry(date=day)
        text = path.read_text(encoding="utf-8")
        front = front_matter(text)
        end = text.find("\n---\n", 4) if text.startswith("---\n") else -1
        body = text[end + 5 :] if end >= 0 else text
        entry = Entry(date=day)
        for attr, key in SCALES.items():
            value = front.get(key)
            if value is not None and value != "":
                setattr(entry, attr, value if attr == "pain_where" else _number(value))
        for attr, heading in SECTIONS.items():
            setattr(entry, attr, _section(body, heading))
        return entry

    def write(self, entry: Entry) -> Entry:
        d = date.fromisoformat(entry.date)
        front: dict[str, Any] = {"datum": d.isoformat()}
        for attr, key in SCALES.items():
            value = getattr(entry, attr)
            if value not in (None, ""):
                front[key] = value
        weekday = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
        body = f"\n# Journal {weekday[d.weekday()]}, {d:%d.%m.%Y}\n"
        for attr, heading in SECTIONS.items():
            body += f"\n## {heading}\n\n{getattr(entry, attr).strip()}\n"
        write_atomic(self.entry_path(entry.date), dump_front(front) + body)
        return self.read(entry.date)

    def recent(self, until: date, days: int = 7) -> list[Entry]:
        out = []
        for offset in range(days, -1, -1):
            d = date.fromordinal(until.toordinal() - offset)
            if self.entry_path(d.isoformat()).is_file():
                out.append(self.read(d.isoformat()))
        return out

    def entries(self, since: date, until: date) -> list[dict[str, Any]]:
        out = []
        for n in range(since.toordinal(), until.toordinal() + 1):
            d = date.fromordinal(n).isoformat()
            if self.entry_path(d).is_file():
                out.append(asdict(self.read(d)))
        return out

    # -- check-ins ---------------------------------------------------------------------

    def log_checkin(self, record: dict[str, Any], at: datetime) -> None:
        path = self._path(f"Log/{at:%Y-%m}.jsonl")
        line = json.dumps({"at": at.isoformat(timespec="seconds"), **record}, ensure_ascii=False)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    # -- wishes ------------------------------------------------------------------------

    @property
    def wishes_path(self) -> Path:
        return self._path("Wishes.jsonl")

    def wishes(self) -> list[dict[str, Any]]:
        path = self.wishes_path
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def _save_wishes(self, wishes: list[dict[str, Any]]) -> None:
        text = "".join(json.dumps(w, ensure_ascii=False) + "\n" for w in wishes)
        write_atomic(self.wishes_path, text)

    def add_wish(self, day: str, text: str, at: datetime) -> dict[str, Any]:
        date.fromisoformat(day)
        wish = {
            "id": "w" + secrets.token_hex(4),
            "created": at.isoformat(timespec="seconds"),
            "day": day,
            "text": text.strip(),
            "status": OPEN,
            "answer": "",
        }
        with self._lock:
            self._save_wishes(self.wishes() + [wish])
        return wish

    def answer_wishes(self, answers: dict[str, tuple[bool, str]], at: datetime) -> None:
        with self._lock:
            wishes = self.wishes()
            for w in wishes:
                if w["id"] in answers and w["status"] == OPEN:
                    done, how = answers[w["id"]]
                    w["status"] = DONE if done else DECLINED
                    w["answer"] = how
                    w["answered"] = at.isoformat(timespec="seconds")
            self._save_wishes(wishes)

    def delete_wish(self, wish_id: str) -> None:
        with self._lock:
            self._save_wishes([w for w in self.wishes() if w["id"] != wish_id])

    def open_wishes(self) -> list[dict[str, Any]]:
        return [w for w in self.wishes() if w["status"] == OPEN]


def _number(value: Any) -> int | float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f
