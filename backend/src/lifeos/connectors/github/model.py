"""Tickets: the normalised form of a GitHub issue, its vault file and the 3-way merge.

A ticket file is Markdown with YAML frontmatter::

    ---
    title: Fix the login
    state: open
    labels: [bug]
    assignees: []
    project:
      Status: In Progress
    number: 12
    repo: me/board
    url: https://github.com/me/board/issues/12
    updated: '2026-01-02T10:00:00Z'
    ---

    Issue body …

``title``, ``state``, ``labels``, ``assignees``, ``project`` and the body are editable;
the rest is maintained by the sync. A file without ``number`` is a new ticket that the
next plan creates on GitHub.

The *base* of a ticket is the last GitHub state the sync has seen. Local changes are
the difference between the file and its base; that is what a plan writes back.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

EDITABLE = ("title", "state", "labels", "assignees", "project", "body")
STATES = ("open", "closed")
CONFLICT_MARK = "<<<<<<< "


def norm_body(text: str | None) -> str:
    return (text or "").replace("\r\n", "\n").strip("\n")


def norm_value(value: Any) -> str | None:
    """Project field values are compared as strings (YAML may load dates and numbers)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


@dataclass
class Ticket:
    repo: str
    number: int | None
    title: str
    state: str = "open"
    labels: list[str] = field(default_factory=list)
    assignees: list[str] = field(default_factory=list)
    project: dict[str, str | None] = field(default_factory=dict)
    body: str = ""
    url: str | None = None
    updated: str | None = None
    conflicts: list[str] = field(default_factory=list)

    def normalised(self) -> Ticket:
        return Ticket(
            repo=self.repo,
            number=self.number,
            title=self.title.strip(),
            state=self.state if self.state in STATES else "open",
            labels=sorted(set(self.labels), key=str.casefold),
            assignees=sorted(set(self.assignees), key=str.casefold),
            project={k: norm_value(v) for k, v in sorted(self.project.items())},
            body=norm_body(self.body),
            url=self.url,
            updated=self.updated,
            conflicts=list(self.conflicts),
        )

    def editable(self) -> dict[str, Any]:
        n = self.normalised()
        return {
            "title": n.title,
            "state": n.state,
            "labels": n.labels,
            "assignees": n.assignees,
            "project": {k: v for k, v in n.project.items() if v is not None},
            "body": n.body,
        }

    def to_dict(self) -> dict[str, Any]:
        n = self.normalised()
        return {
            "repo": n.repo,
            "number": n.number,
            "title": n.title,
            "state": n.state,
            "labels": n.labels,
            "assignees": n.assignees,
            "project": n.project,
            "body": n.body,
            "url": n.url,
            "updated": n.updated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Ticket:
        return cls(
            repo=str(data["repo"]),
            number=data.get("number"),
            title=str(data.get("title") or ""),
            state=str(data.get("state") or "open"),
            labels=[str(x) for x in data.get("labels") or []],
            assignees=[str(x) for x in data.get("assignees") or []],
            project=dict(data.get("project") or {}),
            body=str(data.get("body") or ""),
            url=data.get("url"),
            updated=data.get("updated"),
        ).normalised()


# -- files ---------------------------------------------------------------------


def slugify(title: str, max_len: int = 50) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "ticket"


def dump(ticket: Ticket) -> str:
    n = ticket.normalised()
    meta: dict[str, Any] = {
        "title": n.title,
        "state": n.state,
        "labels": n.labels,
        "assignees": n.assignees,
        "project": {k: v for k, v in n.project.items() if v is not None},
    }
    if n.number is not None:
        meta |= {"number": n.number, "repo": n.repo, "url": n.url, "updated": n.updated}
    else:
        meta["repo"] = n.repo
    if ticket.conflicts:
        meta["conflicts"] = sorted(set(ticket.conflicts))
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, default_flow_style=None)
    return f"---\n{front}---\n\n{n.body}\n" if n.body else f"---\n{front}---\n"


def load(text: str, repo: str) -> Ticket:
    text = text.replace("\r\n", "\n")
    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.DOTALL)
    if not match:
        raise ValueError("ticket file has no frontmatter")
    meta = yaml.safe_load(match.group(1)) or {}
    if not isinstance(meta, dict):
        raise ValueError("ticket frontmatter must be a mapping")
    number = meta.get("number")
    ticket = Ticket(
        repo=repo,
        number=int(number) if number not in (None, "") else None,
        title=str(meta.get("title") or ""),
        state=str(meta.get("state") or "open").lower(),
        labels=[str(x) for x in _as_list(meta.get("labels"))],
        assignees=[str(x) for x in _as_list(meta.get("assignees"))],
        project=dict(meta.get("project") or {}),
        body=match.group(2),
        url=meta.get("url"),
        updated=_timestamp(meta.get("updated")),
        conflicts=[str(x) for x in _as_list(meta.get("conflicts"))],
    )
    return ticket.normalised()


def _timestamp(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return None if value is None else str(value)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return list(value)


# -- 3-way merge ---------------------------------------------------------------


@dataclass
class MergeResult:
    ticket: Ticket
    conflicts: list[str]
    changed_locally: bool


def _pick(base: Any, local: Any, remote: Any) -> tuple[Any, bool]:
    """Returns (value, conflict). Local wins a conflict; the base then holds the remote value."""
    if local == remote or remote == base:
        return local, False
    if local == base:
        return remote, False
    return local, True


def _merge_set(base: list[str], local: list[str], remote: list[str]) -> list[str]:
    b, lo, r = set(base), set(local), set(remote)
    added = (lo - b) | (r - b)
    removed = (b - lo) | (b - r)
    return sorted((b | added) - removed, key=str.casefold)


def merge_text(base: str, local: str, remote: str) -> tuple[str, bool]:
    """Line-based 3-way merge with ``git merge-file``; conflicts get the usual markers."""
    if local == remote or remote == base:
        return local, False
    if local == base:
        return remote, False
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for name, text in (("local", local), ("base", base), ("github", remote)):
            p = Path(tmp) / name
            p.write_text(text + "\n", encoding="utf-8", newline="\n")
            paths.append(str(p))
        proc = subprocess.run(
            ["git", "merge-file", "-p", "-L", "local", "-L", "base", "-L", "github", *paths],
            capture_output=True,
            check=False,
        )
    if proc.returncode < 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace"))
    return norm_body(proc.stdout.decode("utf-8")), proc.returncode > 0


def merge(base: Ticket | None, local: Ticket, remote: Ticket) -> MergeResult:
    """Merge a new GitHub state into the local file. The result's base is ``remote``."""
    lo, r = local.normalised(), remote.normalised()
    # Without a merge base (first sync of an existing file, or lost state) local values
    # are kept and every difference is flagged instead of guessing.
    b = base.normalised() if base is not None else None
    conflicts = set(lo.conflicts)
    out = Ticket(repo=r.repo, number=r.number, title="", url=r.url, updated=r.updated)

    for name in ("title", "state"):
        lv, rv = getattr(lo, name), getattr(r, name)
        value, conflict = _pick(getattr(b, name), lv, rv) if b else (lv, lv != rv)
        setattr(out, name, value)
        if conflict:
            conflicts.add(name)

    for name in ("labels", "assignees"):
        if b:
            setattr(out, name, _merge_set(getattr(b, name), getattr(lo, name), getattr(r, name)))
        else:
            setattr(out, name, getattr(lo, name))
            if getattr(lo, name) != getattr(r, name):
                conflicts.add(name)

    project: dict[str, str | None] = {}
    for key in sorted(set(lo.project) | set(r.project) | (set(b.project) if b else set())):
        lv, rv = lo.project.get(key), r.project.get(key)
        if b:
            value, conflict = _pick(b.project.get(key), lv, rv)
        else:
            value, conflict = lv, lv != rv
        project[key] = value
        if conflict:
            conflicts.add(f"project.{key}")
    out.project = project

    if b:
        out.body, body_conflict = merge_text(b.body, lo.body, r.body)
    else:
        out.body, body_conflict = lo.body, lo.body != r.body
    if body_conflict:
        conflicts.add("body")

    out = out.normalised()
    out.conflicts = sorted(conflicts)
    return MergeResult(ticket=out, conflicts=sorted(conflicts), changed_locally=bool(diff(r, out)))


def diff(base: Ticket, local: Ticket) -> dict[str, tuple[Any, Any]]:
    """Editable fields that differ: ``{field: (base value, local value)}``."""
    b, lo = base.normalised(), local.normalised()
    changes: dict[str, tuple[Any, Any]] = {}
    for name in ("title", "state", "labels", "assignees", "body"):
        if getattr(b, name) != getattr(lo, name):
            changes[name] = (getattr(b, name), getattr(lo, name))
    for key in sorted(set(b.project) | set(lo.project)):
        if b.project.get(key) != lo.project.get(key):
            changes[f"project.{key}"] = (b.project.get(key), lo.project.get(key))
    return changes


def has_conflict_markers(body: str) -> bool:
    return any(line.startswith(CONFLICT_MARK) for line in body.split("\n"))
