"""Agent settings the user changes in LifeOS: for now, how an agent's model calls are paid.

The agent definition in the vault stays the single source: a change edits its front
matter, adds a line to its history section (so the change explains itself months later)
and is committed like any other vault edit.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from lifeos.agents.comments import Texts
from lifeos.agents.config import (
    AGENTS_DIR,
    BILLING,
    AgentDef,
    ConfigError,
    load_config,
    parse_agent,
)
from lifeos.core.vault import Vault, revision_of, write_checked

log = logging.getLogger("lifeos.agents")
Committer = Callable[[list[str], str], None]
Quotas = Callable[[], dict[str, dict[str, Any]]]  # agent → processes, runs today, limit


class AgentAdminError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def with_billing(text: str, billing: str) -> str:
    """``text`` with ``billing:`` in its front matter set to ``billing``."""
    if not text.startswith("---\n"):
        raise AgentAdminError("the agent file has no front matter", 422)
    end = text.find("\n---\n", 4)
    if end < 0:
        raise AgentAdminError("the agent file's front matter is not closed", 422)
    front, rest = text[: end + 1], text[end + 1 :]
    line = f"billing: {billing}"
    if re.search(r"^billing:", front, flags=re.M):
        front = re.sub(r"^billing:.*$", line, front, count=1, flags=re.M)
    else:
        front += line + "\n"
    return front + rest


def with_history(text: str, heading: str, entry: str) -> str:
    """``text`` with ``- entry`` appended to its ``## heading`` section (created if missing)."""
    match = re.search(rf"^## {re.escape(heading)}[ \t]*$", text, flags=re.M)
    if match is None:
        return text.rstrip("\n") + f"\n\n## {heading}\n\n- {entry}\n"
    following = re.search(r"^## ", text[match.end() :], flags=re.M)
    end = match.end() + following.start() if following else len(text)
    section = text[match.end() : end].rstrip("\n")
    separator = "\n" if section.strip() else "\n\n"
    after = "\n\n" + text[end:] if following else "\n"
    return text[: match.end()] + section + separator + f"- {entry}" + after


class AgentAdmin:
    def __init__(
        self, vault: Vault, committer: Committer | None = None, quotas: Quotas | None = None,
        timezone: str = "UTC",
    ) -> None:  # fmt: skip
        self.tz = ZoneInfo(timezone)
        self.vault = vault
        self.committer = committer
        self.quotas = quotas

    def _quotas(self) -> dict[str, dict[str, Any]]:
        if self.quotas is None:
            return {}
        try:
            return self.quotas()
        except Exception:  # the page works without the counters
            log.exception("could not read the agents' run counters")
            return {}

    def _summary(
        self, agent: AgentDef, boards: dict[str, Any], quota: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        board = boards.get(agent.board or "")
        return {
            "name": agent.name,
            "title": agent.title,
            "board": agent.board,
            "boardTitle": board.title if board else None,
            "model": agent.model,
            "billing": agent.billing,
            "path": agent.path,
            "revision": revision_of(self.vault.resolve(agent.path)),
            "quota": quota,
        }

    def overview(self) -> dict[str, Any]:
        cfg = load_config(self.vault)
        quotas = self._quotas()
        return {
            "agents": [self._summary(a, cfg.boards, quotas.get(a.name)) for a in sorted(
                cfg.agents.values(), key=lambda a: a.title.lower())],
            "billingOptions": list(BILLING),
            "errors": cfg.errors,
        }  # fmt: skip

    def set_billing(self, name: str, billing: str, revision: str | None) -> dict[str, Any]:
        if billing not in BILLING:
            raise AgentAdminError(f"billing must be one of {', '.join(BILLING)}", 422)
        cfg = load_config(self.vault)
        agent = cfg.agents.get(name)
        if agent is None:
            raise AgentAdminError(f"unknown agent: {name}", 404)
        if agent.billing == billing:
            return self._summary(agent, cfg.boards)
        path = self.vault.resolve(f"{name}.md", zone=AGENTS_DIR)
        raw = path.read_bytes().decode("utf-8")  # read_text would hide CRLF line endings
        crlf = "\r\n" in raw
        text = raw.replace("\r\n", "\n")
        t = Texts.load(self.vault)
        entry = t(
            "history_billing",
            date=datetime.now(self.tz).date().isoformat(),  # the owner's day, not the server's
            way=t(f"billing_{billing}"),
            previous=t(f"billing_{agent.billing}"),
        )
        text = with_history(with_billing(text, billing), t("history_heading"), entry)
        try:
            parse_agent(text, agent.path)
        except ConfigError as exc:  # never write a definition the worker cannot read
            raise AgentAdminError(str(exc), 422) from exc
        write_checked(path, text.replace("\n", "\r\n") if crlf else text, revision)
        if self.committer:
            self.committer([agent.path], f"agents: {name} billing {agent.billing} → {billing}")
        updated = load_config(self.vault).agents[name]
        return self._summary(updated, cfg.boards)
