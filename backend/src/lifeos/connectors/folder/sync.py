"""One-way mirror: a local folder → Markdown notes in the knowledge hub.

Built for the Teams files of a study programme: the OneDrive client syncs the document
libraries of the Teams to the PC, and this mirror turns every PDF, PowerPoint, Word, HTML
and text file below that folder into a note under ``ready/knowledge/<target>/``. The folder
stays the source of truth. ``.lifeos/folder/<name>.json`` remembers what was taken over
(per file: modification time and size), so a run only reads new or changed files. A file
that disappears keeps its note with ``status: deleted``.

The first path segment is the library (or the synced channel folder) of one team; OneDrive
names it ``<Team> - Dokumente`` / ``- Documents`` / ``- General``, the suffix is dropped in
the note path.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from lifeos.core.extract import ExtractError, supported, to_markdown
from lifeos.core.extract.text import dump_front, front_matter, mark_deleted
from lifeos.core.vault import KNOWLEDGE, STATE, Vault, VaultPathError, write_atomic

KIND = "folder-file"
MAX_BYTES = 50 * 1024 * 1024
# Office lock files, OneDrive/Windows bookkeeping and hidden files are never mirrored.
_IGNORED = re.compile(r"^(~\$|\.|desktop\.ini$|thumbs\.db$)", re.IGNORECASE)
_LIBRARY_SUFFIX = re.compile(
    r"\s+-\s+(Dokumente|Documents|Freigegebene Dokumente|Shared Documents|General|Allgemein)$",
    re.IGNORECASE,
)


class FolderError(RuntimeError):
    pass


class FolderSettings(BaseSettings):
    """Settings of the PC-side run (read from ``deploy/.env`` via ``--env-file``)."""

    model_config = SettingsConfigDict(env_prefix="LIFEOS_", extra="ignore")

    vault_root: Path = Field(description="Absolute path to the vault directory.")
    teams_files_dir: Path | None = Field(
        default=None, description="Local folder with the synced Teams document libraries."
    )
    teams_files_target: str = Field(
        default="Teams", description="Folder below ready/knowledge for the mirror notes."
    )


def load_settings(env_file: Path | None) -> FolderSettings:
    if env_file is not None:
        return FolderSettings(_env_file=env_file, _env_file_encoding="utf-8")  # type: ignore[call-arg]
    return FolderSettings()  # type: ignore[call-arg]


def safe_name(name: str, fallback: str = "untitled") -> str:
    """A file/folder name that works on Windows, in Obsidian and in wikilinks."""
    name = re.sub(r'[\\/:*?"<>|#^\[\]\x00-\x1f]', "-", name)
    name = re.sub(r"\s+", " ", name).strip(" .-")
    return name[:80].rstrip(" .-") or fallback


def library_name(segment: str) -> str:
    return _LIBRARY_SUFFIX.sub("", segment).strip() or segment


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class FolderReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # reported once per file
    errors: list[str] = field(default_factory=list)
    unchanged: int = 0
    at: str = field(default_factory=lambda: _iso(datetime.now(UTC).timestamp()))

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated or self.deleted)

    def summary(self) -> str:
        parts = [
            f"{len(self.created)} new",
            f"{len(self.updated)} updated",
            f"{len(self.deleted)} deleted",
        ]
        if self.errors:
            parts.append(f"{len(self.errors)} failed")
        return ", ".join(parts)


class FolderMirror:
    def __init__(self, vault: Vault, source: Path, target: str = "Teams", name: str = "teams"):
        self.vault = vault
        self.source = source
        self.target_dir = f"{KNOWLEDGE}/{target.strip('/')}"
        self.name = name
        self.state_rel = f"{STATE}/folder/{name}.json"
        self.last_run_rel = f"{STATE}/folder/{name}-last-run.json"

    # -- state -------------------------------------------------------------------

    def load_state(self) -> dict[str, Any]:
        path = self.vault.resolve(self.state_rel)
        if not path.is_file():
            return {"files": {}}
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("files", {})
        return data

    def _save(self, rel: str, data: dict[str, Any]) -> None:
        write_atomic(self.vault.resolve(rel), json.dumps(data, indent=2, ensure_ascii=False))

    # -- paths -------------------------------------------------------------------

    def _note_path(self, note: str) -> Path:
        return self.vault.resolve(note, zone=self.target_dir)

    def knowledge_path(self, note: str) -> str:
        return f"{self.target_dir}/{note}"

    def note_for(self, rel: str, taken: set[str]) -> str:
        """Note path (relative to the target) for a source file; unique within one run."""
        parts = PurePosixPath(rel).parts
        dirs = [safe_name(library_name(parts[0]))] if len(parts) > 1 else []
        dirs += [safe_name(p) for p in parts[1:-1]]
        stem, suffix = PurePosixPath(parts[-1]).stem, PurePosixPath(parts[-1]).suffix
        note = "/".join([*dirs, safe_name(stem) + ".md"])
        if note.lower() in taken:  # e.g. "Slides.pdf" next to "Slides.pptx"
            note = "/".join([*dirs, safe_name(f"{stem} ({suffix.lstrip('.')})") + ".md"])
        taken.add(note.lower())
        return note

    @staticmethod
    def _owned(path: Path, rel: str) -> bool:
        """A note may be overwritten only if it is our mirror of ``rel``."""
        if not path.exists():
            return True
        meta = front_matter(path.read_text(encoding="utf-8"))
        return meta.get("type") == KIND and meta.get("path") == rel

    # -- run ---------------------------------------------------------------------

    def run(self) -> FolderReport:
        if not self.source.is_dir():
            raise FolderError(f"source folder not found: {self.source}")
        report = FolderReport()
        state = self.load_state()
        files: dict[str, dict[str, Any]] = state["files"]
        seen: set[str] = set()
        taken: set[str] = set()

        for path in sorted(self.source.rglob("*"), key=lambda p: p.as_posix().lower()):
            rel = path.relative_to(self.source).as_posix()
            if any(_IGNORED.match(part) for part in PurePosixPath(rel).parts):
                continue
            if not path.is_file():
                continue
            seen.add(rel)
            try:
                self._file(path, rel, files, taken, report)
            except (OSError, VaultPathError) as exc:
                report.errors.append(f"{rel}: {exc}")

        for rel, entry in files.items():
            if rel in seen or entry.get("deleted"):
                continue
            entry["deleted"] = True
            note = entry.get("note")
            if not note:
                continue
            target = self._note_path(note)
            if target.is_file() and self._owned(target, rel):
                content = target.read_text(encoding="utf-8")
                marked = mark_deleted(content)
                if marked != content:
                    write_atomic(target, marked)
                    report.deleted.append(self.knowledge_path(note))

        self._save(self.state_rel, state)
        self._save(self.last_run_rel, asdict(report))
        return report

    def _file(
        self,
        path: Path,
        rel: str,
        files: dict[str, dict[str, Any]],
        taken: set[str],
        report: FolderReport,
    ) -> None:
        stat = path.stat()
        entry = files.get(rel)
        reason = None
        if not supported(path.name):
            reason = "file type is not mirrored"
        elif stat.st_size > MAX_BYTES:
            reason = "larger than 50 MB"
        if reason:
            if entry is None or not entry.get("skipped"):
                report.skipped.append(f"{rel}: {reason}")
            files[rel] = {"skipped": True}
            return

        note = self.note_for(rel, taken)
        target = self._note_path(note)
        if not self._owned(target, rel):
            report.errors.append(f"{rel}: a different note already exists at {note}")
            return
        mtime, size = int(stat.st_mtime), stat.st_size
        old_note = entry.get("note") if entry else None
        unchanged = (
            entry is not None
            and entry.get("mtime") == mtime
            and entry.get("size") == size
            and old_note == note
            and not entry.get("deleted")
            and target.is_file()
        )
        if unchanged:
            report.unchanged += 1
            return

        try:
            extracted = to_markdown(path.name, path.read_bytes())
        except ExtractError as exc:
            report.errors.append(f"{rel}: {exc}")
            return
        content = self._render(rel, path.name, mtime, extracted)
        existed = target.is_file()
        if not existed or target.read_text(encoding="utf-8") != content:
            write_atomic(target, content)
            (report.updated if existed else report.created).append(self.knowledge_path(note))
        else:
            report.unchanged += 1
        if old_note and old_note != note:  # the note path changed (e.g. a name collision)
            old = self._note_path(old_note)
            if old.is_file() and self._owned(old, rel):
                old.unlink()
        files[rel] = {"note": note, "mtime": mtime, "size": size, "deleted": False}

    @staticmethod
    def _render(rel: str, filename: str, mtime: int, extracted) -> str:
        parts = PurePosixPath(rel).parts
        title = PurePosixPath(filename).stem
        front: dict[str, Any] = {
            "type": KIND,
            "source": "teams",
            "title": title,
        }
        if len(parts) > 1:
            front["team"] = library_name(parts[0])
        front["path"] = rel
        front["file"] = filename
        front["modified"] = _iso(mtime)
        if extracted.pages is not None:
            front["pages"] = extracted.pages
        front["mirror"] = True
        notice = (
            f"> Mirror of `{rel}` from the synced Teams files, generated by LifeOS."
            " Changes here are overwritten on the next sync."
        )
        body = extracted.body
        if extracted.empty:
            body = "> No text found in this file (a scanned document?). Open the original.\n"
        return f"{dump_front(front)}\n# {title}\n\n{notice}\n\n{body}".rstrip() + "\n"
