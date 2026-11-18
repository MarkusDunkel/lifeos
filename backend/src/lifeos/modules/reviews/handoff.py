"""Feedback from the review page on its way to the agent, through the ticket.

"Send feedback" seals the open rounds of a package and posts one comment on the package's
ticket: a readable summary for people, and an invisible marker naming the rounds for the
agent. The comment has no agent marker, so it counts as the owner's feedback; an agent
that understands the review marker reads the sealed rounds from the vault (exact quotes,
line numbers) instead of the summary, and answers every item in ``response.md`` next to
the round (see :meth:`FeedbackRounds.respond`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from lifeos.core.vault import Vault
from lifeos.modules.workspaces.feedback import Item, Round
from lifeos.modules.workspaces.rounds import FeedbackError, FeedbackRounds

MARKER_RE = re.compile(r"<!--\s*lifeos-review:\s*(\{.*?\})\s*-->", re.S)
QUOTE_CHARS = 90


@dataclass
class SentRound:
    label: str  # the document's label in the package
    source: str  # vault-relative file the round is on
    round: Round


def marker(package: str, rounds: list[dict[str, Any]]) -> str:
    payload = json.dumps({"package": package, "rounds": rounds}, ensure_ascii=False)
    payload = payload.replace(">", "\\u003e")  # never close the HTML comment early
    return f"<!-- lifeos-review: {payload} -->"


def parse_marker(body: str) -> dict[str, Any] | None:
    match = MARKER_RE.search(body or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("rounds"), list):
        return None
    return data


def load_sent(vault: Vault, payload: dict[str, Any]) -> list[SentRound]:
    """The sealed rounds a review comment names; missing or unsealed ones are skipped."""
    rounds = FeedbackRounds(vault)
    out = []
    for entry in payload.get("rounds") or []:
        if not isinstance(entry, dict):
            continue
        source, round_id = str(entry.get("source", "")), str(entry.get("round", ""))
        try:
            r = rounds.sealed(source, round_id)
        except (FileNotFoundError, FeedbackError, ValueError):
            continue
        out.append(SentRound(str(entry.get("label") or source), source, r))
    return out


def short_quote(item: Item) -> str:
    text = " ".join(item.quote.split())
    return text if len(text) <= QUOTE_CHARS else text[: QUOTE_CHARS - 1].rstrip() + "…"


def summary(t: Any, rounds: list[SentRound], review_url: str | None) -> str:
    """The readable part of the ticket comment (``t``: the agent texts)."""
    count = sum(len(s.round.items) for s in rounds)
    lines = [t("review_sent_title", count=count)]
    for s in rounds:
        lines += ["", t("review_sent_document", label=s.label)]
        for item in s.round.items:
            feedback = "\n  ".join(item.feedback.strip().splitlines())
            lines.append(t("review_sent_item", id=item.id, quote=short_quote(item),
                           feedback=feedback))  # fmt: skip
    if review_url:
        lines += ["", t("review_sent_link", url=review_url)]
    return "\n".join(lines)
