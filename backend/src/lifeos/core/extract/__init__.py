"""Text extraction: PDF, PowerPoint, Word, HTML and plain text → Markdown.

``to_markdown`` is the single entry point used by the mirror connectors.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from lifeos.core.extract.text import ExtractError, clean, escape, md_table

SUPPORTED = {".pdf", ".pptx", ".docx", ".html", ".htm", ".txt", ".md"}


@dataclass
class Extracted:
    body: str  # Markdown without frontmatter and title
    pages: int | None  # pages or slides, if the format has them
    empty: bool  # no text at all (e.g. a scanned PDF)


def supported(filename: str) -> bool:
    return PurePosixPath(filename).suffix.lower() in SUPPORTED


def to_markdown(filename: str, data: bytes) -> Extracted:
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix == ".pdf":
        return _pdf(data)
    if suffix == ".pptx":
        from lifeos.core.extract.pptx import extract, slides_markdown

        slides = extract(data)
        has_text = any(s.title or s.blocks or s.notes for s in slides)
        return Extracted(slides_markdown(slides) if slides else "", len(slides), not has_text)
    if suffix == ".docx":
        return _docx(data)
    if suffix in (".html", ".htm"):
        body = html_to_markdown(_decode(data))
        return Extracted(body, None, not body.strip())
    if suffix in (".txt", ".md"):
        body = _decode(data).strip() + "\n"
        return Extracted(body, None, not body.strip())
    raise ExtractError(f"unsupported file type {suffix or '(none)'}")


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _pdf(data: bytes) -> Extracted:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ExtractError("the PDF is password-protected")
        pages = [page.extract_text() or "" for page in reader.pages]
    except ExtractError:
        raise
    except Exception as exc:  # pypdf raises many kinds on damaged files
        raise ExtractError("not a readable PDF (damaged?)") from exc
    out: list[str] = []
    for number, text in enumerate(pages, start=1):
        lines = [escape(line.rstrip()) for line in text.splitlines()]
        body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
        if body:
            out += [f"## Page {number}", "", body, ""]
    return Extracted("\n".join(out).strip() + "\n" if out else "", len(pages), not out)


def _docx(data: bytes) -> Extracted:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise ExtractError("not a readable .docx (password-protected or damaged?)") from exc
    out: list[str] = []
    for block in document.iter_inner_content():
        if isinstance(block, Table):
            table = md_table([[cell.text for cell in row.cells] for row in block.rows])
            if table:
                out += [table, ""]
            continue
        if not isinstance(block, Paragraph):
            continue
        text = clean(block.text)
        if not text:
            continue
        style = (block.style.name if block.style is not None else "") or ""
        level = re.match(r"Heading (\d)", style)
        if level:
            out += [f"{'#' * min(int(level.group(1)) + 1, 6)} {text}", ""]
        elif style.startswith("List"):
            out.append(f"- {escape(text)}")
        else:
            out += [escape(text), ""]
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return Extracted(body + "\n" if body else "", None, not body)


def html_to_markdown(html: str) -> str:
    from markdownify import markdownify

    text = markdownify(html, heading_style="ATX", strip=["script", "style", "img"])
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n" if text.strip() else ""


__all__ = ["ExtractError", "Extracted", "html_to_markdown", "supported", "to_markdown"]
