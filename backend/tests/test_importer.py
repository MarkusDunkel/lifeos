from pathlib import Path

import pytest

from lifeos.core.importer import import_tree
from lifeos.core.vault_init import init_vault


def test_import_skips_vcs_and_existing(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / ".git").mkdir(parents=True)
    (src / ".git" / "config").write_text("x")
    (src / ".obsidian").mkdir()
    (src / ".obsidian" / "app.json").write_text("{}")
    (src / "Sub").mkdir()
    (src / "Sub" / "a.md").write_text("A")
    (src / "b.md").write_text("B")

    vault = tmp_path / "vault"
    init_vault(vault)
    (vault / "ready/knowledge/Dest").mkdir(parents=True)
    (vault / "ready/knowledge/Dest/b.md").write_text("mine")

    dry = import_tree(src, vault, "ready/knowledge/Dest", dry_run=True)
    assert not (vault / "ready/knowledge/Dest/Sub/a.md").exists()
    report = import_tree(src, vault, "ready/knowledge/Dest", dry_run=False)
    assert dry.copied == report.copied == ["ready/knowledge/Dest/Sub/a.md"]
    assert report.skipped_existing == ["ready/knowledge/Dest/b.md"]
    assert (vault / "ready/knowledge/Dest/b.md").read_text() == "mine"


def test_import_refuses_destination_outside_ready(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    init_vault(tmp_path / "vault")
    with pytest.raises(ValueError):
        import_tree(tmp_path / "src", tmp_path / "vault", ".md4h", dry_run=True)


def test_init_vault_is_idempotent_and_blocks_push(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    init_vault(vault)
    assert init_vault(vault) == ["vault already initialised"]
    hook = (vault / ".git/hooks/pre-push").read_text()
    assert "exit 1" in hook
