"""Imported job postings, kept on the server outside git.

Layout below ``.lifeos/jobsearch/`` (gitignored in the vault):

- ``postings/<source>/<id>.json``: one posting as imported
- ``index.json``: per posting ``firstSeen``, ``lastSeen``, ``goneAt`` and duplicate links
- ``imports.json``: the latest import runs

Duplicates (see :mod:`lifeos.modules.jobsearch.dedup`): a posting that matches an
active one gets ``duplicateOf`` = the earliest active posting of that job; a posting
that matches only postings that went offline gets ``repostOf`` = the latest of them.
When a posting with duplicates goes offline, its earliest duplicate takes over.
Postings that have been offline for ``RETENTION_DAYS`` are deleted.

Every read-modify-write of the index holds a thread lock and a file lock, so the web
process and a later worker can share the store.
"""

from __future__ import annotations

import contextlib
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from lifeos.core.locks import FileLock
from lifeos.core.vault import STATE, Vault, write_atomic
from lifeos.modules.jobsearch.dedup import cities, match_key, same_place
from lifeos.modules.jobsearch.model import Posting

ROOT = f"{STATE}/jobsearch"
MAX_RUNS = 50
RETENTION_DAYS = 365

Index = dict[str, dict[str, Any]]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def is_active(entry: dict[str, Any]) -> bool:
    return not entry.get("goneAt")


def is_primary(entry: dict[str, Any]) -> bool:
    return is_active(entry) and not entry.get("duplicateOf")


class JobStore:
    def __init__(self, vault: Vault) -> None:
        self.vault = vault
        self._lock = threading.Lock()

    # -- paths and files ----------------------------------------------------------

    def _path(self, rel: str) -> Path:
        return self.vault.resolve(f"{ROOT}/{rel}")

    def _posting_path(self, key: str) -> Path:
        source, _, posting_id = key.partition(":")
        return self._path(f"postings/{source}/{posting_id}.json")

    @contextlib.contextmanager
    def _locked(self):
        with self._lock, FileLock(self._path("store.lock")):
            yield

    def _read(self, name: str, default: Any) -> Any:
        path = self._path(name)
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def _write(self, name: str, data: Any) -> None:
        write_atomic(self._path(name), json.dumps(data, ensure_ascii=False, indent=1))

    # -- import -----------------------------------------------------------------

    def diff(self, source: str, ids: list[str]) -> dict[str, Any]:
        """Compare the board's current ids with the store.

        Known ids are marked as seen (or as back if they had gone); known ids of this
        source that are missing are marked as gone. Returns the unknown ids.
        """
        at = now_iso()
        current = set(ids)
        prefix = f"{source}:"
        with self._locked():
            index: Index = self._read("index.json", {})
            unknown: list[str] = []
            back = 0
            for posting_id in ids:
                key = prefix + posting_id
                entry = index.get(key)
                if entry is None or not self._posting_path(key).exists():
                    unknown.append(posting_id)
                    continue
                entry["lastSeen"] = at
                if entry.pop("goneAt", None):
                    back += 1
            newly_gone = [
                key
                for key, entry in index.items()
                if key.startswith(prefix) and key[len(prefix) :] not in current and is_active(entry)
            ]
            for key in newly_gone:
                index[key]["goneAt"] = at
                _promote_duplicates(index, key)
            self._write("index.json", index)
        known = len(ids) - len(unknown)
        return {"unknown": unknown, "known": known, "gone": len(newly_gone), "back": back}

    def add(self, postings: list[Posting]) -> dict[str, int]:
        at = now_iso()
        added = duplicates = reposts = 0
        with self._locked():
            index: Index = self._read("index.json", {})
            self._backfill(index)
            by_match: dict[str, list[str]] = {}
            for key, entry in index.items():
                by_match.setdefault(entry["match"], []).append(key)
            for posting in postings:
                key = posting.key
                write_atomic(self._posting_path(key), posting.model_dump_json(indent=1))
                entry = index.get(key)
                if entry is not None:  # re-sent (e.g. the file was missing)
                    entry["lastSeen"] = at
                    entry.pop("goneAt", None)
                    continue
                entry = {"firstSeen": at, "lastSeen": at, **summary(posting)}
                _link(index, by_match.get(entry["match"], []), entry)
                duplicates += "duplicateOf" in entry
                reposts += "repostOf" in entry
                index[key] = entry
                by_match.setdefault(entry["match"], []).append(key)
                added += 1
            self._write("index.json", index)
        stored = len(postings)
        return {"stored": stored, "added": added, "duplicates": duplicates, "reposts": reposts}

    def _backfill(self, index: Index) -> None:
        """Give entries from before duplicate detection a match key and link them
        (in the order they were first seen)."""
        missing = [k for k, e in index.items() if "match" not in e]
        if not missing:
            return
        for key in missing:
            posting = self.posting(key)
            index[key].update(summary(posting) if posting else {"match": "", "cities": []})
        by_match: dict[str, list[str]] = {}
        for key in sorted(index, key=lambda k: index[k].get("firstSeen", "")):
            entry = index[key]
            if key in missing and entry["match"]:
                _link(index, by_match.get(entry["match"], []), entry)
            by_match.setdefault(entry["match"], []).append(key)

    def finish(self, run: dict[str, Any]) -> dict[str, Any]:
        pruned = self.prune()
        record = {"at": now_iso(), **run, "pruned": pruned}
        with self._locked():
            runs = self._read("imports.json", [])
            self._write("imports.json", ([record] + runs)[:MAX_RUNS])
        return record

    def prune(self, days: int = RETENTION_DAYS) -> int:
        """Delete postings that have been offline for more than ``days`` days."""
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
        with self._locked():
            index: Index = self._read("index.json", {})
            old = [k for k, e in index.items() if e.get("goneAt") and e["goneAt"] < cutoff]
            for key in old:
                del index[key]
                with contextlib.suppress(FileNotFoundError):
                    self._posting_path(key).unlink()
            for entry in index.values():
                if entry.get("repostOf") in old:
                    del entry["repostOf"]
            if old:
                self._write("index.json", index)
        return len(old)

    def runs(self) -> list[dict[str, Any]]:
        return self._read("imports.json", [])

    # -- reading ------------------------------------------------------------------

    def index(self) -> Index:
        return self._read("index.json", {})

    def posting(self, key: str) -> Posting | None:
        path = self._posting_path(key)
        if not path.exists():
            return None
        return Posting.model_validate_json(path.read_text(encoding="utf-8"))

    def mark(self, key: str, **fields: Any) -> None:
        """Attach extra fields to a posting's entry (e.g. ``application``)."""
        with self._locked():
            index: Index = self._read("index.json", {})
            if key in index:
                index[key].update(fields)
                self._write("index.json", index)

    def counts(self) -> dict[str, int]:
        index = self.index()
        active = [e for e in index.values() if is_active(e)]
        return {
            "active": sum(1 for e in active if not e.get("duplicateOf")),
            "duplicates": sum(1 for e in active if e.get("duplicateOf")),
            "reposts": sum(1 for e in active if e.get("repostOf")),
            "gone": len(index) - len(active),
        }


def summary(posting: Posting) -> dict[str, Any]:
    """What the index keeps per posting: enough to search and to find duplicates."""
    return {
        "title": posting.title,
        "company": posting.company,
        "skills": posting.skills,
        "match": match_key(posting),
        "cities": cities(posting),
    }


def _link(index: Index, candidates: list[str], entry: dict[str, Any]) -> None:
    """Mark ``entry`` as duplicate of the earliest active posting of the same job, or
    as repost of the latest offline one."""
    same = [k for k in candidates if same_place(entry["cities"], index[k].get("cities", []))]
    primaries = sorted(
        (k for k in same if is_primary(index[k])), key=lambda k: index[k]["firstSeen"]
    )
    offline = sorted((k for k in same if not is_active(index[k])), key=lambda k: index[k]["goneAt"])
    if primaries:
        entry["duplicateOf"] = primaries[0]
    elif offline:
        entry["repostOf"] = offline[-1]


def _promote_duplicates(index: Index, gone_key: str) -> None:
    """The earliest active duplicate of a posting that went offline becomes the primary."""
    dupes = sorted(
        (k for k, e in index.items() if e.get("duplicateOf") == gone_key and is_active(e)),
        key=lambda k: index[k]["firstSeen"],
    )
    if not dupes:
        return
    head, *rest = dupes
    del index[head]["duplicateOf"]
    for key in rest:
        index[key]["duplicateOf"] = head
