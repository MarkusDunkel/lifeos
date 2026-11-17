"""Turn a draft into the cover letter and CV PDFs with Typst.

Templates and the font (XCharter, a free Bitstream Charter) ship with the app in
``lifeos/templates/typst``; system fonts are ignored so the result is the same on every
machine. The photo is copied next to the template for the compile run.
"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pypdf import PdfReader

from lifeos.modules.applications.model import Draft, german_date, resolve_cv
from lifeos.modules.applications.profile import Profile

TEMPLATES = Path(__file__).resolve().parents[2] / "templates" / "typst"
FONTS = TEMPLATES / "fonts"
MAX_PAGES = {"letter": 1, "cv": 2}


class RenderError(Exception):
    pass


@dataclass
class Rendered:
    letter: bytes
    cv: bytes
    pages: dict[str, int]


def render(draft: Draft, profile: Profile, on: date | None = None) -> Rendered:
    import typst  # imported lazily: only needed where PDFs are made

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in ("cv.typ", "letter.typ"):
            shutil.copy(TEMPLATES / name, root / name)
        photo = None
        if profile.photo is not None:
            photo = "photo" + profile.photo.suffix.lower()
            shutil.copy(profile.photo, root / photo)
        data = {
            "personal": profile.personal.model_dump(),
            "photo": photo,
            "date": german_date(on or date.today()),
            "letter": draft.letter.model_dump(),
            "cv": resolve_cv(draft.cv, profile.master),
        }
        inputs = {"data": json.dumps(data, ensure_ascii=False)}
        pdfs: dict[str, bytes] = {}
        for kind in ("letter", "cv"):
            try:
                pdfs[kind] = typst.compile(
                    str(root / f"{kind}.typ"),
                    root=str(root),
                    font_paths=[str(FONTS)],
                    ignore_system_fonts=True,
                    sys_inputs=inputs,
                )
            except Exception as exc:  # typst raises its own error types
                detail = getattr(exc, "diagnostic", None) or exc
                raise RenderError(f"{kind}: Typst failed: {detail}") from exc
    pages = {kind: len(PdfReader(io.BytesIO(pdf)).pages) for kind, pdf in pdfs.items()}
    too_long = [
        f"{'cover letter' if k == 'letter' else 'CV'} has {n} pages (max. {MAX_PAGES[k]})"
        for k, n in pages.items()
        if n > MAX_PAGES[k]
    ]
    if too_long:
        raise RenderError("; ".join(too_long))
    return Rendered(pdfs["letter"], pdfs["cv"], pages)
