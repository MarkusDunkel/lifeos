"""Raw job postings and their processing state, kept on the server outside git.

Layout below ``.lifeos/jobsearch/`` (gitignored in the vault):

- ``postings/<source>/<id>.json``: one posting as imported
- ``index.json``: per posting ``firstSeen``, ``lastSeen``, ``goneAt``, ``state``, rating
- ``imports.json``: the latest import runs

The web process (imports) and the worker (evaluation) both write, so every
read-modify-write of the index holds a thread lock and a file lock.
"""

from __future__ import annotations

import contextlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lifeos.core.locks import FileLock
from lifeos.core.vault import STATE, Vault, write_atomic
from lifeos.modules.jobsearch.model import Posting

ROOT = f"{STATE}/jobsearch"
MAX_RUNS = 50

# Processing states of a posting.
NEW = "new"  # imported, not evaluated yet
OUT_OF_REGION = "out_of_region"
FILTERED = "filtered"  # failed a hard criterion or the quick pre-filter
RATED = "rated"
FAILED = "failed"  # evaluation failed; retried on the next run
STATES = (NEW, OUT_OF_REGION, FILTERED, RATED, FAILED)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class JobStore:
    def __init__(self, vault: Vault) -> None:
        self.vault = vault
        self._lock = threading.Lock()

    # -- paths ------------------------------------------------------------------

    def _path(self, rel: str) -> Path:
        return self.vault.resolve(f"{ROOT}/{rel}")

    def _posting_path(self, source: str, posting_id: str) -> Path:
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

        Known ids are marked as seen (and as back if they had gone); known ids of this
        source that are missing are marked as gone. Returns the unknown ids.
        """
        at = now_iso()
        current = set(ids)
        with self._locked():
            index = self._read("index.json", {})
            unknown: list[str] = []
            gone = back = 0
            for posting_id in ids:
                entry = index.get(f"{source}:{posting_id}")
                if entry is None or not self._posting_path(source, posting_id).exists():
                    unknown.append(posting_id)
                    continue
                entry["lastSeen"] = at
                if entry.pop("goneAt", None):
                    back += 1
            prefix = f"{source}:"
            for key, entry in index.items():
                missing = key.startswith(prefix) and key[len(prefix) :] not in current
                if missing and not entry.get("goneAt"):
                    entry["goneAt"] = at
                    gone += 1
            self._write("index.json", index)
        return {"unknown": unknown, "known": len(ids) - len(unknown), "gone": gone, "back": back}

    def add(self, postings: list[Posting]) -> int:
        at = now_iso()
        with self._locked():
            index = self._read("index.json", {})
            for posting in postings:
                write_atomic(
                    self._posting_path(posting.source, posting.id),
                    posting.model_dump_json(indent=1),
                )
                entry = index.setdefault(posting.key, {"firstSeen": at, "state": NEW})
                entry["lastSeen"] = at
                entry.pop("goneAt", None)
            self._write("index.json", index)
        return len(postings)

    def finish(self, run: dict[str, Any]) -> dict[str, Any]:
        record = {"at": now_iso(), **run}
        with self._locked():
            runs = self._read("imports.json", [])
            runs = ([record] + runs)[:MAX_RUNS]
            self._write("imports.json", runs)
        return record

    def runs(self) -> list[dict[str, Any]]:
        return self._read("imports.json", [])

    # -- reading and evaluation state --------------------------------------------

    def index(self) -> dict[str, dict[str, Any]]:
        return self._read("index.json", {})

    def posting(self, key: str) -> Posting | None:
        source, _, posting_id = key.partition(":")
        path = self._posting_path(source, posting_id)
        if not path.exists():
            return None
        return Posting.model_validate_json(path.read_text(encoding="utf-8"))

    def keys_in_state(self, *states: str) -> list[str]:
        return [
            key
            for key, entry in self.index().items()
            if entry.get("state") in states and not entry.get("goneAt")
        ]

    def update(self, key: str, **fields: Any) -> None:
        with self._locked():
            index = self._read("index.json", {})
            if key in index:
                index[key].update(fields)
                self._write("index.json", index)

    def counts(self) -> dict[str, int]:
        counts = dict.fromkeys(STATES, 0)
        gone = 0
        for entry in self.index().values():
            if entry.get("goneAt"):
                gone += 1
            else:
                counts[entry.get("state", NEW)] = counts.get(entry.get("state", NEW), 0) + 1
        return counts | {"gone": gone}
