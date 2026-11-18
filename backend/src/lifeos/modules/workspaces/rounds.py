"""Feedback rounds on any file of the vault, in the md4h ``feedback/v2`` format.

A round on ``<source>`` (a vault-relative path) lives in
``.md4h/feedback/<source>--<round>/feedback.md`` – the md4h convention, so workspace
reports, review packages and the editor extension share one layout. A round starts as a
draft and is sealed together with the SHA-256 of the source; a draft becomes stale
(read-only) as soon as the source changes on disk. Callers decide which sources may be
commented on; this class only checks that the source exists inside the vault.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from lifeos.core.vault import FEEDBACK, Vault, VaultPathError, write_atomic
from lifeos.modules.workspaces.feedback import HANDOFF_PROMPT, Item, Round, Scope, parse, render

ROUND_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{4}$")
MAX_TEXT = 20_000


class FeedbackError(Exception):
    """A feedback operation is not possible in the current state (HTTP ``status``)."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


@dataclass
class RoundInfo:
    round: str
    state: str
    items: int
    created_at: str
    sealed_at: str | None
    stale: bool  # the source changed since the round was started
    editable: bool  # a draft written by LifeOS that still matches the source


def round_payload(r: Round, stale: bool) -> dict:
    return {**asdict(r), "stale": stale, "editable": r.state == "draft" and not stale}


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FeedbackRounds:
    def __init__(self, vault: Vault, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.vault = vault
        self._now = now
        self._lock = threading.Lock()

    # -- paths ---------------------------------------------------------------------

    def _source(self, source: str) -> Path:
        path = self.vault.resolve(source)
        if not path.is_file():
            raise FileNotFoundError(source)
        return path

    def round_file(self, source: str, round_id: str) -> Path:
        if not ROUND_ID_RE.match(round_id):
            raise VaultPathError(f"invalid round id: {round_id}")
        return self.vault.resolve(f"{source}--{round_id}/feedback.md", zone=FEEDBACK)

    def feedback_rel(self, source: str, round_id: str) -> str:
        return f"{FEEDBACK}/{source}--{round_id}/feedback.md"

    # -- reading -------------------------------------------------------------------

    def rounds(self, source: str, current_sha: str | None = None) -> list[RoundInfo]:
        """The rounds on ``source``, newest first."""
        name = source.rsplit("/", 1)[-1]
        folder = source.rsplit("/", 1)[0] if "/" in source else ""
        base = self.vault.resolve(folder, zone=FEEDBACK) if folder else self.vault.resolve(FEEDBACK)
        if not base.is_dir():
            return []
        if current_sha is None:
            current_sha = sha256_of(self._source(source))
        out: list[RoundInfo] = []
        prefix = f"{name}--"
        for d in base.iterdir():
            round_id = d.name[len(prefix) :]
            if not d.name.startswith(prefix) or not ROUND_ID_RE.match(round_id):
                continue
            file = d / "feedback.md"
            if not file.is_file():
                continue
            text = file.read_text(encoding="utf-8", errors="replace")
            try:
                r = parse(text)
            except ValueError:
                continue
            stale = r.source_sha256 != current_sha
            out.append(
                RoundInfo(
                    round=round_id,
                    state=r.state,
                    items=len(r.items),
                    created_at=r.created_at,
                    sealed_at=r.sealed_at,
                    stale=stale,
                    editable=r.state == "draft" and not stale and _ours(text),
                )
            )
        out.sort(key=lambda i: i.round, reverse=True)
        return out

    def round(self, source: str, round_id: str) -> dict:
        path = self._source(source)
        file = self.round_file(source, round_id)
        if not file.is_file():
            raise FileNotFoundError(f"round {round_id}")
        text = file.read_text(encoding="utf-8")
        r = parse(text)
        payload = round_payload(r, stale=r.source_sha256 != sha256_of(path))
        payload["editable"] = payload["editable"] and _ours(text)
        return payload

    # -- writing -------------------------------------------------------------------

    def _open_draft(self, source: str, round_id: str) -> tuple[Path, Round, bytes]:
        """Load a draft for editing; refuses sealed, foreign and stale rounds."""
        path = self._source(source)
        file = self.round_file(source, round_id)
        if not file.is_file():
            raise FileNotFoundError(f"round {round_id}")
        text = file.read_text(encoding="utf-8")
        r = parse(text)
        if r.state != "draft":
            raise FeedbackError("this round is sealed and can no longer change")
        if not _ours(text):
            raise FeedbackError("this draft was started in the editor extension; continue it there")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != r.source_sha256:
            raise FeedbackError("the document changed since this round started; discard the draft")
        return file, r, raw

    def start(self, source: str) -> dict:
        """Resume the open LifeOS draft on ``source`` or start a new round."""
        with self._lock:
            raw = self._source(source).read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            for info in self.rounds(source, sha):
                if info.editable:
                    return self.round(source, info.round)
            now = self._now()
            round_id = f"{now:%Y%m%dT%H%M%SZ}-{secrets.token_hex(2)}"
            r = Round(
                round=round_id,
                source=source,
                source_sha256=sha,
                state="draft",
                created_at=_iso_ms(now),
                next_id="F1",
            )
            write_atomic(self.round_file(source, round_id), render(r))
            return round_payload(r, stale=False)

    def add_item(
        self,
        source: str,
        round_id: str,
        *,
        start_line: int,
        end_line: int,
        scope: Scope,
        feedback: str,
        quote: str | None = None,
    ) -> dict:
        _check_text("feedback", feedback)
        with self._lock:
            file, r, raw = self._open_draft(source, round_id)
            lines = raw.decode("utf-8").replace("\r\n", "\n").split("\n")
            if not 1 <= start_line <= end_line <= len(lines):
                raise FeedbackError(f"lines {start_line}-{end_line} are outside the document", 400)
            if scope == "blocks":
                quote = "\n".join(lines[start_line - 1 : end_line])
            else:
                _check_text("selected text", quote or "")
            assert quote is not None
            number = r.next_number
            r.items.append(
                Item(
                    id=f"F{number}",
                    start_line=start_line,
                    end_line=end_line,
                    scope=scope,
                    quote=quote,
                    feedback=feedback.strip("\n"),
                )
            )
            r.next_id = f"F{number + 1}"
            write_atomic(file, render(r))
            return round_payload(r, stale=False)

    def update_item(self, source: str, round_id: str, item_id: str, feedback: str) -> dict:
        _check_text("feedback", feedback)
        with self._lock:
            file, r, _ = self._open_draft(source, round_id)
            item = next((i for i in r.items if i.id == item_id), None)
            if item is None:
                raise FileNotFoundError(f"feedback {item_id}")
            item.feedback = feedback.strip("\n")
            write_atomic(file, render(r))
            return round_payload(r, stale=False)

    def delete_item(self, source: str, round_id: str, item_id: str) -> dict:
        with self._lock:
            file, r, _ = self._open_draft(source, round_id)
            r.items = [i for i in r.items if i.id != item_id]  # IDs are never reused (next_id)
            write_atomic(file, render(r))
            return round_payload(r, stale=False)

    def seal(self, source: str, round_id: str) -> dict:
        with self._lock:
            file, r, _ = self._open_draft(source, round_id)
            if not r.items:
                raise FeedbackError("add at least one comment before finishing", 400)
            r.state = "sealed"
            r.sealed_at = _iso_ms(self._now())
            write_atomic(file, render(r))
            feedback_file = self.feedback_rel(source, round_id)
            return {
                **round_payload(r, stale=False),
                "feedbackFile": feedback_file,
                "prompt": HANDOFF_PROMPT.format(feedback_file=feedback_file),
            }

    # -- answers of the agent that implemented a sealed round ----------------------------

    def sealed(self, source: str, round_id: str) -> Round:
        file = self.round_file(source, round_id)
        if not file.is_file():
            raise FileNotFoundError(f"round {round_id}")
        r = parse(file.read_text(encoding="utf-8"))
        if r.state != "sealed":
            raise FeedbackError(f"round {round_id} is not sealed")
        return r

    def response_file(self, source: str, round_id: str) -> Path:
        return self.round_file(source, round_id).with_name(RESPONSE_FILE)

    def respond(self, source: str, round_id: str, agent: str, answers: list[Answer]) -> str:
        """Write the agent's answer per feedback item next to the sealed round.

        md4h forbids changing ``feedback.md`` once sealed, so answers live in their own
        ``response.md`` in the round folder (replaced when the round is answered again).
        Returns the vault-relative path.
        """
        r = self.sealed(source, round_id)
        known = {i.id for i in r.items}
        file = self.response_file(source, round_id)
        write_atomic(file, render_response(r, agent, _iso_ms(self._now()),
                                           [a for a in answers if a.id in known]))  # fmt: skip
        return self.vault.relative(file)

    def response(self, source: str, round_id: str) -> list[Answer]:
        file = self.response_file(source, round_id)
        if not file.is_file():
            return []
        return parse_response(file.read_text(encoding="utf-8"))

    def discard(self, source: str, round_id: str) -> None:
        """Delete a LifeOS draft (also allowed when stale). Sealed rounds are never deleted."""
        with self._lock:
            file = self.round_file(source, round_id)
            if not file.is_file():
                raise FileNotFoundError(f"round {round_id}")
            text = file.read_text(encoding="utf-8")
            if parse(text).state != "draft" or not _ours(text):
                raise FeedbackError("only drafts started in LifeOS can be discarded here")
            file.unlink()
            if not any(file.parent.iterdir()):
                file.parent.rmdir()


RESPONSE_FILE = "response.md"
RESPONSE_SCHEMA = "lifeos-feedback-response/v1"
ANSWER_STATES = ("done", "declined")
ANSWER_RE = re.compile(r"^## (F\d+) · (done|declined)\s*$")


@dataclass
class Answer:
    id: str  # feedback item, e.g. "F2"
    status: str  # "done" | "declined"
    text: str  # what was changed, or why not


def render_response(r: Round, agent: str, answered_at: str, answers: list[Answer]) -> str:
    head = {
        "schema": RESPONSE_SCHEMA,
        "round": r.round,
        "source": r.source,
        "source_sha256": r.source_sha256,
        "agent": agent,
        "answered_at": answered_at,
    }
    front = yaml.safe_dump(head, sort_keys=False, allow_unicode=True).strip()
    lines = ["---", front, "---", "", f"# Answers to feedback round {r.round}", ""]
    for a in answers:
        status = a.status if a.status in ANSWER_STATES else "declined"
        lines += [f"## {a.id} · {status}", "", a.text.strip() or "-", ""]
    return "\n".join(lines)


def parse_response(text: str) -> list[Answer]:
    out: list[Answer] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        match = ANSWER_RE.match(line)
        if match:
            out.append(Answer(match.group(1), match.group(2), ""))
        elif out:
            out[-1].text += line + "\n"
    for a in out:
        a.text = a.text.strip()
    return out


def _ours(text: str) -> bool:
    """Rounds from the editor extension carry anchors LifeOS cannot rewrite faithfully."""
    return "md4h-target-v2:" not in text and "Asset SHA-256" not in text


def _iso_ms(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _check_text(what: str, text: str) -> None:
    if not text.strip():
        raise FeedbackError(f"{what} must not be empty", 400)
    if len(text) > MAX_TEXT:
        raise FeedbackError(f"{what} is too long (max {MAX_TEXT} characters)", 400)
