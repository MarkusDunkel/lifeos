"""One-way mirror: presentations in a OneDrive folder → Markdown notes in the knowledge hub.

OneDrive is the source of truth for the .pptx files; the vault only holds their text.
Each file is identified by its drive item id, so renames and moves follow the file.
State (id → note, last content tag) lives in ``.lifeos/onedrive/presentations.json``.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from lifeos.connectors.onedrive.auth import OneDriveError
from lifeos.connectors.onedrive.client import DriveFile, GraphApi
from lifeos.connectors.onedrive.extract import (
    ExtractError,
    extract,
    front_matter,
    mark_deleted,
    render,
)
from lifeos.core.vault import KNOWLEDGE, STATE, Vault, VaultPathError, write_atomic

STATE_FILE = f"{STATE}/onedrive/presentations.json"


@dataclass
class SyncReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    renamed: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)
    synced_at: str = ""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class PresentationsSync:
    def __init__(
        self,
        vault: Vault,
        api_factory: Callable[[], GraphApi] | None,
        folder: str | None,
        target_dir: str,
    ):
        self.vault = vault
        self._api_factory = api_factory
        self.folder = (folder or "").strip().strip("/")
        self.target_dir = target_dir.strip().strip("/")
        self.zone = f"{KNOWLEDGE}/{self.target_dir}"
        vault.resolve(self.target_dir, zone=KNOWLEDGE)  # validates the setting early
        self._lock = threading.Lock()

    # -- state -------------------------------------------------------------------

    def _state_path(self) -> Path:
        return self.vault.resolve(STATE_FILE)

    def load_state(self) -> dict[str, Any]:
        path = self._state_path()
        if not path.is_file():
            return {"items": {}, "last": None}
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_state(self, state: dict[str, Any]) -> None:
        write_atomic(self._state_path(), json.dumps(state, indent=2, ensure_ascii=False))

    # -- paths -------------------------------------------------------------------

    def note_path(self, f: DriveFile) -> str:
        """Note path relative to the target folder: ``sub/Deck.pptx`` → ``sub/Deck.md``."""
        return str(PurePosixPath(f.path).with_suffix(".md"))

    def knowledge_path(self, rel: str) -> str:
        return f"{self.target_dir}/{rel}"

    def _file(self, rel: str) -> Path:
        return self.vault.resolve(rel, zone=self.zone)

    def _owned(self, path: Path, item_id: str) -> bool:
        """A file at ``path`` may be overwritten only if it is our mirror of ``item_id``."""
        if not path.exists():
            return True
        meta = front_matter(path.read_text(encoding="utf-8"))
        return meta.get("type") == "presentation" and meta.get("drive_item_id") == item_id

    # -- sync --------------------------------------------------------------------

    def sync(self) -> SyncReport:
        if self._api_factory is None:
            raise OneDriveError("OneDrive is not set up (see Settings)", 400)
        if not self.folder:
            raise OneDriveError("No OneDrive folder configured: set LIFEOS_ONEDRIVE_FOLDER", 400)
        if not self._lock.acquire(blocking=False):
            raise OneDriveError("a sync is already running", 409)
        try:
            return self._sync(self._api_factory())
        finally:
            self._lock.release()

    def _sync(self, api: GraphApi) -> SyncReport:
        report = SyncReport()
        state = self.load_state()
        items: dict[str, dict[str, Any]] = state.setdefault("items", {})
        files = api.list_files(self.folder)
        try:
            account = api.account()
        except OneDriveError:
            account = None
        state["account"] = account or state.get("account")

        seen: set[str] = set()
        for f in files:
            seen.add(f.id)
            try:
                self._one(api, f, items, report)
            except (ExtractError, OneDriveError, VaultPathError) as exc:
                report.errors.append(f"{f.path}: {exc}")

        for item_id, entry in items.items():
            if item_id in seen or entry.get("deleted"):
                continue
            path = self._file(entry["note"])
            if path.is_file() and self._owned(path, item_id):
                write_atomic(path, mark_deleted(path.read_text(encoding="utf-8")))
            entry["deleted"] = True
            report.deleted.append(self.knowledge_path(entry["note"]))

        report.synced_at = now_iso()
        state["last"] = asdict(report)
        self._save_state(state)
        return report

    def _one(
        self, api: GraphApi, f: DriveFile, items: dict[str, dict[str, Any]], report: SyncReport
    ) -> None:
        rel = self.note_path(f)
        target = self._file(rel)
        entry = items.get(f.id)
        if not self._owned(target, f.id):
            raise VaultPathError(f"a different note already exists at {self.knowledge_path(rel)}")

        moved = entry is not None and entry["note"] != rel
        if moved:
            old = self._file(entry["note"])
            if old.is_file() and self._owned(old, f.id) and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                old.replace(target)
            report.renamed.append(
                f"{self.knowledge_path(entry['note'])} → {self.knowledge_path(rel)}"
            )

        unchanged = (
            entry is not None
            and entry.get("ctag") == f.ctag
            and not entry.get("deleted")
            and not moved
            and target.is_file()
        )
        if unchanged:
            report.unchanged += 1
            return

        content = render(
            {
                "title": PurePosixPath(f.path).stem,
                "url": f.web_url,
                "path": f"{self.folder}/{f.path}",
                "id": f.id,
                "modified": f.modified,
            },
            extract(api.download(f.id)),
        )
        existed = target.is_file()
        if not existed or target.read_text(encoding="utf-8") != content:
            write_atomic(target, content)
            if not moved:
                (report.updated if existed else report.created).append(self.knowledge_path(rel))
        elif not moved:
            report.unchanged += 1
        items[f.id] = {"note": rel, "ctag": f.ctag, "deleted": False}

    # -- status ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        state = self.load_state()
        items = state.get("items", {})
        return {
            "folder": self.folder or None,
            "target": f"{self.target_dir}/",
            "presentations": sum(1 for e in items.values() if not e.get("deleted")),
            "account": state.get("account"),
            "last": state.get("last"),
        }
