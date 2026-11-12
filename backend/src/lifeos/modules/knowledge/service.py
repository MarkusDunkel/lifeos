"""File operations on the knowledge hub (``ready/knowledge``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lifeos.core.vault import (
    KNOWLEDGE,
    RevisionConflict,
    Vault,
    VaultPathError,
    revision_of,
    write_checked,
)

TEXT_SUFFIXES = {".md", ".txt", ".canvas", ".csv", ".json", ".yml", ".yaml"}
EDITABLE_SUFFIXES = {".md", ".txt"}


@dataclass
class TreeNode:
    name: str
    path: str
    kind: str  # "dir" | "file"
    children: list[TreeNode] = field(default_factory=list)


@dataclass
class Document:
    path: str
    content: str
    revision: str


class KnowledgeService:
    def __init__(self, vault: Vault):
        self.vault = vault
        self.root = vault.root / KNOWLEDGE
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, rel: str) -> Path:
        return self.vault.resolve(rel, zone=KNOWLEDGE)

    def tree(self) -> TreeNode:
        return self._node(self.root, "")

    def _node(self, directory: Path, rel: str) -> TreeNode:
        node = TreeNode(name=directory.name if rel else "", path=rel, kind="dir")
        entries = sorted(
            (e for e in directory.iterdir() if not e.name.startswith(".") and not e.is_symlink()),
            key=lambda e: (e.is_file(), e.name.casefold()),
        )
        for entry in entries:
            child_rel = f"{rel}/{entry.name}" if rel else entry.name
            if entry.is_dir():
                node.children.append(self._node(entry, child_rel))
            else:
                node.children.append(TreeNode(name=entry.name, path=child_rel, kind="file"))
        return node

    def read(self, rel: str) -> Document:
        path = self._resolve(rel)
        if not path.is_file():
            raise FileNotFoundError(rel)
        if path.suffix.lower() not in TEXT_SUFFIXES:
            raise VaultPathError(f"not a text file: {rel}")
        revision = revision_of(path)
        assert revision is not None
        return Document(path=rel, content=path.read_text(encoding="utf-8"), revision=revision)

    def write(self, rel: str, content: str, revision: str | None) -> str:
        path = self._resolve(rel)
        if path.suffix.lower() not in EDITABLE_SUFFIXES:
            raise VaultPathError(f"only Markdown/text files can be edited: {rel}")
        return write_checked(path, content, revision)

    def delete(self, rel: str, revision: str) -> None:
        path = self._resolve(rel)
        current = revision_of(path)
        if current != revision:
            raise RevisionConflict(current)
        path.unlink()

    def asset(self, rel: str) -> Path:
        path = self._resolve(rel)
        if not path.is_file():
            raise FileNotFoundError(rel)
        return path
