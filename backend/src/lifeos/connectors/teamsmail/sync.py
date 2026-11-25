"""Teams messages from Gmail → one note per channel or chat in the knowledge hub.

For organisations that block third-party apps on Microsoft 365, two Power Automate flows
on the user's account send every new Teams channel and chat message as a mail. Each mail
has header lines (``Team:``, ``Kanal:``, ``Chat:``, ``Nachrichten-ID:`` …), a ``---``
separator, the message text and, after ``=== RAW ===``, the full Graph message as escaped
JSON in a ``<pre>`` block. This import reads those mails (read-only), keeps every message
once (key: conversation + message id) in ``inbox/teams/messages.json`` and renders one
note per conversation:

- channels: ``ready/knowledge/<target>/<Team>/Kanal <Channel>.md``
- chats:    ``ready/knowledge/<target>/Chats/<Topic or participants>.md``

Deleted messages and system events (members added …) are dropped. A note that is not our
mirror is never overwritten.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from lifeos.connectors.folder.sync import safe_name
from lifeos.connectors.google.gmail import body
from lifeos.core.extract import html_to_markdown
from lifeos.core.extract.text import dump_front, front_matter, link
from lifeos.core.vault import INBOX, KNOWLEDGE, STATE, Vault, write_atomic

KIND = "teams-messages"
QUERY = 'in:anywhere subject:Teams "Nachrichten-ID"'  # also archived, spam and trash
STORE = f"{INBOX}/teams/messages.json"
STATE_FILE = f"{STATE}/teams-mail/state.json"
OVERLAP_DAYS = 3  # mails are searched from the last run minus this, duplicates are skipped
FIRST_RUN_LIMIT = 5000
RUN_LIMIT = 1000
HEADER_FIELDS = {
    "team": "Team",
    "team_id": "Team-ID",
    "channel": "Kanal",
    "channel_id": "Kanal-ID",
    "chat": "Chat",
    "chat_id": "Chat-ID",
    "chat_type": "Chat-Typ",
    "id": "Nachrichten-ID",
    "from": "Von",
    "time": "Zeit",
    "subject": "Betreff",
    "link": "Link",
}


class MailApi(Protocol):
    def search(self, query: str, limit: int = 50) -> list[str]: ...

    def message(self, message_id: str) -> dict[str, Any]: ...


@dataclass
class TeamsMailReport:
    mails: int = 0  # new mails read
    messages: int = 0  # new or changed messages
    ignored: int = 0  # deleted messages, system events, unreadable mails
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated)

    def summary(self) -> str:
        notes = len(self.created) + len(self.updated)
        return f"{self.messages} new messages in {notes} conversations"


# -- parsing ------------------------------------------------------------------------


def _text_lines(fragment: str) -> list[str]:
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", fragment)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return [line.strip() for line in text.replace("\r", "").split("\n")]


def parse_mail(mail_html: str, mail_text: str = "") -> dict[str, Any] | None:
    """The message in a flow mail: header fields, text (Markdown) and the raw Graph JSON."""
    source = mail_html or html.escape(mail_text).replace("\n", "<br>")
    raw_at = source.find("=== RAW ===")
    head = source if raw_at < 0 else source[:raw_at]
    fields: dict[str, str] = {}
    text_lines: list[str] = []
    in_text = False
    for line in _text_lines(head):
        if in_text:
            text_lines.append(line)
            continue
        if line.startswith("---"):
            in_text = True
            continue
        key, sep, value = line.partition(":")
        if sep and key.strip() in HEADER_FIELDS.values():
            name = next(k for k, v in HEADER_FIELDS.items() if v == key.strip())
            fields.setdefault(name, value.strip())

    raw: dict[str, Any] = {}
    if raw_at >= 0:
        rest = source[raw_at:]
        start = rest.lower().find("<pre")
        end = rest.lower().find("</pre>")
        block = rest[rest.find(">", start) + 1 : end] if 0 <= start < end else rest[11:]
        block = html.unescape(re.sub(r"<[^>]+>", "", block)).strip()
        for candidate in (block, block.replace("\r", "").replace("\n", "")):
            try:
                raw = json.loads(candidate)
                break
            except ValueError:
                continue

    message_id = str(raw.get("id") or fields.get("id") or "")
    if not message_id:
        return None
    content = (raw.get("body") or {}).get("content")
    if content is not None:
        content = re.sub(r"<attachment[^>]*>\s*</attachment>", "", content)
        text = html_to_markdown(content) if content.strip() else ""
    else:
        text = "\n".join(text_lines).strip()
    sender = fields.get("from") or ""
    origin = raw.get("from") or {}
    for who in ("user", "application"):
        if not sender and isinstance(origin.get(who), dict):
            sender = origin[who].get("displayName") or ""
    meetings = [
        a.get("name") or "Besprechung"
        for a in raw.get("attachments", []) or []
        if a.get("contentType") == "meetingReference"
    ]
    files = [
        a.get("name") or "Datei"
        for a in raw.get("attachments", []) or []
        if a.get("contentType") == "reference"
    ]
    return {
        "id": message_id,
        "kind": "chat" if fields.get("chat_id") else "channel",
        "team": fields.get("team", ""),
        "team_id": fields.get("team_id", ""),
        "channel": fields.get("channel", ""),
        "channel_id": fields.get("channel_id", ""),
        "chat": fields.get("chat", ""),
        "chat_id": fields.get("chat_id", ""),
        "chat_type": fields.get("chat_type", ""),
        "from": sender,
        "from_id": (origin.get("user") or {}).get("id") if isinstance(origin, dict) else None,
        "time": raw.get("createdDateTime") or fields.get("time", ""),
        "modified": raw.get("lastModifiedDateTime") or raw.get("createdDateTime") or "",
        "subject": raw.get("subject") or fields.get("subject") or "",
        "link": raw.get("webUrl") or fields.get("link") or "",
        "text": text,
        "meetings": meetings,
        "files": files,
        "deleted": bool(raw.get("deletedDateTime")),
        "system": raw.get("messageType", "message") != "message",
    }


def conversation_key(msg: dict[str, Any]) -> str:
    if msg["kind"] == "chat":
        return f"chat:{msg['chat_id']}"
    return f"channel:{msg['team_id'] or msg['team']}:{msg['channel_id'] or msg['channel']}"


# -- import -------------------------------------------------------------------------


class TeamsMailImport:
    def __init__(
        self,
        vault: Vault,
        mail: Callable[[], MailApi] | None,
        target: str = "Teams",
        timezone: str = "UTC",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.vault = vault
        self._mail = mail
        self.target_dir = f"{KNOWLEDGE}/{target.strip('/')}"
        self.tz = ZoneInfo(timezone)
        self._clock = clock

    def _load(self, rel: str, default: dict[str, Any]) -> dict[str, Any]:
        path = self.vault.resolve(rel)
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def _save(self, rel: str, data: dict[str, Any]) -> None:
        write_atomic(self.vault.resolve(rel), json.dumps(data, indent=1, ensure_ascii=False))

    def run(self) -> TeamsMailReport:
        if self._mail is None:
            raise RuntimeError("Google is not set up")
        api = self._mail()
        report = TeamsMailReport()
        state = self._load(STATE_FILE, {"seen": [], "last_run": None})
        store = self._load(STORE, {"conversations": {}})
        conversations: dict[str, Any] = store["conversations"]
        seen = set(state.get("seen", []))

        query, limit = QUERY, FIRST_RUN_LIMIT
        if state.get("last_run"):
            since = datetime.fromisoformat(state["last_run"]) - timedelta(days=OVERLAP_DAYS)
            query, limit = f"{QUERY} after:{since:%Y/%m/%d}", RUN_LIMIT
        started = self._clock()
        touched: set[str] = set()
        for mail_id in api.search(query, limit):
            if mail_id in seen:
                continue
            try:
                mail = body(api.message(mail_id))
                msg = parse_mail(mail.html, mail.text)
            except Exception as exc:  # one broken mail must not stop the import
                report.errors.append(f"mail {mail_id}: {exc}")
                continue
            seen.add(mail_id)
            report.mails += 1
            if msg is None or msg["deleted"] or msg["system"]:
                report.ignored += 1
                continue
            key = conversation_key(msg)
            conv = conversations.setdefault(key, {"messages": {}})
            old = conv["messages"].get(msg["id"])
            if old and old.get("modified", "") >= msg["modified"]:
                continue
            conv["messages"][msg["id"]] = msg
            report.messages += 1
            touched.add(key)

        for key in sorted(touched):
            self._write_note(key, conversations[key], report)

        self._save(STORE, store)
        self._save(
            STATE_FILE,
            {"seen": sorted(seen), "last_run": started.isoformat(timespec="seconds")},
        )
        return report

    # -- notes ------------------------------------------------------------------

    def _note_rel(self, key: str, conv: dict[str, Any], latest: dict[str, Any]) -> str:
        if conv.get("note"):
            return conv["note"]
        if latest["kind"] == "chat":
            name = latest["chat"]
            if not name:
                people = sorted({m["from"] for m in conv["messages"].values() if m["from"]})
                name = "Chat mit " + ", ".join(people[:3]) if people else "Chat"
            short = hashlib.sha1(key.encode()).hexdigest()[:6]
            note = f"Chats/{safe_name(name)} ({short}).md"
        else:
            team = safe_name(latest["team"] or "Team")
            note = f"{team}/Kanal {safe_name(latest['channel'] or 'Kanal')}.md"
        conv["note"] = note
        return note

    def _write_note(self, key: str, conv: dict[str, Any], report: TeamsMailReport) -> None:
        messages = sorted(conv["messages"].values(), key=lambda m: m["time"])
        latest = messages[-1]
        note = self._note_rel(key, conv, latest)
        target = self.vault.resolve(note, zone=self.target_dir)
        rel = f"{self.target_dir}/{note}"
        if target.exists():
            meta = front_matter(target.read_text(encoding="utf-8"))
            if meta.get("type") != KIND or meta.get("conversation") != key:
                report.errors.append(f"{rel}: a different note already exists there")
                return
        content = self._render(key, latest, messages)
        existed = target.is_file()
        if existed and target.read_text(encoding="utf-8") == content:
            return
        write_atomic(target, content)
        (report.updated if existed else report.created).append(rel)

    def _local(self, iso: str) -> datetime | None:
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(self.tz)
        except ValueError:
            return None

    def _render(self, key: str, latest: dict[str, Any], messages: list[dict[str, Any]]) -> str:
        if latest["kind"] == "chat":
            people = sorted({m["from"] for m in messages if m["from"]})
            title = latest["chat"] or ("Chat mit " + ", ".join(people) if people else "Chat")
            front: dict[str, Any] = {
                "type": KIND,
                "source": "teams",
                "kind": "chat",
                "title": title,
                "chat_type": latest["chat_type"] or None,
                "participants": people,
            }
        else:
            title = f"{latest['team']} / {latest['channel']}"
            front = {
                "type": KIND,
                "source": "teams",
                "kind": "channel",
                "title": title,
                "team": latest["team"],
                "channel": latest["channel"],
            }
        front["conversation"] = key
        front["messages"] = len(messages)
        front["last_message"] = latest["time"]
        front["mirror"] = True
        notice = (
            "> Teams messages, collected by LifeOS from the Power Automate mails."
            " Changes here are overwritten on the next import."
        )
        out = [dump_front(front), f"# {title}", "", notice, ""]
        day = None
        for m in messages:
            when = self._local(m["time"])
            this_day = when.strftime("%Y-%m-%d") if when else "unbekannt"
            if this_day != day:
                out += [f"## {this_day}", ""]
                day = this_day
            stamp = when.strftime("%H:%M") if when else ""
            head = f"**{stamp} · {m['from'] or 'unbekannt'}**"
            if m["link"]:
                head += f" · [in Teams öffnen]({link(m['link'])})"
            out.append(head)
            if m["subject"]:
                out.append(f"**Betreff: {m['subject']}**")
            out.append("")
            if m["text"].strip():
                out += [m["text"].strip(), ""]
            for name in m["meetings"]:
                out += [f"*Besprechung geplant: {name}*", ""]
            for name in m["files"]:
                out += [f"*Datei: {name}*", ""]
        return "\n".join(out).rstrip() + "\n"


def report_dict(report: TeamsMailReport) -> dict[str, Any]:
    return {**asdict(report), "summary": report.summary()}
