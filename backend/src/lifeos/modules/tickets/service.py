"""Tickets in the app: list and edit the mirrored issue files in ``ready/tickets``."""

from __future__ import annotations

import secrets
from typing import Any, Literal

from lifeos.connectors.github.client import split_repo
from lifeos.connectors.github.model import Ticket, dump, has_conflict_markers, load, slugify
from lifeos.connectors.github.sync import GitHubConnector, SyncError
from lifeos.core.vault import TICKETS, revision_of, write_checked


def _summary(rel: str, t: Ticket, changes: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": rel,
        "repo": t.repo,
        "number": t.number,
        "title": t.title,
        "state": t.state,
        "labels": t.labels,
        "assignees": t.assignees,
        "project": {k: v for k, v in t.project.items() if v is not None},
        "url": t.url,
        "updated": t.updated,
        "conflicts": t.conflicts,
        "changes": sorted(changes),
    }


class TicketService:
    def __init__(self, connector: GitHubConnector):
        self.gh = connector
        self.vault = connector.vault

    def list(self) -> dict[str, Any]:
        tickets, broken = [], []
        for rel, path, repo in self.gh.files():
            try:
                t = load(path.read_text(encoding="utf-8"), repo)
            except (ValueError, OSError) as exc:
                broken.append({"path": rel, "error": str(exc)})
                continue
            tickets.append(_summary(rel, t, self.gh.changes(t)))
        return {
            "meta": self.gh.meta(),
            "tickets": tickets,
            "broken": broken,
            "plans": self.gh.plans(),
        }

    def read(self, rel: str) -> dict[str, Any]:
        path, _ = self.gh.ticket_path(rel)
        t = self.gh.read(rel)
        base = self.gh.base(t.repo, t.number) if t.number is not None else None
        return {
            **_summary(rel, t, self.gh.changes(t)),
            "body": t.body,
            "revision": revision_of(path),
            "base": base.to_dict() if base else None,
        }

    def save(self, rel: str, fields: dict[str, Any], revision: str | None) -> dict[str, Any]:
        path, _ = self.gh.ticket_path(rel)
        current = self.gh.read(rel)
        updated = Ticket(
            repo=current.repo,
            number=current.number,
            title=str(fields.get("title", current.title)),
            state=str(fields.get("state", current.state)),
            labels=list(fields.get("labels", current.labels)),
            assignees=list(fields.get("assignees", current.assignees)),
            project=dict(fields.get("project", current.project)),
            body=str(fields.get("body", current.body)),
            url=current.url,
            updated=current.updated,
            conflicts=current.conflicts,
        )
        if updated.state not in ("open", "closed"):
            raise SyncError("state must be open or closed", 400)
        write_checked(path, dump(updated), revision)
        return self.read(rel)

    def create(self, repo: str, title: str) -> dict[str, Any]:
        owner, name = split_repo(repo)
        if repo not in self.gh.repos and not (self.vault.root / TICKETS / owner / name).is_dir():
            raise SyncError(f"{repo} is not a mirrored repository", 400)
        if not title.strip():
            raise SyncError("the title must not be empty", 400)
        rel = f"{owner}/{name}/new-{slugify(title)}-{secrets.token_hex(2)}.md"
        path, _ = self.gh.ticket_path(rel)
        write_checked(path, dump(Ticket(repo=repo, number=None, title=title.strip())), None)
        return self.read(rel)

    def delete(self, rel: str, revision: str) -> None:
        path, _ = self.gh.ticket_path(rel)
        if self.gh.read(rel).number is not None:
            raise SyncError("only tickets that are not on GitHub yet can be deleted here", 400)
        if revision_of(path) != revision:
            raise SyncError("the ticket changed in the meantime; reload it")
        path.unlink()

    def resolve(
        self, rel: str, field: str, choice: Literal["local", "remote"], revision: str
    ) -> dict[str, Any]:
        path, _ = self.gh.ticket_path(rel)
        t = self.gh.read(rel)
        if field not in t.conflicts:
            raise SyncError(f"'{field}' has no conflict", 400)
        if choice == "remote":
            base = self.gh.base(t.repo, t.number) if t.number is not None else None
            if base is None:
                raise SyncError("no GitHub version is known for this ticket; run a sync", 400)
            if field.startswith("project."):
                t.project[field.removeprefix("project.")] = base.project.get(
                    field.removeprefix("project.")
                )
            else:
                setattr(t, field, getattr(base, field))
        elif field == "body" and has_conflict_markers(t.body):
            raise SyncError("remove the conflict markers from the text first", 400)
        t.conflicts = [c for c in t.conflicts if c != field]
        write_checked(path, dump(t), revision)
        return self.read(rel)
