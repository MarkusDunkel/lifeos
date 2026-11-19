"""Turn a draft into the cover letter and CV PDFs with Typst.

Templates and the font (XCharter, a free Bitstream Charter) ship with the app in
``lifeos/templates/typst``; system fonts are ignored so the result is the same on every
machine. The photo is copied next to the template for the compile run.

The CV is spread evenly over its pages: every vertical gap in the template is scaled by
``spread``, and the code raises it as far as the page count allows, so the last page is
filled instead of ending with empty space.
"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from pypdf import PdfReader

from lifeos.modules.applications.model import Draft, german_date, resolve_cv
from lifeos.modules.applications.profile import Profile

TEMPLATES = Path(__file__).resolve().parents[2] / "templates" / "typst"
FONTS = TEMPLATES / "fonts"
MAX_PAGES = {"letter": 1, "cv": 2}
MAX_SPREAD = 3.0  # at most three times the normal gaps
SPREAD_STEPS = 7  # bisection steps: precise to about 1 %
IMAGE_PPI = 80  # page images for the reviewer: readable, small


class RenderError(Exception):
    pass


@dataclass
class Rendered:
    letter: bytes
    cv: bytes
    pages: dict[str, int]
    spread: float = 1.0
    images: dict[str, list[bytes]] = field(default_factory=dict)  # PNG per page


def _pages(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages)


def render(
    draft: Draft, profile: Profile, on: date | None = None, images: bool = False
) -> Rendered:
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
        payload = json.dumps(data, ensure_ascii=False)

        def compile_(kind: str, spread: float = 1.0, fmt: str = "pdf") -> bytes | list[bytes]:
            try:
                return typst.compile(
                    str(root / f"{kind}.typ"),
                    root=str(root),
                    font_paths=[str(FONTS)],
                    ignore_system_fonts=True,
                    sys_inputs={"data": payload, "spread": f"{spread:.4f}"},
                    format=fmt,
                    **({"ppi": IMAGE_PPI} if fmt == "png" else {}),
                )
            except Exception as exc:  # typst raises its own error types
                detail = getattr(exc, "diagnostic", None) or exc
                raise RenderError(f"{kind}: Typst failed: {detail}") from exc

        pdfs: dict[str, bytes] = {kind: compile_(kind) for kind in ("letter", "cv")}
        pages = {kind: _pages(pdf) for kind, pdf in pdfs.items()}
        too_long = [
            f"{'cover letter' if k == 'letter' else 'CV'} has {n} pages (max. {MAX_PAGES[k]})"
            for k, n in pages.items()
            if n > MAX_PAGES[k]
        ]
        if too_long:
            raise RenderError("; ".join(too_long))

        spread = 1.0
        if draft.cv.layout.fill_pages:
            low, high = 1.0, MAX_SPREAD
            for _ in range(SPREAD_STEPS):
                mid = (low + high) / 2
                if _pages(compile_("cv", mid)) == pages["cv"]:
                    low = mid
                else:
                    high = mid
            if low > 1.0:
                spread = low
                pdfs["cv"] = compile_("cv", spread)
        out = Rendered(pdfs["letter"], pdfs["cv"], pages, spread)
        if images:

            def pngs(kind: str, spread: float = 1.0) -> list[bytes]:
                result = compile_(kind, spread, fmt="png")
                return result if isinstance(result, list) else [result]  # one page: bytes

            out.images = {"letter": pngs("letter"), "cv": pngs("cv", spread)}
        return out
