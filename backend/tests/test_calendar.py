from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from lifeos.connectors.google.auth import GoogleAuth, GoogleError
from lifeos.connectors.google.sync import EVENTS_FILE, MIRROR, CalendarSync
from lifeos.core.config import Settings
from lifeos.core.vault import Vault

NOW = datetime(2026, 11, 23, 18, 0, tzinfo=UTC).timestamp()


class FakeCalendar:
    """In-memory Google Calendar with the calls LifeOS uses."""

    def __init__(self) -> None:
        self.cals: dict[str, dict[str, Any]] = {"primary": {"id": "primary", "summary": "Me"}}
        self.items: dict[str, dict[str, dict[str, Any]]] = {"primary": {}}
        self.calls: list[str] = []
        self._n = 0

    def add(self, cal: str, summary: str, start: str, end: str, **extra: Any) -> str:
        self._n += 1
        eid = f"e{self._n}"
        self.items.setdefault(cal, {})[eid] = {
            "id": eid,
            "summary": summary,
            "start": {"dateTime": start},
            "end": {"dateTime": end},
            **extra,
        }
        return eid

    def calendars(self) -> list[dict[str, Any]]:
        return list(self.cals.values())

    def events(self, calendar_id, time_min, time_max, private_property=None):
        return [dict(e) for e in self.items.get(calendar_id, {}).values()]

    def create_calendar(self, summary: str, timezone: str) -> str:
        self._n += 1
        cid = f"cal{self._n}@group"
        self.cals[cid] = {"id": cid, "summary": summary}
        self.items[cid] = {}
        self.calls.append(f"create {summary}")
        return cid

    def insert_event(self, calendar_id, body):
        self._n += 1
        eid = f"e{self._n}"
        self.items[calendar_id][eid] = {"id": eid, **body}
        self.calls.append(f"insert {body['summary']}")
        return self.items[calendar_id][eid]

    def patch_event(self, calendar_id, event_id, body):
        self.items[calendar_id][event_id].update(body)
        self.calls.append(f"patch {body['summary']}")
        return self.items[calendar_id][event_id]

    def delete_event(self, calendar_id, event_id):
        item = self.items[calendar_id].pop(event_id)
        self.calls.append(f"delete {item['summary']}")


@pytest.fixture
def fake() -> FakeCalendar:
    return FakeCalendar()


@pytest.fixture
def sync(vault_root: Path, fake: FakeCalendar) -> CalendarSync:
    s = CalendarSync(Vault.open(vault_root), lambda: fake, "Europe/Vienna", clock=lambda: NOW)
    s.set_inputs([{"id": "primary", "label": "Privat"}])
    return s


def target_events(fake: FakeCalendar, sync: CalendarSync) -> list[dict[str, Any]]:
    return list(fake.items[sync.config()["target"]].values())


def test_first_sync_creates_target_and_mirrors(sync, fake):
    fake.add("primary", "Dentist", "2026-11-24T09:00:00+01:00", "2026-11-24T10:00:00+01:00")
    report = sync.sync()
    assert not report.errors
    assert fake.calls[0] == "create LifeOS"
    assert report.created == 1
    [mirror] = target_events(fake, sync)
    assert mirror["summary"] == "Dentist"
    assert mirror["extendedProperties"]["private"]["lifeos_kind"] == MIRROR
    assert "Privat" in mirror["description"]


def test_second_sync_is_a_no_op_and_follows_changes(sync, fake):
    eid = fake.add("primary", "Dentist", "2026-11-24T09:00:00+01:00", "2026-11-24T10:00:00+01:00")
    sync.sync()
    fake.calls.clear()
    assert sync.sync().created == 0 and fake.calls == []

    fake.items["primary"][eid]["start"] = {"dateTime": "2026-11-24T11:00:00+01:00"}
    report = sync.sync()
    assert report.updated == 1
    assert target_events(fake, sync)[0]["start"]["dateTime"].startswith("2026-11-24T11")

    del fake.items["primary"][eid]
    assert sync.sync().deleted == 1
    assert target_events(fake, sync) == []


def test_manual_events_in_target_are_kept_and_reported(sync, fake):
    sync.sync()
    target = sync.config()["target"]
    fake.add(target, "Gym with Anna", "2026-11-25T18:00:00+01:00", "2026-11-25T19:00:00+01:00")
    report = sync.sync()
    assert report.manual == 1 and report.deleted == 0
    assert "delete Gym with Anna" not in fake.calls
    store = json.loads((sync.vault.resolve(EVENTS_FILE)).read_text(encoding="utf-8"))
    assert [e["title"] for e in store["manual"]] == ["Gym with Anna"]


def test_declined_and_cancelled_events_are_skipped(sync, fake):
    fake.add(
        "primary", "Declined", "2026-11-24T09:00:00+01:00", "2026-11-24T10:00:00+01:00",
        attendees=[{"self": True, "responseStatus": "declined"}],
    )  # fmt: skip
    fake.add(
        "primary", "Gone", "2026-11-24T11:00:00+01:00", "2026-11-24T12:00:00+01:00",
        status="cancelled",
    )  # fmt: skip
    assert sync.sync().events == 0


def test_target_is_never_an_input(sync, fake):
    sync.sync()
    target = sync.config()["target"]
    cfg = sync.set_inputs([{"id": "primary", "label": "P"}, {"id": target, "label": "T"}])
    assert [i["id"] for i in cfg["inputs"]] == ["primary"]


def test_lost_target_is_recreated(sync, fake):
    sync.sync()
    first = sync.config()["target"]
    del fake.cals[first]
    sync.sync()
    assert sync.config()["target"] not in (first, None)


# -- OAuth ---------------------------------------------------------------------------


def _id_token(email: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
    return f"h.{payload}.s"


def _auth(tmp_path: Path, answers: list[httpx.Response]) -> tuple[GoogleAuth, list]:
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(parse_qs(request.content.decode()))
        return answers.pop(0)

    auth = GoogleAuth("cid", "secret", tmp_path, httpx.MockTransport(handler), lambda: NOW)
    return auth, seen


def test_oauth_flow_stores_tokens_and_refreshes(tmp_path):
    scope = (
        "openid email https://www.googleapis.com/auth/calendar.calendarlist.readonly "
        "https://www.googleapis.com/auth/calendar.events.readonly "
        "https://www.googleapis.com/auth/calendar.app.created"
    )
    auth, seen = _auth(
        tmp_path,
        [
            httpx.Response(
                200,
                json={
                    "access_token": "a1",
                    "refresh_token": "r1",
                    "expires_in": 3600,
                    "scope": scope,
                    "id_token": _id_token("me@example.org"),
                },
            ),  # fmt: skip
        ],
    )
    url = auth.authorize_url("https://host/api/calendar/google/callback")
    state = parse_qs(urlparse(url).query)["state"][0]
    assert "calendar.app.created" in url and "access_type=offline" in url
    assert auth.complete("CODE", state) == "me@example.org"
    assert seen[0]["code"] == ["CODE"]
    assert auth.access_token() == "a1"
    assert auth.account() == "me@example.org"


def test_oauth_rejects_foreign_state(tmp_path):
    auth, _ = _auth(tmp_path, [])
    auth.authorize_url("https://host/cb")
    with pytest.raises(GoogleError):
        auth.complete("CODE", "forged")
    assert not auth.connected


def test_oauth_requires_all_calendar_scopes(tmp_path):
    auth, _ = _auth(
        tmp_path,
        [httpx.Response(200, json={"access_token": "a", "refresh_token": "r", "scope": "openid"})],
    )
    url = auth.authorize_url("https://host/cb")
    state = parse_qs(urlparse(url).query)["state"][0]
    with pytest.raises(GoogleError, match="missing"):
        auth.complete("CODE", state)
    assert not auth.connected


# -- API -------------------------------------------------------------------------------


def test_status_without_google_settings(client):
    data = client.get("/api/calendar/status").json()
    assert data["configured"] is False
    assert "LIFEOS_GOOGLE_CLIENT_ID" in data["missing"]
    assert client.post("/api/calendar/sync").status_code == 400


def test_connect_returns_google_url(settings: Settings, tmp_path):
    from conftest import PASSWORD
    from fastapi.testclient import TestClient

    from lifeos.main import create_app

    state = tmp_path / "state"
    state.mkdir()
    s = settings.model_copy(
        update={"google_client_id": "cid", "google_client_secret": "sec", "state_dir": state}
    )
    c = TestClient(create_app(s))
    c.post("/api/auth/login", json={"password": PASSWORD})
    url = c.post("/api/calendar/google/connect").json()["url"]
    assert url.startswith("https://accounts.google.com/")
    assert "redirect_uri=http%3A%2F%2Ftestserver%2Fapi%2Fcalendar%2Fgoogle%2Fcallback" in url
    r = c.get("/api/calendar/google/callback?state=x&code=y", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/plan?")
