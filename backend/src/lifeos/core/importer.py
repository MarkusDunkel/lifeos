"""Copy a folder tree into the vault (used for one-off migrations).

Never overwrites existing files and skips VCS/editor state, so it is safe to
re-run. Source and destination are always given on the command line.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {".git", ".obsidian", ".trash", "__pycache__", "node_modules"}
SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}


@dataclass
class ImportReport:
    copied: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)


def import_tree(src: Path, vault_root: Path, dest_rel: str, *, dry_run: bool) -> ImportReport:
    src = src.resolve(strict=True)
    vault_root = vault_root.resolve(strict=True)
    dest = (vault_root / dest_rel).resolve()
    if not dest.is_relative_to(vault_root / "ready"):
        raise ValueError("destination must be inside the vault's ready/ folder")

    report = ImportReport()
    for file in sorted(src.rglob("*")):
        rel = file.relative_to(src)
        if not file.is_file() or file.is_symlink():
            continue
        if any(part in SKIP_DIRS for part in rel.parts) or file.name in SKIP_FILES:
            continue
        target = dest / rel
        label = target.relative_to(vault_root).as_posix()
        if target.exists():
            report.skipped_existing.append(label)
            continue
        report.copied.append(label)
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, target)
    return report
