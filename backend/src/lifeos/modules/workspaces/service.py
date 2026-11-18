"""Workspaces: short-lived AI reports in ``ready/workspaces`` with feedback rounds.

Reports are read-only. Feedback is written as md4h rounds under
``.md4h/feedback/ready/workspaces/<file>--<round>/feedback.md`` (see ``feedback.py``).
A round starts as a draft and is sealed together with the SHA-256 of the report; a
draft becomes stale (read-only) as soon as the report changes on disk.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from lifeos.core.vault import WORKSPACES, Vault, VaultPathError
from lifeos.modules.knowledge.index import title_of
from lifeos.modules.workspaces.feedback import Scope
from lifeos.modules.workspaces.rounds import (  # noqa: F401 - re-exported
    FeedbackError,
    FeedbackRounds,
    RoundInfo,
    round_payload,
)

ARCHIVE = "archive"
CHANGELOG = "aenderungsprotokoll"


@dataclass
class FileInfo:
    path: str
    name: str
    title: str
    modified: str
    archived: bool
    changelog: str | None
    rounds: list[RoundInfo] = field(default_factory=list)


@dataclass
class ReportDocument:
    path: str
    content: str
    sha256: str
    archived: bool
    changelog: str | None
    rounds: list[RoundInfo]


def _utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class WorkspaceService:
    def __init__(self, vault: Vault, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.vault = vault
        self.root = vault.root / WORKSPACES
        self.root.mkdir(parents=True, exist_ok=True)
        self.feedback = FeedbackRounds(vault, now)

    # -- reports -----------------------------------------------------------------

    def _report(self, rel: str) -> Path:
        """Resolve ``<name>.md``, ``archive/<name>.md`` or a change log of a report."""
        parts = rel.split("/")
        if len(parts) > 2 or (len(parts) == 2 and parts[0] not in (ARCHIVE, CHANGELOG)):
            raise VaultPathError(f"not a workspace report: {rel}")
        if not rel.lower().endswith(".md"):
            raise VaultPathError(f"not a Markdown report: {rel}")
        path = self.vault.resolve(rel, zone=WORKSPACES)
        if not path.is_file():
            raise FileNotFoundError(rel)
        return path

    def _changelog_for(self, name: str) -> str | None:
        return f"{CHANGELOG}/{name}" if (self.root / CHANGELOG / name).is_file() else None

    def _info(self, path: Path, rel: str, archived: bool) -> FileInfo:
        content = path.read_text(encoding="utf-8", errors="replace")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        return FileInfo(
            path=rel,
            name=path.name,
            title=title_of(rel, content),
            modified=_utc(path.stat().st_mtime),
            archived=archived,
            changelog=self._changelog_for(path.name),
            rounds=self._rounds(path.name, sha),
        )

    def list(self) -> dict[str, list[dict]]:
        def scan(directory: Path, prefix: str, archived: bool) -> list[dict]:
            if not directory.is_dir():
                return []
            files = [
                p
                for p in directory.glob("*.md")
                if p.is_file() and not p.is_symlink() and not p.name.startswith(".")
            ]
            infos = [self._info(p, prefix + p.name, archived) for p in files]
            infos.sort(key=lambda i: i.modified, reverse=True)
            return [asdict(i) for i in infos]

        return {
            "open": scan(self.root, "", False),
            "archived": scan(self.root / ARCHIVE, f"{ARCHIVE}/", True),
        }

    def read(self, rel: str) -> ReportDocument:
        path = self._report(rel)
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        top = "/" not in rel
        return ReportDocument(
            path=rel,
            content=raw.decode("utf-8", errors="replace"),
            sha256=sha,
            archived=rel.startswith(f"{ARCHIVE}/"),
            changelog=self._changelog_for(path.name) if top or rel.startswith(ARCHIVE) else None,
            rounds=self._rounds(path.name, sha),
        )

    # -- rounds (see rounds.py) -------------------------------------------------

    def _rounds(self, name: str, current_sha: str) -> list[RoundInfo]:
        return self.feedback.rounds(f"{WORKSPACES}/{name}", current_sha)

    def _source_for_feedback(self, name: str) -> str:
        if "/" in name or "\\" in name:
            raise FeedbackError("feedback is only possible on open reports", status=400)
        self._report(name)
        return f"{WORKSPACES}/{name}"

    def round(self, name: str, round_id: str) -> dict:
        self._report(name)
        return self.feedback.round(f"{WORKSPACES}/{name}", round_id)

    def start(self, name: str) -> dict:
        """Resume the open LifeOS draft for ``name`` or start a new round."""
        return self.feedback.start(self._source_for_feedback(name))

    def add_item(
        self,
        name: str,
        round_id: str,
        *,
        start_line: int,
        end_line: int,
        scope: Scope,
        feedback: str,
        quote: str | None = None,
    ) -> dict:
        return self.feedback.add_item(
            self._source_for_feedback(name), round_id, start_line=start_line,
            end_line=end_line, scope=scope, feedback=feedback, quote=quote,
        )  # fmt: skip

    def update_item(self, name: str, round_id: str, item_id: str, feedback: str) -> dict:
        return self.feedback.update_item(self._source_for_feedback(name), round_id, item_id,
                                         feedback)  # fmt: skip

    def delete_item(self, name: str, round_id: str, item_id: str) -> dict:
        return self.feedback.delete_item(self._source_for_feedback(name), round_id, item_id)

    def seal(self, name: str, round_id: str) -> dict:
        return self.feedback.seal(self._source_for_feedback(name), round_id)

    def discard(self, name: str, round_id: str) -> None:
        """Delete a LifeOS draft (also allowed when stale). Sealed rounds are never deleted."""
        if "/" in name or not name.lower().endswith(".md"):
            raise VaultPathError(f"not a workspace report: {name}")
        self.feedback.discard(f"{WORKSPACES}/{name}", round_id)
