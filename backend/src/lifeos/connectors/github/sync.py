"""Ingest → merge → digest for GitHub tickets.

- ``sync``  fetches issues (and project fields) into ``inbox/github/`` and merges them
  into ``ready/tickets/<owner>/<repo>/<number>-<slug>.md`` (3-way, see ``model.merge``).
  The GitHub state seen last is the merge base in ``.lifeos/github/base/``.
- ``plan``  turns local changes into ``outbox/github/<ts>_plan.md`` for review.
- ``apply`` executes a reviewed plan, writes ``<ts>_result.md`` and syncs again.
  Each operation is skipped if its file or the issue on GitHub changed since the plan.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import secrets
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lifeos.connectors.github.client import (
    GitHubApi,
    GitHubError,
    Project,
    item_values,
    project_item,
    split_repo,
)
from lifeos.connectors.github.model import (
    Ticket,
    diff,
    dump,
    has_conflict_markers,
    load,
    merge,
    slugify,
)
from lifeos.core.vault import INBOX, OUTBOX, STATE, TICKETS, Vault, VaultPathError, write_atomic
from lifeos.modules.workspaces.feedback import fence_for

INBOX_DIR = f"{INBOX}/github"
OUTBOX_DIR = f"{OUTBOX}/github"
STATE_DIR = f"{STATE}/github"
PLAN_RE = re.compile(r"^(\d{8}T\d{6}Z-[0-9a-f]{4})_plan\.md$")
OPS_HEADING = "\n## Operations (machine-readable)\n\n"
OPS_BLOCK = re.compile(r"^(`{3,})json\n(.*)\n\1\n*$", re.S)


class SyncError(Exception):
    """An operation is not possible (HTTP ``status``)."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


@dataclass
class SyncReport:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    conflicts: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)  # local changes kept for write-back
    errors: list[str] = field(default_factory=list)
    synced_at: str = ""


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class GitHubConnector:
    def __init__(
        self,
        vault: Vault,
        api_factory: Callable[[], GitHubApi] | None,
        repos: list[str],
        project: str | None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.vault = vault
        self._api_factory = api_factory
        self.repos = repos
        self.project_ref = project
        self._now = now
        self._lock = threading.Lock()
        (vault.root / TICKETS).mkdir(parents=True, exist_ok=True)

    # -- configuration -----------------------------------------------------------

    @property
    def configured(self) -> bool:
        return self._api_factory is not None and bool(self.repos)

    def _api(self) -> GitHubApi:
        if self._api_factory is None:
            raise SyncError("GitHub is not configured: set LIFEOS_GITHUB_TOKEN", 400)
        if not self.repos:
            raise SyncError("No repositories configured: set LIFEOS_GITHUB_REPOS", 400)
        return self._api_factory()

    def _project(self, api: GitHubApi) -> Project | None:
        if not self.project_ref:
            return None
        owner, _, number = self.project_ref.partition("/")
        if not number.isdigit():
            raise SyncError("LIFEOS_GITHUB_PROJECT must look like owner/number", 400)
        return api.project(owner, int(number))

    def _state_file(self, name: str) -> Path:
        return self.vault.resolve(f"{STATE_DIR}/{name}")

    def meta(self) -> dict[str, Any]:
        """Cached project fields and labels from the last sync (for the editor)."""
        path = self._state_file("meta.json")
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        return {
            "configured": self.configured,
            "repos": self.repos,
            "project": self.project_ref,
            "fields": data.get("fields", {}),
            "labels": data.get("labels", {}),
            "assignees": data.get("assignees", {}),
            "synced_at": data.get("synced_at"),
        }

    # -- files -------------------------------------------------------------------

    def _repo_dir(self, repo: str) -> Path:
        owner, name = split_repo(repo)
        return self.vault.resolve(f"{owner}/{name}", zone=TICKETS)

    def ticket_path(self, rel: str) -> tuple[Path, str]:
        """Resolve ``owner/repo/file.md`` inside ``ready/tickets``; returns (path, repo)."""
        parts = rel.split("/")
        if len(parts) != 3 or not parts[2].endswith(".md"):
            raise VaultPathError(f"not a ticket path: {rel}")
        return self.vault.resolve(rel, zone=TICKETS), f"{parts[0]}/{parts[1]}"

    def files(self) -> list[tuple[str, Path, str]]:
        """All ticket files as (relative path, path, repo)."""
        root = self.vault.root / TICKETS
        out = []
        for path in sorted(root.glob("*/*/*.md")):
            if path.is_file() and not path.is_symlink():
                rel = path.relative_to(root).as_posix()
                out.append((rel, path, "/".join(rel.split("/")[:2])))
        return out

    def _find(self, repo: str, number: int) -> Path | None:
        d = self._repo_dir(repo)
        matches = sorted(d.glob(f"{number}-*.md")) + (
            [d / f"{number}.md"] if (d / f"{number}.md").exists() else []
        )
        return matches[0] if matches else None

    def _base_path(self, repo: str, number: int) -> Path:
        owner, name = split_repo(repo)
        return self.vault.resolve(f"{STATE_DIR}/base/{owner}/{name}/{number}.json")

    def base(self, repo: str, number: int) -> Ticket | None:
        path = self._base_path(repo, number)
        if not path.is_file():
            return None
        return Ticket.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def _save_base(self, t: Ticket) -> None:
        assert t.number is not None
        write_atomic(
            self._base_path(t.repo, t.number), json.dumps(t.to_dict(), indent=1, ensure_ascii=False)
        )

    def read(self, rel: str) -> Ticket:
        path, repo = self.ticket_path(rel)
        if not path.is_file():
            raise FileNotFoundError(rel)
        return load(path.read_text(encoding="utf-8"), repo)

    def changes(self, t: Ticket) -> dict[str, tuple[Any, Any]]:
        if t.number is None:
            return {"new": (None, t.title)}
        base = self.base(t.repo, t.number)
        return diff(base, t) if base else {}

    # -- sync --------------------------------------------------------------------

    def sync(self) -> SyncReport:
        with self._lock:
            return self._sync()

    def _sync(self) -> SyncReport:
        api = self._api()
        report = SyncReport(synced_at=now_iso())
        project = self._project(api)
        fields = project.fields if project else {}
        meta: dict[str, Any] = {
            "synced_at": report.synced_at,
            "fields": {n: {"type": f.type, "options": list(f.options)} for n, f in fields.items()},
            "labels": {},
            "assignees": {},
        }
        for repo in self.repos:
            try:
                repo_meta = api.repo(repo)
                raw = api.issues(repo)
            except GitHubError as exc:
                report.errors.append(f"{repo}: {exc}")
                continue
            meta["labels"][repo] = sorted(repo_meta.labels, key=str.casefold)
            meta["assignees"][repo] = sorted(repo_meta.assignees, key=str.casefold)
            owner, name = split_repo(repo)
            write_atomic(
                self.vault.resolve(f"{INBOX_DIR}/{owner}/{name}/issues.json"),
                json.dumps(raw, indent=1, ensure_ascii=False),
            )
            for issue in raw:
                remote = to_ticket(repo, issue, project)
                self._merge_one(remote, report)
        report.pending += [
            rel for rel, path, repo in self.files() if rel.split("/")[-1].startswith("new-")
        ]
        write_atomic(self._state_file("meta.json"), json.dumps(meta, indent=1, ensure_ascii=False))
        return report

    def _merge_one(self, remote: Ticket, report: SyncReport) -> None:
        assert remote.number is not None
        path = self._find(remote.repo, remote.number)
        if path is None:
            path = self._repo_dir(remote.repo) / f"{remote.number}-{slugify(remote.title)}.md"
            write_atomic(path, dump(remote))
            report.created += 1
        else:
            text = path.read_text(encoding="utf-8")
            local = load(text, remote.repo)
            result = merge(self.base(remote.repo, remote.number), local, remote)
            new_text = dump(result.ticket)
            if new_text != text:
                write_atomic(path, new_text)
                report.updated += 1
            else:
                report.unchanged += 1
            rel = path.relative_to(self.vault.root / TICKETS).as_posix()
            if result.conflicts:
                report.conflicts.append(rel)
            elif result.changed_locally:
                report.pending.append(rel)
        self._save_base(remote)

    # -- plan --------------------------------------------------------------------

    def plans(self) -> list[dict[str, Any]]:
        d = self.vault.resolve(OUTBOX_DIR)
        if not d.is_dir():
            return []
        out = []
        for p in sorted(d.glob("*_plan.md"), reverse=True):
            m = PLAN_RE.match(p.name)
            if not m:
                continue
            result = d / f"{m.group(1)}_result.md"
            out.append({"id": m.group(1), "status": "applied" if result.exists() else "pending"})
        return out

    def read_plan(self, plan_id: str) -> dict[str, Any]:
        plan, result = self._plan_paths(plan_id)
        if not plan.is_file():
            raise FileNotFoundError(f"plan {plan_id}")
        return {
            "id": plan_id,
            "status": "applied" if result.exists() else "pending",
            "plan": plan.read_text(encoding="utf-8"),
            "result": result.read_text(encoding="utf-8") if result.exists() else None,
        }

    def _plan_paths(self, plan_id: str) -> tuple[Path, Path]:
        if not re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{4}", plan_id):
            raise VaultPathError(f"invalid plan id: {plan_id}")
        return (
            self.vault.resolve(f"{OUTBOX_DIR}/{plan_id}_plan.md"),
            self.vault.resolve(f"{OUTBOX_DIR}/{plan_id}_result.md"),
        )

    def create_plan(self) -> dict[str, Any]:
        with self._lock:
            if any(p["status"] == "pending" for p in self.plans()):
                raise SyncError("apply or discard the open plan first")
            ops: list[dict[str, Any]] = []
            blockers: list[str] = []
            for rel, path, repo in self.files():
                text = path.read_text(encoding="utf-8")
                try:
                    t = load(text, repo)
                except ValueError as exc:
                    blockers.append(f"{rel}: {exc}")
                    continue
                if t.conflicts:
                    blockers.append(
                        f"{rel}: resolve the conflicts first ({', '.join(t.conflicts)})"
                    )
                    continue
                if has_conflict_markers(t.body):
                    blockers.append(f"{rel}: the text still contains conflict markers")
                    continue
                if not t.title:
                    blockers.append(f"{rel}: the title is empty")
                    continue
                if t.number is None:
                    ops.append(
                        {
                            "kind": "create",
                            "path": rel,
                            "repo": repo,
                            "file_sha": sha(text),
                            "ticket": t.editable(),
                        }
                    )
                    continue
                base = self.base(repo, t.number)
                if base is None:
                    blockers.append(f"{rel}: not synced yet, run a sync first")
                    continue
                changes = diff(base, t)
                if changes:
                    ops.append(
                        {
                            "kind": "update",
                            "path": rel,
                            "repo": repo,
                            "number": t.number,
                            "title": t.title,
                            "file_sha": sha(text),
                            "base_updated": base.updated,
                            "changes": {k: list(v) for k, v in changes.items()},
                        }
                    )
            if blockers:
                raise SyncError("cannot create a plan:\n" + "\n".join(blockers))
            if not ops:
                raise SyncError("there are no local changes to write back", 400)
            self._validate(ops)
            plan_id = f"{self._now():%Y%m%dT%H%M%SZ}-{secrets.token_hex(2)}"
            plan_path, _ = self._plan_paths(plan_id)
            write_atomic(plan_path, render_plan(plan_id, ops, self.project_ref))
            return self.read_plan(plan_id)

    def _validate(self, ops: list[dict[str, Any]]) -> None:
        """Check labels, assignees and project values against GitHub before the review."""
        meta = self.meta()
        problems = []
        for op in ops:
            ticket = op.get("ticket") or {k: v[1] for k, v in op.get("changes", {}).items()}
            known_labels = set(meta["labels"].get(op["repo"], []))
            for label in ticket.get("labels") or []:
                if known_labels and label not in known_labels:
                    problems.append(f"{op['path']}: label '{label}' does not exist in {op['repo']}")
            known_users = set(meta["assignees"].get(op["repo"], []))
            for user in ticket.get("assignees") or []:
                if known_users and user not in known_users:
                    problems.append(f"{op['path']}: '{user}' cannot be assigned in {op['repo']}")
            project = dict(ticket.get("project") or {})
            project |= {
                k.removeprefix("project."): v for k, v in ticket.items() if k.startswith("project.")
            }
            for name, value in project.items():
                f = meta["fields"].get(name)
                if f is None:
                    problems.append(f"{op['path']}: the project has no field '{name}'")
                elif (
                    value is not None and f["type"] == "SINGLE_SELECT" and value not in f["options"]
                ):
                    problems.append(f"{op['path']}: '{value}' is not an option of {name}")
        if problems:
            raise SyncError("cannot create a plan:\n" + "\n".join(problems))

    def discard_plan(self, plan_id: str) -> None:
        plan, result = self._plan_paths(plan_id)
        if result.exists():
            raise SyncError("an applied plan is kept as a record")
        if not plan.is_file():
            raise FileNotFoundError(f"plan {plan_id}")
        plan.unlink()

    # -- apply -------------------------------------------------------------------

    def apply(self, plan_id: str) -> dict[str, Any]:
        with self._lock:
            plan_path, result_path = self._plan_paths(plan_id)
            if not plan_path.is_file():
                raise FileNotFoundError(f"plan {plan_id}")
            if result_path.exists():
                raise SyncError("this plan was already applied")
            ops = parse_plan(plan_path.read_text(encoding="utf-8"))
            api = self._api()
            project = self._project(api)
            outcomes = []
            for op in ops:
                try:
                    outcomes.append((op, "done", self._apply_op(api, project, op)))
                except (GitHubError, SyncError) as exc:
                    outcomes.append((op, "skipped", str(exc)))
            write_atomic(result_path, render_result(plan_id, outcomes, now_iso()))
            report = self._sync()
            return {**self.read_plan(plan_id), "sync": asdict(report)}

    def _apply_op(self, api: GitHubApi, project: Project | None, op: dict[str, Any]) -> str:
        path, repo = self.ticket_path(op["path"])
        if not path.is_file() or sha(path.read_text(encoding="utf-8")) != op["file_sha"]:
            raise SyncError(
                "the file changed after the plan was created; "
                "the local changes are still pending, create a new plan"
            )
        repo_meta = api.repo(repo)

        if op["kind"] == "create":
            t = op["ticket"]
            created = api.create_issue(
                repo_meta.id,
                issue_input(
                    repo_meta,
                    {
                        "title": t["title"],
                        "body": t["body"],
                        "labels": t["labels"],
                        "assignees": t["assignees"],
                    },
                ),
            )
            if t["state"] == "closed":
                api.update_issue(created["id"], {"state": "CLOSED"})
            if project:
                item_id = api.add_to_project(project.id, created["id"])
                for name, value in (t.get("project") or {}).items():
                    api.set_field(project.id, item_id, project.fields[name], value)
            path.unlink()  # the next sync writes the canonical file for the new number
            return f"created {repo}#{created['number']}"

        issue = api.issue(repo, op["number"])
        if issue["updatedAt"] != op["base_updated"]:
            raise SyncError(
                "the issue changed on GitHub after the plan was created; "
                "sync, then create a new plan (the local changes are still pending)"
            )
        changes = {k: v[1] for k, v in op["changes"].items()}
        issue_changes = {k: v for k, v in changes.items() if not k.startswith("project.")}
        if issue_changes:
            api.update_issue(issue["id"], issue_input(repo_meta, issue_changes))
        field_changes = {
            k.removeprefix("project."): v for k, v in changes.items() if k.startswith("project.")
        }
        if field_changes:
            if project is None:
                raise SyncError("project fields changed but LIFEOS_GITHUB_PROJECT is not set")
            item = project_item(issue, project.id)
            item_id = item["id"] if item else api.add_to_project(project.id, issue["id"])
            for name, value in field_changes.items():
                if name not in project.fields:
                    raise SyncError(f"the project has no field '{name}' (anymore)")
                api.set_field(project.id, item_id, project.fields[name], value)
        return "updated " + ", ".join(op["changes"])


def issue_input(meta: Any, changes: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "title" in changes:
        out["title"] = changes["title"]
    if "body" in changes:
        out["body"] = changes["body"]
    if "state" in changes:
        out["state"] = "CLOSED" if changes["state"] == "closed" else "OPEN"
    if "labels" in changes:
        missing = [n for n in changes["labels"] if n not in meta.labels]
        if missing:
            raise GitHubError(f"unknown labels: {', '.join(missing)}")
        out["labelIds"] = [meta.labels[n] for n in changes["labels"]]
    if "assignees" in changes:
        missing = [n for n in changes["assignees"] if n not in meta.assignees]
        if missing:
            raise GitHubError(f"cannot assign: {', '.join(missing)}")
        out["assigneeIds"] = [meta.assignees[n] for n in changes["assignees"]]
    return out


def to_ticket(repo: str, issue: dict[str, Any], project: Project | None) -> Ticket:
    fields = project.fields if project else {}
    item = project_item(issue, project.id if project else None)
    return Ticket(
        repo=repo,
        number=issue["number"],
        title=issue["title"],
        state=issue["state"].lower(),
        labels=[n["name"] for n in issue["labels"]["nodes"]],
        assignees=[n["login"] for n in issue["assignees"]["nodes"]],
        project=item_values(item, fields),
        body=issue.get("body") or "",
        url=issue["url"],
        updated=issue["updatedAt"],
    ).normalised()


# -- plan files ----------------------------------------------------------------


def _show(value: Any) -> str:
    if value is None or value == "" or value == []:
        return "(empty)"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def render_plan(plan_id: str, ops: list[dict[str, Any]], project: str | None) -> str:
    lines = [
        f"# GitHub write-back plan {plan_id}",
        "",
        f"{len(ops)} operation(s). Nothing is written to GitHub until the plan is applied.",
        "Each operation is skipped if its file or the issue changed after this plan was made.",
    ]
    for op in ops:
        lines.append("")
        if op["kind"] == "create":
            t = op["ticket"]
            lines += [f"## Create in {op['repo']}: {t['title']}", "", f"- File: `{op['path']}`"]
            lines += [
                f"- State: {t['state']}",
                f"- Labels: {_show(t['labels'])}",
                f"- Assignees: {_show(t['assignees'])}",
            ]
            for name, value in (t.get("project") or {}).items():
                lines.append(f"- {name}: {_show(value)}")
            if t["body"]:
                fence = fence_for(t["body"])
                lines += ["", f"{fence}markdown", t["body"], fence]
            continue
        lines += [f"## Update {op['repo']}#{op['number']}: {op['title']}", ""]
        for name, (old, new) in op["changes"].items():
            if name == "body":
                continue
            label = name.removeprefix("project.")
            if isinstance(old, list) or isinstance(new, list):
                added = sorted(set(new or []) - set(old or []))
                removed = sorted(set(old or []) - set(new or []))
                parts = [f"+{a}" for a in added] + [f"-{r}" for r in removed]
                lines.append(f"- {label}: {' '.join(parts)}")
            else:
                lines.append(f"- {label}: {_show(old)} → {_show(new)}")
        if "body" in op["changes"]:
            old, new = op["changes"]["body"]
            udiff = "\n".join(
                difflib.unified_diff(
                    old.split("\n"), new.split("\n"), "github", "local", lineterm="", n=2
                )
            )
            fence = fence_for(udiff)
            lines += ["- Text:", "", f"{fence}diff", udiff, fence]
    data = json.dumps(ops, indent=1, ensure_ascii=False)
    fence = fence_for(data)
    lines += ["", "## Operations (machine-readable)", "", f"{fence}json", data, fence, ""]
    if project:
        lines.insert(3, f"Project: {project}.")
    return "\n".join(lines)


def parse_plan(text: str) -> list[dict[str, Any]]:
    """Operations from the last section, which the app always writes at the very end
    (issue text quoted earlier in the plan can never be mistaken for it)."""
    text = text.replace("\r\n", "\n")
    start = text.rfind(OPS_HEADING)
    m = OPS_BLOCK.match(text[start + len(OPS_HEADING) :]) if start >= 0 else None
    if not m:
        raise SyncError("the plan has no machine-readable operations", 400)
    return json.loads(m.group(2))


def render_result(plan_id: str, outcomes: list[tuple[dict[str, Any], str, str]], at: str) -> str:
    lines = [
        f"# Result of plan {plan_id}",
        "",
        f"Applied at {at}.",
        "",
        "| Ticket | Outcome | Details |",
        "| --- | --- | --- |",
    ]
    for op, status, detail in outcomes:
        name = (
            f"{op['repo']}#{op['number']}"
            if op["kind"] == "update"
            else f"new: {op['ticket']['title']}"
        )
        safe = detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {name.replace('|', '\\|')} | {status} | {safe} |")
    return "\n".join(lines) + "\n"
