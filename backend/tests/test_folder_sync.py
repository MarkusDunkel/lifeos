from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from lifeos.connectors.folder.autostart import task_xml
from lifeos.connectors.folder.sync import FolderError, FolderMirror, library_name
from lifeos.core.extract.text import front_matter
from lifeos.core.vault import Vault

KB = "ready/knowledge/Teams"


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "FH"
    lib = root / "Course A - Dokumente" / "General"
    lib.mkdir(parents=True)
    (lib / "Slides.txt").write_text("Week 1: introduction\n", encoding="utf-8")
    (lib / "Slides.md").write_text("# Notes\n\nSame stem, other type.\n", encoding="utf-8")
    (lib / "Info.html").write_text("<h1>Exam</h1><p>Room 102</p>", encoding="utf-8")
    (lib / "video.mp4").write_bytes(b"\x00" * 10)
    (lib / "~$Slides.docx").write_bytes(b"lock")
    (lib / "desktop.ini").write_text("x", encoding="utf-8")
    return root


@pytest.fixture
def mirror(vault_root: Path, source: Path) -> FolderMirror:
    return FolderMirror(Vault.open(vault_root), source)


def note(vault_root: Path, rel: str) -> str:
    return (vault_root / KB / rel).read_text(encoding="utf-8")


def test_library_name_drops_onedrive_suffix() -> None:
    assert library_name("Course A - Dokumente") == "Course A"
    assert library_name("Course B - Shared Documents") == "Course B"
    assert library_name("Plain") == "Plain"


def test_first_run_mirrors_supported_files(mirror: FolderMirror, vault_root: Path) -> None:
    report = mirror.run()

    assert sorted(report.created) == [
        f"{KB}/Course A/General/Info.md",
        f"{KB}/Course A/General/Slides (txt).md",
        f"{KB}/Course A/General/Slides.md",
    ]
    assert report.skipped == ["Course A - Dokumente/General/video.mp4: file type is not mirrored"]
    assert report.errors == []

    text = note(vault_root, "Course A/General/Slides (txt).md")
    meta = front_matter(text)
    assert meta["type"] == "folder-file"
    assert meta["team"] == "Course A"
    assert meta["path"] == "Course A - Dokumente/General/Slides.txt"
    assert meta["mirror"] is True
    assert "Week 1: introduction" in text
    assert "Room 102" in note(vault_root, "Course A/General/Info.md")


def test_second_run_is_idempotent_and_skips_are_reported_once(mirror: FolderMirror) -> None:
    mirror.run()
    report = mirror.run()
    assert not report.changed
    assert report.unchanged == 3
    assert report.skipped == []


def test_changed_file_is_updated(mirror: FolderMirror, source: Path, vault_root: Path) -> None:
    mirror.run()
    path = source / "Course A - Dokumente" / "General" / "Info.html"
    path.write_text("<p>Room 205</p>", encoding="utf-8")
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 60))

    report = mirror.run()
    assert report.updated == [f"{KB}/Course A/General/Info.md"]
    assert "Room 205" in note(vault_root, "Course A/General/Info.md")


def test_deleted_file_keeps_note_marked(
    mirror: FolderMirror, source: Path, vault_root: Path
) -> None:
    mirror.run()
    (source / "Course A - Dokumente" / "General" / "Info.html").unlink()

    report = mirror.run()
    assert report.deleted == [f"{KB}/Course A/General/Info.md"]
    assert front_matter(note(vault_root, "Course A/General/Info.md"))["status"] == "deleted"
    assert not mirror.run().deleted  # reported once


def test_foreign_note_is_never_overwritten(mirror: FolderMirror, vault_root: Path) -> None:
    own = vault_root / KB / "Course A" / "General" / "Info.md"
    own.parent.mkdir(parents=True)
    own.write_text("# My own notes\n", encoding="utf-8")

    report = mirror.run()
    assert own.read_text(encoding="utf-8") == "# My own notes\n"
    assert any("a different note already exists" in e for e in report.errors)


def test_missing_source_raises(vault_root: Path, tmp_path: Path) -> None:
    with pytest.raises(FolderError):
        FolderMirror(Vault.open(vault_root), tmp_path / "missing").run()


def test_task_xml_runs_sync_with_commit() -> None:
    xml = task_xml(
        Path("C:/py/pythonw.exe"), Path("C:/env/.env"), Path("C:/app"), "PC\\me", date(2026, 1, 1)
    )
    assert "teams-files sync --commit --env-file" in xml
    assert "2026-01-01T07:00:00" in xml
