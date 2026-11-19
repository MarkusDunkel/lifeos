"""Load personal data and the master CV from the vault."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from lifeos.core.vault import KNOWLEDGE, Vault
from lifeos.modules.applications.model import MasterCv, Personal

CAREER = f"{KNOWLEDGE}/Career"
CV_DIR = f"{CAREER}/CV"
PERSONAL_FILE = f"{CV_DIR}/personal.yaml"
MASTER_FILE = f"{CV_DIR}/cv.yaml"
APPLICATIONS_DIR = f"{CAREER}/Applications"
EXAMPLES_DIR = f"{APPLICATIONS_DIR}/Examples"  # sent cover letters
EXAMPLE_CVS_DIR = f"{EXAMPLES_DIR}/CV"  # sent CVs with their selection
GUIDE_FILE = f"{CAREER}/Application-Guide.md"


class ProfileError(Exception):
    pass


@dataclass
class Profile:
    personal: Personal
    master: MasterCv
    photo: Path | None


def _yaml(vault: Vault, rel: str) -> dict:
    path = vault.resolve(rel)
    if not path.is_file():
        raise ProfileError(f"missing {rel} in the vault")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProfileError(f"{rel} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError(f"{rel} must contain a mapping")
    return data


def load_profile(vault: Vault) -> Profile:
    try:
        personal = Personal.model_validate(_yaml(vault, PERSONAL_FILE))
        master = MasterCv.model_validate(_yaml(vault, MASTER_FILE))
    except ValidationError as exc:
        raise ProfileError(f"invalid CV data: {exc}") from exc
    ids = [p.id for p in master.positions] + [e.id for e in master.education]
    bullets = [b.id for p in master.positions for b in p.bullets] + [
        b.id for e in master.education for b in e.bullets
    ]
    for kind, values in (("entry", ids), ("bullet", bullets)):
        dupes = {v for v in values if values.count(v) > 1}
        if dupes:
            raise ProfileError(f"duplicate {kind} ids in the master CV: {', '.join(sorted(dupes))}")
    photo = None
    if personal.photo:
        photo = vault.resolve(f"{CV_DIR}/{personal.photo}")
        if not photo.is_file():
            raise ProfileError(f"photo {personal.photo} not found in {CV_DIR}")
    return Profile(personal, master, photo)
