"""Build the Gmail reader and the Teams mail import from the settings."""

from __future__ import annotations

from collections.abc import Callable

from lifeos.connectors.google.auth import GoogleAuth
from lifeos.connectors.google.gmail import GmailClient
from lifeos.connectors.teamsmail.sync import TeamsMailImport
from lifeos.core.config import Settings
from lifeos.core.vault import Vault


def gmail_factory(settings: Settings) -> Callable[[], GmailClient] | None:
    """A read-only Gmail client on the Google sign-in of the calendars, or None."""
    if not (settings.google_client_id and settings.google_client_secret and settings.state_dir):
        return None
    auth = GoogleAuth(settings.google_client_id, settings.google_client_secret, settings.state_dir)
    if not auth.connected:
        return None
    return lambda: GmailClient(auth.access_token)


def build_teams_mail(settings: Settings, vault: Vault) -> TeamsMailImport:
    return TeamsMailImport(vault, gmail_factory(settings), settings.teams_dir, settings.timezone)
