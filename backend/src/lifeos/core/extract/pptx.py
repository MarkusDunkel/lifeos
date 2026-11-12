"""PowerPoint (.pptx): text per slide — titles, bullets, tables and speaker notes."""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER

from lifeos.core.extract.text import ExtractError, clean, escape, md_table

BODY_PLACEHOLDERS = {PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT}
TITLE_PLACEHOLDERS = {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE}
SKIP_PLACEHOLDERS = {
    PP_PLACEHOLDER.DATE,
    PP_PLACEHOLDER.FOOTER,
    PP_PLACEHOLDER.SLIDE_NUMBER,
    PP_PLACEHOLDER.HEADER,
}


@dataclass
class Slide:
    number: int
    title: str = ""
    blocks: list[str] = field(default_factory=list)
    notes: str = ""
    hidden: bool = False


def extract(data: bytes) -> list[Slide]:
    try:
        deck = Presentation(io.BytesIO(data))
    except Exception as exc:  # zipfile, lxml and python-pptx raise many kinds
        raise ExtractError("not a readable .pptx (password-protected or damaged?)") from exc
    try:
        return _slides(deck)
    except Exception as exc:  # pragma: no cover - unusual files
        raise ExtractError(f"could not read the slides ({type(exc).__name__})") from exc


def _slides(deck) -> list[Slide]:
    slides: list[Slide] = []
    for number, s in enumerate(deck.slides, start=1):
        slide = Slide(number=number, hidden=s._element.get("show") == "0")
        for shape in _ordered(s.shapes):
            kind = _placeholder(shape)
            if kind in SKIP_PLACEHOLDERS:
                continue
            if kind in TITLE_PLACEHOLDERS and not slide.title:
                slide.title = clean(shape.text_frame.text)
                continue
            block = _block(shape, bulleted=kind in BODY_PLACEHOLDERS)
            if block:
                slide.blocks.append(block)
        if s.has_notes_slide and s.notes_slide.notes_text_frame is not None:
            slide.notes = s.notes_slide.notes_text_frame.text.strip()
        slides.append(slide)
    return slides


def _placeholder(shape):
    if not shape.is_placeholder:
        return None
    try:
        return shape.placeholder_format.type
    except ValueError:  # pragma: no cover - unknown placeholder
        return None


def _ordered(shapes):
    """Reading order: top to bottom, then left to right; groups are flattened."""
    flat = []
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            flat += _ordered(shape.shapes)
        else:
            flat.append(shape)
    return sorted(flat, key=lambda sh: (sh.top or 0, sh.left or 0))


def _block(shape, bulleted: bool) -> str:
    if getattr(shape, "has_table", False) and shape.has_table:
        return _table(shape.table)
    if not getattr(shape, "has_text_frame", False) or not shape.has_text_frame:
        return ""
    paragraphs = [(p.level, clean(_text(p))) for p in shape.text_frame.paragraphs]
    paragraphs = [(level, text) for level, text in paragraphs if text]
    if not paragraphs:
        return ""
    if bulleted or len(paragraphs) > 1:
        return "\n".join(f"{'  ' * level}- {escape(text)}" for level, text in paragraphs)
    return escape(paragraphs[0][1])


def _text(paragraph) -> str:
    return "".join(run.text for run in paragraph.runs) or paragraph.text


def _table(table) -> str:
    return md_table([[cell.text for cell in row.cells] for row in table.rows])


def slides_markdown(slides: list[Slide]) -> str:
    """One ``## Slide n · Title`` section per slide."""
    out: list[str] = []
    for slide in slides:
        heading = f"## Slide {slide.number}"
        if slide.title:
            heading += f" · {slide.title}"
        if slide.hidden:
            heading += " (hidden)"
        out += ["", heading]
        for block in slide.blocks:
            out += ["", block]
        if slide.notes:
            notes = slide.notes.splitlines()
            out += ["", "> **Notes:** " + notes[0]] + [f"> {line}" for line in notes[1:]]
    return "\n".join(out).strip() + "\n"
