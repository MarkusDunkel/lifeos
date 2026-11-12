import os
from pathlib import Path

import pytest

from lifeos.core.vault import (
    KNOWLEDGE,
    RevisionConflict,
    Vault,
    VaultPathError,
    revision_of,
    write_checked,
)


@pytest.mark.parametrize(
    "bad", ["../secret", "a/../../b", "/etc/passwd", "C:/Windows", "a\\..\\..\\b", "x:y"]
)
def test_rejects_escapes(vault_root: Path, bad: str) -> None:
    with pytest.raises(VaultPathError):
        Vault.open(vault_root).resolve(bad, zone=KNOWLEDGE)


def test_resolves_inside_zone(vault_root: Path) -> None:
    p = Vault.open(vault_root).resolve("Studium/BWL.md", zone=KNOWLEDGE)
    assert p.read_text(encoding="utf-8").startswith("# Betriebs")


def test_rejects_symlink(vault_root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = vault_root / "ready" / "knowledge" / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not permitted on this system")
    with pytest.raises(VaultPathError):
        Vault.open(vault_root).resolve("link/file.md", zone=KNOWLEDGE)


def test_write_checked_detects_conflict(tmp_path: Path) -> None:
    f = tmp_path / "a.md"
    rev = write_checked(f, "one", None)
    with pytest.raises(RevisionConflict):
        write_checked(f, "x", None)  # create, but exists
    rev2 = write_checked(f, "two", rev)
    with pytest.raises(RevisionConflict):
        write_checked(f, "three", rev)  # stale revision
    assert revision_of(f) == rev2
    assert not list(tmp_path.glob("*.tmp-*"))
