"""Build the calendar sync the same way for the web app and the worker."""

from __future__ import annotations

from lifeos.connectors.google.auth import GoogleAuth
from lifeos.connectors.google.client import GoogleCalendarClient
from lifeos.connectors.google.sync import CalendarSync
from lifeos.core.config import Settings
from lifeos.core.vault import Vault


def build_calendar(
    settings: Settings, vault: Vault
) -> tuple[CalendarSync, GoogleAuth | None, list[str]]:
    missing = [
        name
        for name, value in (
            ("LIFEOS_GOOGLE_CLIENT_ID", settings.google_client_id),
            ("LIFEOS_GOOGLE_CLIENT_SECRET", settings.google_client_secret),
            ("LIFEOS_STATE_DIR", settings.state_dir),
        )
        if not value
    ]
    auth = None
    if not missing:
        assert settings.google_client_id and settings.google_client_secret and settings.state_dir
        auth = GoogleAuth(
            settings.google_client_id, settings.google_client_secret, settings.state_dir
        )
    factory = (lambda: GoogleCalendarClient(auth.access_token)) if auth else None
    sync = CalendarSync(vault, factory, settings.timezone, settings.planning_dir)
    return sync, auth, missing
