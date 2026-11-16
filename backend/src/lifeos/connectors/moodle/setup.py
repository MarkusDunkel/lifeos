"""Build the Moodle sync from the settings (shared by the web app, CLI and worker)."""

from __future__ import annotations

from lifeos.connectors.moodle.client import MoodleClient
from lifeos.connectors.moodle.sync import MoodleAccess, MoodleSync
from lifeos.core.config import Settings
from lifeos.core.vault import Vault


def build_moodle(settings: Settings, vault: Vault) -> tuple[MoodleSync, list[str]]:
    """Return the sync and the settings still missing before Moodle can be used."""
    missing = [
        name
        for name, value in (
            ("LIFEOS_MOODLE_URL", settings.moodle_url),
            ("LIFEOS_STATE_DIR", settings.state_dir),
        )
        if not value
    ]
    access = None
    if settings.moodle_url and settings.state_dir:
        access = MoodleAccess(settings.moodle_url, settings.state_dir, MoodleClient)
    sync = MoodleSync(vault, access, settings.moodle_dir, timezone=settings.timezone)
    return sync, missing
