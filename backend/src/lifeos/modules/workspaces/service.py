"""Workspaces: short-lived AI reports in ``ready/workspaces`` with feedback rounds.

Reports are read-only. Feedback is written as md4h rounds under
``.md4h/feedback/ready/workspaces/<file>--<round>/feedback.md`` (see ``feedback.py``).
A round starts as a draft and is sealed together with the SHA-256 of the report; a
draft becomes stale (read-only) as soon as the report changes on disk.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from lifeos.core.vault import FEEDBACK, WORKSPACES, Vault, VaultPathError, write_atomic
from lifeos.modules.knowledge.index import title_of
from lifeos.modules.workspaces.feedback import HANDOFF_PROMPT, Item, Round, Scope, parse, render

ARCHIVE = "archive"
CHANGELOG = "aenderungsprotokoll"
ROUNDS_ZONE = f"{FEEDBACK}/{WORKSPACES}"
ROUND_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{4}$")
MAX_TEXT = 20_000


class FeedbackError(Exception):
    """A feedback operation is not possible in the current state (HTTP ``status``)."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


@dataclass
class RoundInfo:
    round: str
    state: str
    items: int
    created_at: str
    sealed_at: str | None
    stale: bool  # the report changed since the round was started
    editable: bool  # a draft written by LifeOS that still matches the report


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


def round_payload(r: Round, stale: bool) -> dict:
    return {**asdict(r), "stale": stale, "editable": r.state == "draft" and not stale}


class WorkspaceService:
    def __init__(self, vault: Vault, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.vault = vault
        self.root = vault.root / WORKSPACES
        self.root.mkdir(parents=True, exist_ok=True)
        self._now = now
        self._lock = threading.Lock()

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

    # -- rounds ------------------------------------------------------------------

    def _rounds_dir(self) -> Path:
        return self.vault.resolve(WORKSPACES, zone=FEEDBACK)

    def _round_file(self, name: str, round_id: str) -> Path:
        if not ROUND_ID_RE.match(round_id):
            raise VaultPathError(f"invalid round id: {round_id}")
        if "/" in name or "\\" in name or not name.lower().endswith(".md"):
            raise VaultPathError(f"not a workspace report: {name}")
        return self.vault.resolve(f"{name}--{round_id}/feedback.md", zone=ROUNDS_ZONE)

    def _rounds(self, name: str, current_sha: str) -> list[RoundInfo]:
        base = self._rounds_dir()
        if not base.is_dir():
            return []
        rounds: list[RoundInfo] = []
        prefix = f"{name}--"
        for d in base.iterdir():
            round_id = d.name[len(prefix) :]
            if not d.name.startswith(prefix) or not ROUND_ID_RE.match(round_id):
                continue
            file = d / "feedback.md"
            if not file.is_file():
                continue
            text = file.read_text(encoding="utf-8", errors="replace")
            try:
                r = parse(text)
            except ValueError:
                continue
            stale = r.source_sha256 != current_sha
            rounds.append(
                RoundInfo(
                    round=round_id,
                    state=r.state,
                    items=len(r.items),
                    created_at=r.created_at,
                    sealed_at=r.sealed_at,
                    stale=stale,
                    editable=r.state == "draft" and not stale and _ours(text),
                )
            )
        rounds.sort(key=lambda i: i.round, reverse=True)
        return rounds

    def round(self, name: str, round_id: str) -> dict:
        source = self._report(name)
        file = self._round_file(name, round_id)
        if not file.is_file():
            raise FileNotFoundError(f"round {round_id}")
        text = file.read_text(encoding="utf-8")
        r = parse(text)
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        payload = round_payload(r, stale=r.source_sha256 != sha)
        payload["editable"] = payload["editable"] and _ours(text)
        return payload

    def _open_draft(self, name: str, round_id: str) -> tuple[Path, Round, bytes]:
        """Load a draft for editing; refuses sealed, foreign and stale rounds."""
        source = self._report_for_feedback(name)
        file = self._round_file(name, round_id)
        if not file.is_file():
            raise FileNotFoundError(f"round {round_id}")
        text = file.read_text(encoding="utf-8")
        r = parse(text)
        if r.state != "draft":
            raise FeedbackError("this round is sealed and can no longer change")
        if not _ours(text):
            raise FeedbackError("this draft was started in the editor extension; continue it there")
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != r.source_sha256:
            raise FeedbackError("the report changed since this round started; discard the draft")
        return file, r, raw

    def _report_for_feedback(self, name: str) -> Path:
        if "/" in name:
            raise FeedbackError("feedback is only possible on open reports", status=400)
        return self._report(name)

    def start(self, name: str) -> dict:
        """Resume the open LifeOS draft for ``name`` or start a new round."""
        with self._lock:
            source = self._report_for_feedback(name)
            raw = source.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            for info in self._rounds(name, sha):
                if info.editable:
                    return self.round(name, info.round)
            now = self._now()
            round_id = f"{now:%Y%m%dT%H%M%SZ}-{secrets.token_hex(2)}"
            r = Round(
                round=round_id,
                source=f"{WORKSPACES}/{name}",
                source_sha256=sha,
                state="draft",
                created_at=_iso_ms(now),
                next_id="F1",
            )
            write_atomic(self._round_file(name, round_id), render(r))
            return round_payload(r, stale=False)

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
        _check_text("feedback", feedback)
        with self._lock:
            file, r, raw = self._open_draft(name, round_id)
            lines = raw.decode("utf-8").replace("\r\n", "\n").split("\n")
            if not 1 <= start_line <= end_line <= len(lines):
                raise FeedbackError(f"lines {start_line}-{end_line} are outside the report", 400)
            if scope == "blocks":
                quote = "\n".join(lines[start_line - 1 : end_line])
            else:
                _check_text("selected text", quote or "")
            assert quote is not None
            number = r.next_number
            r.items.append(
                Item(
                    id=f"F{number}",
                    start_line=start_line,
                    end_line=end_line,
                    scope=scope,
                    quote=quote,
                    feedback=feedback.strip("\n"),
                )
            )
            r.next_id = f"F{number + 1}"
            write_atomic(file, render(r))
            return round_payload(r, stale=False)

    def update_item(self, name: str, round_id: str, item_id: str, feedback: str) -> dict:
        _check_text("feedback", feedback)
        with self._lock:
            file, r, _ = self._open_draft(name, round_id)
            item = next((i for i in r.items if i.id == item_id), None)
            if item is None:
                raise FileNotFoundError(f"feedback {item_id}")
            item.feedback = feedback.strip("\n")
            write_atomic(file, render(r))
            return round_payload(r, stale=False)

    def delete_item(self, name: str, round_id: str, item_id: str) -> dict:
        with self._lock:
            file, r, _ = self._open_draft(name, round_id)
            r.items = [i for i in r.items if i.id != item_id]  # IDs are never reused (next_id)
            write_atomic(file, render(r))
            return round_payload(r, stale=False)

    def seal(self, name: str, round_id: str) -> dict:
        with self._lock:
            file, r, _ = self._open_draft(name, round_id)
            if not r.items:
                raise FeedbackError("add at least one comment before finishing", 400)
            r.state = "sealed"
            r.sealed_at = _iso_ms(self._now())
            write_atomic(file, render(r))
            feedback_file = f"{ROUNDS_ZONE}/{name}--{round_id}/feedback.md"
            return {
                **round_payload(r, stale=False),
                "feedbackFile": feedback_file,
                "prompt": HANDOFF_PROMPT.format(feedback_file=feedback_file),
            }

    def discard(self, name: str, round_id: str) -> None:
        """Delete a LifeOS draft (also allowed when stale). Sealed rounds are never deleted."""
        with self._lock:
            file = self._round_file(name, round_id)
            if not file.is_file():
                raise FileNotFoundError(f"round {round_id}")
            text = file.read_text(encoding="utf-8")
            if parse(text).state != "draft" or not _ours(text):
                raise FeedbackError("only drafts started in LifeOS can be discarded here")
            file.unlink()
            if not any(file.parent.iterdir()):
                file.parent.rmdir()


def _ours(text: str) -> bool:
    """Rounds from the editor extension carry anchors LifeOS cannot rewrite faithfully."""
    return "md4h-target-v2:" not in text and "Asset SHA-256" not in text


def _iso_ms(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _check_text(what: str, text: str) -> None:
    if not text.strip():
        raise FeedbackError(f"{what} must not be empty", 400)
    if len(text) > MAX_TEXT:
        raise FeedbackError(f"{what} is too long (max {MAX_TEXT} characters)", 400)
