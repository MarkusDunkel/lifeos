from __future__ import annotations

import base64
import html
import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
from mcp.client.client import Client

from lifeos.connectors.google.gmail import body, headers
from lifeos.connectors.teamsmail.sync import TeamsMailImport, parse_mail
from lifeos.core.extract.text import front_matter
from lifeos.core.vault import Vault
from lifeos.mcp_server.server import build_mcp
from lifeos.modules.study.service import StudyService

KB = "ready/knowledge/Teams"
FOOTER = "<p>Example University GmbH<br>Imprint: https://example.invalid</p>"


def channel_raw(mid: str, text: str, **extra: Any) -> dict:
    return {
        "id": mid,
        "messageType": "message",
        "createdDateTime": "2026-11-25T13:56:59.801Z",
        "lastModifiedDateTime": "2026-11-25T13:56:59.801Z",
        "subject": "Hello",
        "webUrl": f"https://teams.example.invalid/l/message/{mid}",
        "from": {"user": {"id": "u1", "displayName": "Ann Example"}},
        "attachments": [],
        "body": {"contentType": "html", "content": f"<p>{text}</p>"},
        **extra,
    }


def flow_mail(header: dict[str, str], raw: dict) -> str:
    """Like the Power Automate mails: header lines, ---, content, RAW JSON in a pre block."""
    lines = "<br>".join(f"{k}: {v}" for k, v in header.items())
    escaped = html.escape(json.dumps(raw), quote=False)
    content = (raw.get("body") or {}).get("content", "")
    return (
        f"<html><body><div>{lines}<br>---<br>{content}<br>=== RAW ===<pre>{escaped}</pre>"
        f"</div>{FOOTER}</body></html>"
    )


def gmail_message(mid: str, mail_html: str, subject: str = "[Teams] X") -> dict:
    data = base64.urlsafe_b64encode(mail_html.encode()).decode().rstrip("=")
    return {
        "id": mid,
        "threadId": mid,
        "snippet": "snippet",
        "labelIds": ["Label_1"],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [{"name": "Subject", "value": subject}, {"name": "From", "value": "me"}],
            "parts": [
                {
                    "mimeType": "text/html",
                    "headers": [{"name": "Content-Type", "value": 'text/html; charset="UTF-8"'}],
                    "body": {"data": data},
                }
            ],
        },
    }


class FakeGmail:
    def __init__(self, mails: dict[str, dict]) -> None:
        self.mails = mails
        self.queries: list[str] = []

    def search(self, query: str, limit: int = 50) -> list[str]:
        self.queries.append(query)
        return list(self.mails)[:limit]

    def message(self, message_id: str) -> dict:
        return self.mails[message_id]


CHANNEL = {
    "Team": "Course A",
    "Team-ID": "t1",
    "Kanal": "General",
    "Kanal-ID": "c1",
    "Nachrichten-ID": "m1",
    "Von": "",
    "Zeit": "2026-11-25T13:56:59.801Z",
}


def test_gmail_helpers_decode_body_and_headers() -> None:
    message = gmail_message("g1", "<p>Grüße</p>")
    assert body(message).html == "<p>Grüße</p>"
    assert headers(message)["subject"] == "[Teams] X"


def test_parse_mail_reads_header_raw_and_meetings() -> None:
    raw = channel_raw(
        "m1",
        'Scheduled<attachment id="a1"></attachment>',
        attachments=[{"id": "a1", "contentType": "meetingReference", "name": "Exam BWL"}],
    )
    msg = parse_mail(flow_mail(CHANNEL, raw))
    assert msg is not None
    assert msg["kind"] == "channel"
    assert (msg["team"], msg["channel"], msg["id"]) == ("Course A", "General", "m1")
    assert msg["from"] == "Ann Example"  # header empty, taken from the raw JSON
    assert msg["meetings"] == ["Exam BWL"]
    assert "Scheduled" in msg["text"] and "attachment" not in msg["text"]
    assert "Imprint" not in msg["text"]


def test_import_writes_one_note_per_conversation(vault_root: Path) -> None:
    chat_header = {"Chat": "", "Chat-ID": "19:chat", "Chat-Typ": "oneOnOne", "Nachrichten-ID": "m3"}
    chat_raw = channel_raw("m3", "Hi there") | {"subject": None}
    mails = {
        "g1": gmail_message("g1", flow_mail(CHANNEL, channel_raw("m1", "First post"))),
        "g2": gmail_message("g2", flow_mail(CHANNEL, channel_raw("m1", "First post"))),  # dup
        "g3": gmail_message(
            "g3",
            flow_mail(
                CHANNEL | {"Nachrichten-ID": "m2"}, channel_raw("m2", "", deletedDateTime="x")
            ),
        ),
        "g4": gmail_message("g4", flow_mail(chat_header, chat_raw)),
    }
    gmail = FakeGmail(mails)
    importer = TeamsMailImport(
        Vault.open(vault_root), lambda: gmail, timezone="Europe/Vienna",
        clock=lambda: datetime(2026, 11, 25, 15, tzinfo=UTC),
    )  # fmt: skip

    report = importer.run()
    assert report.mails == 4
    assert report.messages == 2
    assert report.ignored == 1
    channel_note = vault_root / KB / "Course A" / "Kanal General.md"
    text = channel_note.read_text(encoding="utf-8")
    meta = front_matter(text)
    assert meta["type"] == "teams-messages" and meta["messages"] == 1
    assert "## 2026-11-25" in text and "14:56 · Ann Example" in text
    assert "First post" in text and "**Betreff: Hello**" in text
    chats = list((vault_root / KB / "Chats").glob("*.md"))
    assert len(chats) == 1 and chats[0].name.startswith("Chat mit Ann Example")

    again = importer.run()
    assert again.mails == 0 and not again.changed
    assert "after:2026/11/22" in gmail.queries[-1]


def test_foreign_note_is_kept(vault_root: Path) -> None:
    note = vault_root / KB / "Course A" / "Kanal General.md"
    note.parent.mkdir(parents=True)
    note.write_text("# mine\n", encoding="utf-8")
    gmail = FakeGmail({"g1": gmail_message("g1", flow_mail(CHANNEL, channel_raw("m1", "x")))})
    report = TeamsMailImport(Vault.open(vault_root), lambda: gmail).run()
    assert note.read_text(encoding="utf-8") == "# mine\n"
    assert report.errors


def test_mcp_mail_tools_are_read_only(vault_root: Path) -> None:
    gmail = FakeGmail({"g1": gmail_message("g1", "<p>Room <b>102</b></p>", "Exam room")})
    study = StudyService(Vault.open(vault_root), "UTC", rng=random.Random(0))
    server = build_mcp(study, lambda: gmail)
    no_mail = build_mcp(study, lambda: None)

    async def scenario() -> None:
        async with Client(server) as client:
            found = await client.call_tool("search_mail", {"query": "exam"})
            data = json.loads(found.content[0].text)
            assert data["mails"][0]["subject"] == "Exam room"
            read = json.loads(
                (await client.call_tool("read_mail", {"message_id": "g1"})).content[0].text
            )
            assert "Room **102**" in read["text"]
        async with Client(no_mail) as client:
            data = json.loads(
                (await client.call_tool("search_mail", {"query": "x"})).content[0].text
            )
            assert "not connected" in data["error"]

    anyio.run(scenario)


def test_gmail_client_backs_off_on_rate_limits() -> None:
    import httpx

    from lifeos.connectors.google.gmail import GmailClient

    answers = [
        httpx.Response(429, json={"error": {"message": "Quota exceeded"}}),
        httpx.Response(200, json={"messages": [{"id": "a"}]}),
    ]
    waits: list[float] = []
    client = GmailClient(
        lambda: "tok", httpx.MockTransport(lambda request: answers.pop(0)), sleep=waits.append
    )
    assert client.search("x") == ["a"]
    assert waits == [2.0]
