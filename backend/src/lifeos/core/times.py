"""Reading timestamps from state files: they are written by several processes (and now and
then by hand), so a broken value must never stop the reader."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def parse_iso(value: Any) -> datetime | None:
    """An aware datetime from an ISO string, or None when it is missing or unreadable.
    A time without an offset counts as UTC."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
