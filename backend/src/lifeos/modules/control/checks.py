"""Preconditions of the processes: is the connector connected, does the token carry the
permissions the process needs. Cheap file checks, run on every status request."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from lifeos.connectors.google.auth import GMAIL_SCOPE
from lifeos.connectors.google.auth import TOKEN_FILE as GOOGLE_TOKEN
from lifeos.connectors.moodle.sync import TOKEN_FILE as MOODLE_TOKEN
from lifeos.connectors.onedrive.auth import TOKEN_FILE as ONEDRIVE_TOKEN
from lifeos.core.config import Settings

DISK_WARN = 0.10  # share of free space below which the disk check warns
DISK_FAIL = 0.03


@dataclass
class Check:
    id: str
    label: str
    ok: bool | None  # None = cannot tell
    message: str
    fix: str = ""


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_checks(settings: Settings) -> dict[str, Check]:
    state = settings.state_dir
    checks: dict[str, Check] = {}

    google = _read_json(state / GOOGLE_TOKEN) if state else None
    if not (settings.google_client_id and settings.google_client_secret and state):
        checks["google"] = Check("google", "Google connected", False, "Google is not set up")
    elif google is None:
        checks["google"] = Check(
            "google", "Google connected", False, "not connected to Google",
            fix="Plan → Calendars → Connect Google",
        )  # fmt: skip
    else:
        checks["google"] = Check("google", "Google connected", True, "connected")
    scope = (google or {}).get("scope")
    if google is None:
        checks["gmail"] = Check("gmail", "Gmail permission", False, "not connected to Google")
    elif scope is None:
        checks["gmail"] = Check(
            "gmail", "Gmail permission", None,
            "unknown (token from before permissions were recorded)",
            fix="Connect Google again once to record the permissions",
        )  # fmt: skip
    elif GMAIL_SCOPE in str(scope).split():
        checks["gmail"] = Check("gmail", "Gmail permission", True, "granted")
    else:
        checks["gmail"] = Check(
            "gmail", "Gmail permission", False, "the Google token has no Gmail permission",
            fix="Plan → Calendars → connect Google again and allow mail access",
        )  # fmt: skip

    for cid, label, file, where in (
        ("moodle", "Moodle connected", MOODLE_TOKEN, "Moodle → Connect"),
        ("onedrive", "OneDrive connected", ONEDRIVE_TOKEN, "Knowledge → Presentations → Connect"),
    ):
        connected = bool(state and (state / file).is_file())
        checks[cid] = Check(
            cid, label, connected, "connected" if connected else "not connected",
            fix="" if connected else where,
        )  # fmt: skip

    checks["github"] = Check(
        "github", "GitHub token", bool(settings.github_token),
        "set" if settings.github_token else "LIFEOS_GITHUB_TOKEN not set",
    )  # fmt: skip
    return checks


def disk_check(path: Path) -> Check:
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        return Check("disk", "Disk space", None, f"cannot read: {exc}")
    free = usage.free / usage.total if usage.total else 1.0
    gib = usage.free / 2**30
    message = f"{gib:.1f} GiB free ({free:.0%})"
    if free < DISK_FAIL:
        return Check("disk", "Disk space", False, message, fix="free disk space on the server")
    if free < DISK_WARN:
        return Check("disk", "Disk space", None, message, fix="free disk space soon")
    return Check("disk", "Disk space", True, message)
