"""Review packages: what an agent hands over for review, described in ``review.yaml``.

A package is a folder below ``ready/`` with a ``review.yaml``::

    title: Bewerbung – Beispiel AG
    ticket: owner/repo#12            # optional: the ticket the review belongs to
    documents:
      - label: Anschreiben
        source: Anschreiben.md       # the content that is commented on (Markdown/text)
        view:                         # how the result looks, shown beside it
          type: pdf-generated
          generator: application-letter
          input: draft.json
      - label: Notizen
        source: Notizen.md
        view: {type: markdown}

When the documents go out (e.g. an application is sent), the package is **frozen**: every
``pdf-generated`` view is stored once as a PDF next to its source and becomes a ``pdf``
view, and ``frozen: <date>`` records when. A frozen package is a record; agents do not
rewrite it.

View types: ``markdown`` (the source, rendered), ``pdf`` (a PDF file of the package,
``file:``), ``pdf-generated`` (made on request by a registered generator from ``input:``,
never stored) and ``none``. Paths are relative to the package folder and stay inside it.
LifeOS knows nothing about applications or other kinds of documents – only packages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

import yaml

PACKAGE_FILE = "review.yaml"
VIEW_TYPES = ("markdown", "pdf", "pdf-generated", "none")


class PackageError(ValueError):
    """``review.yaml`` is missing or invalid."""


@dataclass
class View:
    type: str
    file: str | None = None  # pdf
    generator: str | None = None  # pdf-generated
    input: str | None = None  # pdf-generated


@dataclass
class Document:
    label: str
    source: str  # relative to the package folder
    view: View


@dataclass
class Package:
    path: str  # vault-relative folder
    title: str
    ticket: str | None
    documents: list[Document] = field(default_factory=list)
    frozen: str | None = None  # date the generated views were stored as files

    def rel(self, inner: str) -> str:
        return f"{self.path}/{inner}"


def _inner(value: Any, what: str) -> str:
    text = str(value or "").strip()
    parts = PurePosixPath(text).parts
    if not text or text.startswith("/") or ".." in parts or "\\" in text:
        raise PackageError(f"{what} must be a path inside the package: {text!r}")
    return text


def parse_package(text: str, path: str) -> Package:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PackageError(f"{path}/{PACKAGE_FILE}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise PackageError(f"{path}/{PACKAGE_FILE}: expected a mapping")
    documents = []
    for i, raw in enumerate(data.get("documents") or [], 1):
        if not isinstance(raw, dict):
            raise PackageError(f"{path}/{PACKAGE_FILE}: document {i} must be a mapping")
        view_raw = raw.get("view") or {"type": "none"}
        if isinstance(view_raw, str):
            view_raw = {"type": view_raw}
        kind = str(view_raw.get("type", "none"))
        if kind not in VIEW_TYPES:
            raise PackageError(f"{path}/{PACKAGE_FILE}: document {i}: unknown view type {kind!r}")
        view = View(type=kind)
        if kind == "pdf":
            view.file = _inner(view_raw.get("file"), f"document {i}: view.file")
        if kind == "pdf-generated":
            view.generator = str(view_raw.get("generator") or "")
            view.input = _inner(view_raw.get("input"), f"document {i}: view.input")
            if not view.generator:
                raise PackageError(f"{path}/{PACKAGE_FILE}: document {i}: view.generator missing")
        source = _inner(raw.get("source"), f"document {i}: source")
        documents.append(Document(label=str(raw.get("label") or source), source=source, view=view))
    if not documents:
        raise PackageError(f"{path}/{PACKAGE_FILE}: no documents")
    title = str(data.get("title") or PurePosixPath(path).name)
    ticket = data.get("ticket")
    frozen = data.get("frozen")
    return Package(path=path, title=title, ticket=str(ticket) if ticket else None,
                   documents=documents, frozen=str(frozen) if frozen else None)  # fmt: skip
