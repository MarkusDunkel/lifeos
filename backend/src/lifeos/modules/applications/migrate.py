"""One-off migration of the first (German) vault layout of the career data to English.

``ready/knowledge/Karriere/`` → ``ready/knowledge/Career/``:

- ``Lebenslauf/Stammdaten.yaml``, ``Lebenslauf.yaml``, photos → ``CV/personal.yaml``,
  ``CV/cv.yaml``, photos
- ``Bewerbungs-Leitfaden.md`` → ``Application-Guide.md``
- ``Bewerbungen/Beispiele/*`` → ``Applications/Examples/*``
- ``Bewerbungen/<application>/`` → ``Applications/<application>/`` with ``Bewerbung.md`` →
  ``Application.md`` and ``Inserat.md`` → ``Posting.md`` (English frontmatter keys)

Existing targets are never overwritten. Safe to run again.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from lifeos.core.extract.text import dump_front, front_matter
from lifeos.core.vault import KNOWLEDGE, Vault, write_atomic
from lifeos.modules.applications.profile import (
    APPLICATIONS_DIR,
    CAREER,
    CV_DIR,
    EXAMPLES_DIR,
    GUIDE_FILE,
)
from lifeos.modules.applications.service import INDEX_FILE

OLD = f"{KNOWLEDGE}/Karriere"
FILES = {
    f"{OLD}/Lebenslauf/Stammdaten.yaml": f"{CV_DIR}/personal.yaml",
    f"{OLD}/Lebenslauf/Lebenslauf.yaml": f"{CV_DIR}/cv.yaml",
    f"{OLD}/Bewerbungs-Leitfaden.md": GUIDE_FILE,
}
KEYS = {
    "firma": "company",
    "stelle": "position",
    "inserat": "posting",
    "erstellt": "created",
    "ort": "place",
    "quelle": "source",
    "importiert": "imported",
    "datum": "date",
}
TEXT = [
    ("# Bewerbung:", "# Application:"),
    ("Inserat: [[Inserat]]", "Posting: [[Posting]]"),
    ("## Verlauf", "## History"),
    ("zur Bewerbung ausgewählt (Spalte Neu)", "selected for an application (column New)"),
    ("- Gehalt: ab ", "- Salary: from "),
    (" € / Jahr", " € / year"),
    (" € / Monat", " € / month"),
    ("- Anstellung: ", "- Employment: "),
    ("- Technologien: ", "- Technologies: "),
]


def _move(vault: Vault, old: str, new: str, moved: list[tuple[str, str]]) -> None:
    src, dst = vault.resolve(old), vault.resolve(new)
    if not src.exists() or dst.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    moved.append((old, new))


def _rewrite(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    front = front_matter(text)
    if front:
        body = text[text.find("\n---\n", 4) + 5 :]
        text = dump_front({KEYS.get(k, k): v for k, v in front.items()}) + body
    for old, new in TEXT:
        text = text.replace(old, new)
    write_atomic(path, text)


def migrate_vault(vault: Vault) -> list[tuple[str, str]]:
    moved: list[tuple[str, str]] = []
    old_root = vault.resolve(OLD)
    if not old_root.is_dir():
        return moved
    for old, new in FILES.items():
        _move(vault, old, new, moved)
    photos = vault.resolve(f"{OLD}/Lebenslauf")
    for photo in sorted(photos.glob("*")) if photos.is_dir() else []:
        _move(vault, f"{OLD}/Lebenslauf/{photo.name}", f"{CV_DIR}/{photo.name}", moved)
    examples = vault.resolve(f"{OLD}/Bewerbungen/Beispiele")
    for example in sorted(examples.glob("*")) if examples.is_dir() else []:
        old = f"{OLD}/Bewerbungen/Beispiele/{example.name}"
        _move(vault, old, f"{EXAMPLES_DIR}/{example.name}", moved)
    applications = vault.resolve(f"{OLD}/Bewerbungen")
    renamed_folders: dict[str, str] = {}
    for folder in sorted(applications.glob("*")) if applications.is_dir() else []:
        if not folder.is_dir() or folder.name == "Beispiele":
            continue
        old, new = f"{OLD}/Bewerbungen/{folder.name}", f"{APPLICATIONS_DIR}/{folder.name}"
        _move(vault, old, new, moved)
        renamed_folders[old] = new
        target = vault.resolve(new)
        for before, after in (("Bewerbung.md", "Application.md"), ("Inserat.md", "Posting.md")):
            _move(vault, f"{new}/{before}", f"{new}/{after}", moved)
            if (target / after).is_file():
                _rewrite(target / after)
    for example in sorted(vault.resolve(EXAMPLES_DIR).glob("*.md")):
        _rewrite(example)
    index_path = vault.resolve(INDEX_FILE)
    if renamed_folders and index_path.is_file():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        for entry in index.values():
            entry["folder"] = renamed_folders.get(entry.get("folder"), entry.get("folder"))
        write_atomic(index_path, json.dumps(index, ensure_ascii=False, indent=1))
    # Remove what is left empty of the old layout.
    for path in sorted(old_root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    if old_root.is_dir() and not any(old_root.iterdir()):
        old_root.rmdir()
    return moved


def changed_paths() -> list[str]:
    """What a commit after the migration has to include."""
    return [OLD, CAREER]
