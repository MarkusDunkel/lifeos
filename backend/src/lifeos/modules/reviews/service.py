"""Review packages: list, read, show the rendered view, and feedback rounds on documents.

Feedback uses the same md4h rounds as workspace reports (``workspaces/rounds.py``), on the
document's ``source``. Generated views (``pdf-generated``) come from registered generators
and are cached in memory by the content of their input; nothing is written to the vault.
"""

from __future__ import annotations

import hashlib
import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

import yaml

from lifeos.agents.comments import Texts
from lifeos.core.vault import FEEDBACK, READY, Vault, VaultPathError, write_atomic
from lifeos.modules.reviews import handoff
from lifeos.modules.reviews.package import PACKAGE_FILE, Document, Package, parse_package
from lifeos.modules.workspaces.feedback import Region, Scope, parse
from lifeos.modules.workspaces.rounds import FeedbackError, FeedbackRounds, sha256_of

Generator = Callable[[Vault, Path], bytes]  # (vault, input file) -> PDF
Committer = Callable[[list[str], str], None]  # (vault paths, message), e.g. in the background
TICKET_RE = re.compile(r"^([\w.-]+/[\w.-]+)#(\d+)$")
CACHE_SIZE = 16


class ViewError(Exception):
    """A view cannot be produced (HTTP 422)."""


def application_generators() -> dict[str, Generator]:
    """PDF views of applications: the letter and the CV made from ``draft.json``."""

    def make(kind: str) -> Generator:
        def generate(vault: Vault, input_path: Path) -> bytes:
            from lifeos.modules.applications.model import Draft
            from lifeos.modules.applications.profile import load_profile
            from lifeos.modules.applications.render import render

            draft = Draft.model_validate_json(input_path.read_text(encoding="utf-8"))
            rendered = render(draft, load_profile(vault))
            return rendered.letter if kind == "letter" else rendered.cv

        return generate

    return {"application-letter": make("letter"), "application-cv": make("cv")}


def _utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class ReviewService:
    def __init__(
        self,
        vault: Vault,
        rounds: FeedbackRounds | None = None,
        generators: dict[str, Generator] | None = None,
        github: Callable[[], Any] | None = None,
        committer: Committer | None = None,
        public_url: str = "",
    ) -> None:
        self.vault = vault
        self.github = github  # a GitHub client factory; None without a token
        self.committer = committer
        self.public_url = public_url.rstrip("/")
        self.feedback = rounds or FeedbackRounds(vault)
        self.generators = generators if generators is not None else application_generators()
        self._cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        self._lock = threading.Lock()

    # -- packages ---------------------------------------------------------------------

    def _package(self, path: str) -> tuple[Package, Path]:
        path = path.strip("/")
        if not (path == READY or path.startswith(f"{READY}/")):
            raise VaultPathError(f"review packages live below {READY}/: {path}")
        folder = self.vault.resolve(path)
        file = folder / PACKAGE_FILE
        if not file.is_file():
            raise FileNotFoundError(f"{path}/{PACKAGE_FILE}")
        return parse_package(file.read_text(encoding="utf-8"), path), folder

    def load(self, path: str) -> Package:
        return self._package(path)[0]

    def _document(self, path: str, doc: int) -> tuple[Package, Document, Path]:
        package, folder = self._package(path)
        if not 0 <= doc < len(package.documents):
            raise FileNotFoundError(f"document {doc} of {path}")
        return package, package.documents[doc], folder

    def _inside(self, folder: Path, inner: str) -> Path:
        return self.vault.resolve(inner, zone=self.vault.relative(folder))

    def list(self) -> list[dict[str, Any]]:
        root = self.vault.resolve(READY)
        out = []
        for file in root.rglob(PACKAGE_FILE):
            rel_parts = file.relative_to(self.vault.root).parts
            if any(part.startswith(".") for part in rel_parts):
                continue
            path = "/".join(rel_parts[:-1])
            try:
                package = parse_package(file.read_text(encoding="utf-8"), path)
            except (OSError, ValueError) as exc:
                out.append({"path": path, "title": path, "error": str(exc),
                            "modified": _utc(file.stat().st_mtime), "documents": 0})  # fmt: skip
                continue
            out.append({
                "path": path,
                "title": package.title,
                "ticket": package.ticket,
                "frozen": package.frozen,
                "documents": len(package.documents),
                "modified": _utc(file.stat().st_mtime),
            })  # fmt: skip
        out.sort(key=lambda p: p["modified"], reverse=True)
        return out

    def package(self, path: str) -> dict[str, Any]:
        package, folder = self._package(path)
        documents = []
        for i, d in enumerate(package.documents):
            source = self._inside(folder, d.source)
            exists = source.is_file()
            content = source.read_text(encoding="utf-8", errors="replace") if exists else ""
            sha = sha256_of(source) if exists else ""
            rel = package.rel(d.source)
            documents.append({
                "index": i,
                "label": d.label,
                "source": rel,
                "exists": exists,
                "content": content,
                "sha256": sha,
                "view": d.view.type,
                "rounds": [r.__dict__ for r in self.feedback.rounds(rel, sha)] if exists else [],
            })  # fmt: skip
        return {"path": package.path, "title": package.title, "ticket": package.ticket,
                "frozen": package.frozen, "documents": documents}  # fmt: skip

    # -- the rendered view --------------------------------------------------------------

    def view(self, path: str, doc: int) -> bytes:
        """The PDF of a ``pdf`` or ``pdf-generated`` view."""
        _, d, folder = self._document(path, doc)
        if d.view.type == "pdf":
            assert d.view.file is not None
            file = self._inside(folder, d.view.file)
            if not file.is_file():
                raise FileNotFoundError(d.view.file)
            return file.read_bytes()
        if d.view.type != "pdf-generated":
            raise ViewError(f"the view of this document is {d.view.type!r}, not a PDF")
        assert d.view.input is not None and d.view.generator is not None
        generator = self.generators.get(d.view.generator)
        if generator is None:
            raise ViewError(f"no generator {d.view.generator!r}")
        source = self._inside(folder, d.view.input)
        if not source.is_file():
            raise FileNotFoundError(d.view.input)
        key = (d.view.generator, hashlib.sha256(source.read_bytes()).hexdigest())
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        try:
            pdf = generator(self.vault, source)
        except Exception as exc:  # show why, instead of a broken viewer
            raise ViewError(f"{d.view.generator}: {exc}") from exc
        with self._lock:
            self._cache[key] = pdf
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return pdf

    # -- freezing: the generated views become files ----------------------------------------

    def freeze(self, path: str, day: str) -> list[str]:
        """Store every generated view as a PDF beside its source and record the date.

        ``Anschreiben.md`` with a generated view gets ``Anschreiben.pdf``; ``review.yaml``
        then points at that file (view ``pdf``) and says ``frozen: <day>``. Returns the
        written vault paths; empty if the package was frozen before. Nothing is written
        when a view cannot be generated.
        """
        package, folder = self._package(path)
        if package.frozen:
            return []
        pdfs: list[tuple[int, str, bytes]] = []
        for i, d in enumerate(package.documents):
            if d.view.type == "pdf-generated":
                name = PurePosixPath(d.source).with_suffix(".pdf").as_posix()
                pdfs.append((i, name, self.view(path, i)))  # all first: no half-frozen package
        file = folder / PACKAGE_FILE
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
        written = []
        for i, name, pdf in pdfs:
            write_atomic(self._inside(folder, name), pdf)
            data["documents"][i]["view"] = {"type": "pdf", "file": name}
            written.append(package.rel(name))
        data["frozen"] = day
        write_atomic(file, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        return [*written, package.rel(PACKAGE_FILE)]

    # -- feedback rounds on a document ----------------------------------------------------

    def _source(self, path: str, doc: int) -> str:
        package, d, folder = self._document(path, doc)
        if not self._inside(folder, d.source).is_file():
            raise FileNotFoundError(d.source)
        rel = package.rel(d.source)
        if rel.startswith(FEEDBACK):
            raise FeedbackError("feedback files cannot be reviewed", 400)
        return rel

    def round(self, path: str, doc: int, round_id: str) -> dict:
        return self.feedback.round(self._source(path, doc), round_id)

    def start(self, path: str, doc: int) -> dict:
        return self.feedback.start(self._source(path, doc))

    def add_item(self, path: str, doc: int, round_id: str, *, start_line: int, end_line: int,
                 scope: Scope, feedback: str, quote: str | None = None,
                 region: Region | None = None) -> dict:  # fmt: skip
        if scope == "rendered-region":
            _, d, _ = self._document(path, doc)
            if d.view.type not in ("pdf", "pdf-generated"):
                raise FeedbackError("areas can only be marked in a PDF view", 400)
        return self.feedback.add_item(self._source(path, doc), round_id, start_line=start_line,
                                      end_line=end_line, scope=scope, feedback=feedback,
                                      quote=quote, region=region)  # fmt: skip

    def update_item(self, path: str, doc: int, round_id: str, item_id: str, text: str) -> dict:
        return self.feedback.update_item(self._source(path, doc), round_id, item_id, text)

    def delete_item(self, path: str, doc: int, round_id: str, item_id: str) -> dict:
        return self.feedback.delete_item(self._source(path, doc), round_id, item_id)

    def seal(self, path: str, doc: int, round_id: str) -> dict:
        return self.feedback.seal(self._source(path, doc), round_id)

    def discard(self, path: str, doc: int, round_id: str) -> None:
        self.feedback.discard(self._source(path, doc), round_id)

    # -- send feedback to the ticket --------------------------------------------------------

    def pending(self, package: Package) -> list[handoff.SentRound]:
        """The open LifeOS drafts with comments, one per source file at most."""
        out: list[handoff.SentRound] = []
        for d in package.documents:
            rel = package.rel(d.source)
            if not self.vault.resolve(rel).is_file() or any(s.source == rel for s in out):
                continue
            for info in self.feedback.rounds(rel):
                if info.editable and info.items:
                    text = self.feedback.round_file(rel, info.round).read_text(encoding="utf-8")
                    out.append(handoff.SentRound(d.label, rel, parse(text)))
                    break
        return out

    def send(self, path: str) -> dict[str, Any]:
        """Post the open comments of all documents to the ticket, then seal their rounds.

        The ticket is not moved: the owner moves it back to the agent when it should rework.
        The comment is posted first, so a failure leaves the drafts open for another try.
        """
        package, _ = self._package(path)
        match = TICKET_RE.match(package.ticket or "")
        if not match:
            raise FeedbackError("this package names no ticket (owner/repo#number)", 400)
        if self.github is None:
            raise FeedbackError("GitHub is not configured (LIFEOS_GITHUB_TOKEN)", 503)
        rounds = self.pending(package)
        if not rounds:
            raise FeedbackError("there are no comments to send", 400)

        url = f"{self.public_url}/reviews/{quote(package.path)}" if self.public_url else None
        refs = [{"label": s.label, "source": s.source, "round": s.round.round} for s in rounds]
        body = "\n".join([handoff.marker(package.path, refs),
                          handoff.summary(Texts.load(self.vault), rounds, url)])  # fmt: skip
        gh = self.github()
        issue = gh.issue(match.group(1), int(match.group(2)))
        gh.add_comment(issue["id"], body)

        paths = []
        for s in rounds:
            self.feedback.seal(s.source, s.round.round)
            paths.append(self.feedback.feedback_rel(s.source, s.round.round))
        if self.committer:
            self.committer(paths, f"review: feedback on {package.title} ({package.ticket})")
        return {
            "ticket": package.ticket,
            "url": issue.get("url"),
            "comments": sum(len(s.round.items) for s in rounds),
            "rounds": refs,
        }
