from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from lifeos.agents import comments
from lifeos.agents.config import ConfigError, load_config, parse_contract
from lifeos.agents.service import run_round
from lifeos.agents.team import AgentBlocked, RunContext, RunResult, Team
from lifeos.connectors.github.client import Project, ProjectField, RepoMeta
from lifeos.core.git_sync import github_web_url
from lifeos.core.vault import Vault

TASKS = """---
board: tasks
title: Team Tasks
project: someone/3
inbox: New
ai:
  pickup: Todo
  working: In Progress
  review: Review
  responsible_field: Responsible
columns:
  - {name: New, owner: Owner, description: "Inbox."}
  - {name: Todo, owner: responsible, description: "Approved."}
  - {name: In Progress, owner: responsible, description: "Being worked on."}
  - {name: Review, owner: Owner, description: "Waiting for the owner."}
  - {name: Done, owner: Owner, description: "Done."}
fields:
  - {name: Responsible, type: single_select, options: [Owner], options_from: agents}
  - {name: AI, type: single_select, options: [running, blocked]}
---

# Board

Text for humans.

## README (mirrored)

Tasks of the owner and the AI.

## Other section

not in the README
"""

APPS = """---
board: apps
title: Applications
project: someone/4
ai: {pickup: Active, working: Active, review: In Review}
columns:
  - {name: New, owner: Owner, description: "New."}
  - {name: Active, owner: ai, agent: writer, description: "AI writes."}
  - {name: In Review, owner: Owner, description: "Owner reviews."}
fields:
  - {name: AI, type: single_select, options: [running, blocked]}
---

## README

Applications.
"""

WRITER = """---
name: writer
title: Writer
runner: workflow
workflow: write
board: apps
---

# Writer

## Workflow

1. Read the posting.
2. Write.

## Rules

- Be concrete.
"""

HELPER = """---
name: helper
title: Helper
runner: workflow
workflow: help
board: tasks
---

## Workflow

1. Help.
"""


class FakeGitHub:
    """In-memory GitHub: boards with fields, items, issues and comments."""

    def __init__(self) -> None:
        self.projects: dict[int, dict[str, Any]] = {}
        self.issues: dict[str, dict[str, Any]] = {}
        self.items: dict[str, dict[str, Any]] = {}  # item id -> {project, issue, values}
        self.comments: list[tuple[str, str]] = []
        self.calls: list[str] = []

    def add_project(self, number: int, columns: list[str]) -> None:
        self.projects[number] = {
            "id": f"P{number}",
            "title": f"Board {number}",
            "readme": "",
            "short": "",
            "fields": {
                "Status": ProjectField(
                    f"F{number}_Status", "Status", "SINGLE_SELECT",
                    {c: f"o-{c}" for c in columns}, {c: "" for c in columns},
                )
            },
        }  # fmt: skip

    def _by_id(self, project_id: str) -> dict[str, Any]:
        return next(p for p in self.projects.values() if p["id"] == project_id)

    def project(self, owner: str, number: int) -> Project:
        p = self.projects[number]
        ids = {"Title": "F_Title"} | {n: f.id for n, f in p["fields"].items()}
        return Project(p["id"], p["title"], "https://example.org/p", dict(p["fields"]),
                       p["readme"], p["short"], ids)  # fmt: skip

    def create_field(self, project_id, name, data_type, options=None, descriptions=None) -> None:
        opts = {o: f"o-{o}" for o in options or []}
        descs = {o: (descriptions or {}).get(o, "") for o in options or []}
        self._by_id(project_id)["fields"][name] = ProjectField(
            f"F_{name}", name, data_type, opts, descs
        )
        self.calls.append(f"create_field:{name}")

    def set_options(self, field_id, options, descriptions=None) -> None:
        for p in self.projects.values():
            for name, f in list(p["fields"].items()):
                if f.id == field_id:
                    p["fields"][name] = ProjectField(
                        f.id, name, f.type, {o: f"o-{o}" for o in options},
                        {o: (descriptions or {}).get(o, "") for o in options},
                    )  # fmt: skip
                    for item in self.items.values():  # like GitHub: values are cleared
                        if item["project"] == p["id"]:
                            item["values"].pop(name, None)
        self.calls.append("set_options")

    def set_readme(self, project_id, readme, short_description) -> None:
        p = self._by_id(project_id)
        p["readme"], p["short"] = readme, short_description
        self.calls.append("set_readme")

    def rename_project(self, project_id: str, title: str) -> None:
        self._by_id(project_id)["title"] = title
        self.calls.append(f"rename:{title}")

    # views: project id -> [{id, name, layout, filter, fields}]
    views_: dict[str, list[dict[str, Any]]]

    def views(self, project_id: str) -> list[dict[str, Any]]:
        return [dict(v) for v in self.__dict__.setdefault("views_", {}).get(project_id, [])]

    def create_view(self, project_id, name, layout, field_ids=None) -> str:
        views = self.__dict__.setdefault("views_", {}).setdefault(project_id, [])
        view = {"id": f"V{len(views) + 1}", "name": name, "layout": layout, "filter": "",
                "fields": list(field_ids or [])}  # fmt: skip
        views.append(view)
        self.calls.append(f"create_view:{name}")
        return view["id"]

    def update_view(self, view_id, layout=None, filter=None, field_ids=None) -> None:
        for views in self.__dict__.setdefault("views_", {}).values():
            for v in views:
                if v["id"] == view_id:
                    v.update({k: val for k, val in (("layout", layout), ("filter", filter),
                              ("fields", field_ids)) if val is not None})  # fmt: skip
        self.calls.append(f"update_view:{view_id}")

    # repository meta and issue labels/assignees
    labels_: dict[str, str]

    def repo(self, repo: str) -> RepoMeta:
        return RepoMeta("R1", dict(self.__dict__.setdefault("labels_", {})), {"owner": "U1"})

    def viewer_login(self) -> str:
        return "owner"

    def create_label(self, repo_id, name, color="0e8a16") -> str:
        self.__dict__.setdefault("labels_", {})[name] = f"L_{name}"
        return f"L_{name}"

    def _issue(self, issue_id: str) -> dict[str, Any]:
        return self.issues[issue_id]

    def add_assignees(self, issue_id, user_ids) -> None:
        self._issue(issue_id).setdefault("assignees", {"nodes": []})["nodes"].append(
            {"login": "owner"}
        )
        self.calls.append(f"assign:{issue_id}")

    def remove_assignees(self, issue_id, user_ids) -> None:
        self._issue(issue_id)["assignees"] = {"nodes": []}
        self.calls.append(f"unassign:{issue_id}")

    def add_labels(self, issue_id, label_ids) -> None:
        names = [n for n, i in self.labels_.items() if i in label_ids]
        nodes = self._issue(issue_id).setdefault("labels", {"nodes": []})["nodes"]
        nodes += [{"name": n} for n in names]
        self.calls.append(f"label:{issue_id}")

    def remove_labels(self, issue_id, label_ids) -> None:
        names = {n for n, i in self.labels_.items() if i in label_ids}
        issue = self._issue(issue_id)
        issue["labels"] = {"nodes": [n for n in issue.get("labels", {}).get("nodes", [])
                                     if n["name"] not in names]}  # fmt: skip
        self.calls.append(f"unlabel:{issue_id}")

    def create_issue(self, repo_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        number = len(self.issues) + 1
        issue = {"id": f"I{number}", "number": number,
                 "url": f"https://github.com/someone/board/issues/{number}"}  # fmt: skip
        self.issues[issue["id"]] = issue | changes | {
            "state": "OPEN", "repository": {"nameWithOwner": "someone/board"}}  # fmt: skip
        return issue

    def add_to_project(self, project_id: str, content_id: str) -> str:
        item_id = f"PI{len(self.items) + 1}"
        self.items[item_id] = {"project": project_id, "issue": content_id, "values": {}}
        return item_id

    def set_field(self, project_id, item_id, f: ProjectField, value) -> None:
        if value is None:
            self.items[item_id]["values"].pop(f.name, None)
            return
        if f.type == "SINGLE_SELECT":
            assert value in f.options, (f.name, value)
        self.items[item_id]["values"][f.name] = value

    def add_comment(self, subject_id: str, body: str) -> None:
        self.comments.append((subject_id, body))

    def project_items(self, project_id: str) -> list[dict[str, Any]]:
        nodes = []
        for item_id, item in self.items.items():
            if item["project"] != project_id:
                continue
            values = [{"name": v, "field": {"name": k}} for k, v in item["values"].items()]
            issue = self.issues[item["issue"]]
            nodes.append({"id": item_id, "fieldValues": {"nodes": values},
                          "content": {"__typename": "Issue", **issue}})  # fmt: skip
        return nodes

    # test helpers
    def ticket(self, number: int, title: str, **values: str) -> str:
        issue = self.create_issue("R1", {"title": title, "body": "details"})
        item_id = self.add_to_project(f"P{number}", issue["id"])
        self.items[item_id]["values"].update(values)
        return item_id

    def values(self, item_id: str) -> dict[str, Any]:
        return self.items[item_id]["values"]


class FakeGit:
    def __init__(self) -> None:
        self.commits: list[tuple[list[str], str]] = []

    def last_commit(self, path: str) -> str:
        return "abc1234"

    def is_dirty(self, path: str) -> bool:
        return False

    def web_url(self) -> str:
        return "https://github.com/someone/vault"

    def commit_paths(self, paths, message):
        self.commits.append((paths, message))
        return paths

    def run(self):
        return None


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    root = tmp_path / "vault"
    for rel, text in {
        "boards/tasks.md": TASKS,
        "boards/apps.md": APPS,
        "agents/writer.md": WRITER,
        "agents/helper.md": HELPER,
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return Vault.open(root)


@pytest.fixture
def gh() -> FakeGitHub:
    fake = FakeGitHub()
    fake.add_project(3, ["Todo", "In Progress", "Done"])
    fake.add_project(4, ["New", "Active", "In Review"])
    return fake


def mirrored(gh: FakeGitHub, vault: Vault) -> None:
    events, failed = run_round(gh, vault, {}, "someone/board", mirror_only=True)
    assert not failed, events


def team(gh, vault, board, workflows, git=None, **kw) -> Team:
    cfg = load_config(vault)
    return Team(gh, vault, cfg, cfg.boards[board], workflows,
                vault.resolve(f".lifeos/agents/runs-{board}.json"), git=git, **kw)  # fmt: skip


# -- configuration -----------------------------------------------------------------------


def test_load_config_reads_contracts_and_agents(vault) -> None:
    cfg = load_config(vault)
    assert cfg.errors == []
    tasks = cfg.boards["tasks"]
    assert tasks.ai.responsible_field == "Responsible"
    assert tasks.column_names == ["New", "Todo", "In Progress", "Review", "Done"]
    assert tasks.readme == "Tasks of the owner and the AI."
    assert cfg.boards["apps"].column("Active").agent == "writer"
    assert [a.name for a in cfg.agents_of("tasks")] == ["helper"]
    assert cfg.agents["writer"].section("Rules") == "- Be concrete."


def test_contract_errors_are_reported(vault) -> None:
    with pytest.raises(ConfigError):
        parse_contract(TASKS.replace("review: Review", "review: Nowhere"), "boards/x.md")
    vault.resolve("agents/broken.md").write_text("---\nname: Other\n---\n", encoding="utf-8")
    assert load_config(vault).errors


# -- mirror ------------------------------------------------------------------------------


def test_mirror_applies_columns_fields_and_readme_and_keeps_statuses(gh, vault) -> None:
    item = gh.ticket(3, "Existing task", Status="In Progress")
    mirrored(gh, vault)
    project = gh.project("someone", 3)
    status = project.fields["Status"]
    assert list(status.options) == ["New", "Todo", "In Progress", "Review", "Done"]
    assert status.descriptions["Review"] == "Waiting for the owner."
    assert list(project.fields["Responsible"].options) == ["Owner", "AI: Helper"]
    assert list(project.fields["AI"].options) == ["running", "blocked"]
    assert project.readme == "Tasks of the owner and the AI."
    assert gh.values(item)["Status"] == "In Progress"  # restored after replacing options

    gh.calls.clear()
    mirrored(gh, vault)  # nothing changed: nothing to do
    assert gh.calls == []


def test_renamed_agent_keeps_its_tickets(gh, vault) -> None:
    item = gh.ticket(3, "Old task", Status="Todo")
    mirrored(gh, vault)
    project = gh.project("someone", 3)
    gh.set_field(project.id, item, project.fields["Responsible"], "AI: Helper")
    path = vault.resolve("agents/helper.md")
    renamed = HELPER.replace("title: Helper", "title: Aide\nformer_titles: [Helper]")
    path.write_text(renamed, encoding="utf-8")
    mirrored(gh, vault)
    project = gh.project("someone", 3)
    assert list(project.fields["Responsible"].options) == ["Owner", "AI: Aide"]
    assert gh.values(item)["Responsible"] == "AI: Aide"  # moved to the new name
    agent = load_config(vault).agents["helper"]
    assert agent.former_options == ["AI: Helper"]


def test_values_wiped_late_by_github_are_written_again(gh, vault) -> None:
    item = gh.ticket(3, "Running task", Status="In Progress")
    real = gh.set_field
    lost = []

    def flaky(project_id, item_id, field, value):  # GitHub clears the first write later
        if not lost:
            lost.append(value)
            return None
        return real(project_id, item_id, field, value)

    gh.set_field = flaky
    mirrored(gh, vault)  # replaces the Status options
    assert lost == ["In Progress"]
    assert gh.values(item)["Status"] == "In Progress"


def test_mirror_reports_drift_once_instead_of_overwriting(gh, vault) -> None:
    mirrored(gh, vault)
    gh.projects[4]["readme"] = "edited on GitHub"
    events, _ = run_round(gh, vault, {}, "someone/board", mirror_only=True)
    assert any("differs from the vault" in e for e in events)
    assert gh.projects[4]["readme"] == "edited on GitHub"  # not overwritten
    drift = [i for i in gh.issues.values() if "differs" in i["title"]]
    assert len(drift) == 1
    item = next(k for k, v in gh.items.items() if v["issue"] == drift[0]["id"])
    assert gh.values(item) == {"Status": "New", "Responsible": "Owner"}
    assert drift[0]["body"].startswith(comments.marker("lifeos"))

    run_round(gh, vault, {}, "someone/board", mirror_only=True)  # same drift: no new ticket
    assert len([i for i in gh.issues.values() if "differs" in i["title"]]) == 1


def test_changed_contract_is_applied_again(gh, vault) -> None:
    mirrored(gh, vault)
    path = vault.resolve("boards/apps.md")
    path.write_text(path.read_text(encoding="utf-8").replace("AI writes.", "AI drafts."),
                    encoding="utf-8")  # fmt: skip
    mirrored(gh, vault)
    assert gh.project("someone", 4).fields["Status"].descriptions["Active"] == "AI drafts."


# -- team: taking tickets by state ---------------------------------------------------------


def test_agent_takes_free_ticket_reports_and_moves_to_review(gh, vault) -> None:
    mirrored(gh, vault)
    item = gh.ticket(4, "Company – Role", Status="Active")
    seen: list[str] = []

    def write(ctx: RunContext) -> RunResult:
        assert gh.values(item)["AI"] == "running"
        seen.append(ctx.ticket.title)
        ctx.step("drafted the letter")
        return RunResult(report="Letter and CV ready.", summary="Company", cost_eur=0.3)

    git = FakeGit()
    events = team(gh, vault, "apps", {"write": write}, git=git).poll()
    assert seen == ["Company – Role"]
    assert events == ["#1 writer: done – Company"]
    assert gh.values(item) == {"Status": "In Review"}  # AI cleared
    start, report = (body for _, body in gh.comments)
    assert start.startswith(comments.marker("writer"))
    assert "**Active**" in start and "AI writes." in start
    assert "1. Read the posting." in start
    assert "https://github.com/someone/vault/blob/abc1234/agents/writer.md" in start
    assert "Letter and CV ready." in report and "0.30 €" in report

    paths, message = git.commits[0]
    assert paths == ["agents/writer/runs.md"]
    assert message.startswith("agent(writer): #1 done – Company")
    assert "Refs someone/board#1" in message
    log = vault.resolve("agents/writer/runs.md").read_text(encoding="utf-8")
    assert "#1 Company – Role" in log and "drafted the letter" in log

    assert team(gh, vault, "apps", {"write": write}).poll() == []  # in review: not again
    gh.values(item)["Status"] = "Active"  # owner moves it back: revise
    team(gh, vault, "apps", {"write": write}).poll()
    assert len(seen) == 2


def test_question_blocks_until_owner_clears_the_field(gh, vault) -> None:
    mirrored(gh, vault)
    item = gh.ticket(4, "Company – Role", Status="Active")
    answers = iter([AgentBlocked("Which salary should I name?"), None])

    def write(ctx: RunContext) -> RunResult:
        exc = next(answers)
        if exc:
            raise exc
        return RunResult(report="ok")

    t = team(gh, vault, "apps", {"write": write})
    assert t.poll() == ["#1 writer: blocked – Which salary should I name?"]
    assert gh.values(item) == {"Status": "Active", "AI": "blocked"}
    assert "Which salary" in gh.comments[-1][1]
    assert t.poll() == []  # blocked: left alone
    gh.values(item).pop("AI")  # the owner answered and cleared the field
    assert t.poll() == ["#1 writer: done"]


def test_failing_workflow_blocks_with_error_comment(gh, vault) -> None:
    mirrored(gh, vault)
    item = gh.ticket(4, "Company – Role", Status="Active")

    def broken(ctx):
        raise RuntimeError("model unavailable")

    assert "error" in team(gh, vault, "apps", {"write": broken}).poll()[0]
    assert gh.values(item)["AI"] == "blocked"
    assert "model unavailable" in gh.comments[-1][1]


def test_missing_workflow_asks_instead_of_crashing(gh, vault) -> None:
    mirrored(gh, vault)
    item = gh.ticket(4, "Company – Role", Status="Active")
    team(gh, vault, "apps", {}).poll()
    assert gh.values(item)["AI"] == "blocked"
    assert "`write`" in gh.comments[-1][1]


def test_tasks_board_only_takes_tickets_responsible_names_an_agent(gh, vault) -> None:
    mirrored(gh, vault)
    mine = gh.ticket(3, "Owner's task", Status="Todo", Responsible="Owner")
    legacy = gh.ticket(3, "Old task", Status="Todo")
    ai = gh.ticket(3, "AI task", Status="Todo", Responsible="AI: Helper")
    moves: list[str] = []

    def helper(ctx):
        moves.append(gh.values(ai)["Status"])
        return RunResult(report="done")

    events = team(gh, vault, "tasks", {"help": helper}).poll()
    assert events == ["#3 helper: done"]
    assert moves == ["In Progress"]  # moved to the working column first
    assert gh.values(ai)["Status"] == "Review"
    assert gh.values(mine)["Status"] == "Todo" and gh.values(legacy)["Status"] == "Todo"


def test_interrupted_run_becomes_blocked(gh, vault) -> None:
    mirrored(gh, vault)
    item = gh.ticket(4, "Company – Role", Status="Active", AI="running")
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    clock = iter([now, now + timedelta(minutes=31)])
    t = team(gh, vault, "apps", {}, clock=lambda: next(clock))
    assert t.poll() == []  # first seen running: give it time
    assert t.poll() == ["#1: run interrupted → blocked"]
    assert gh.values(item)["AI"] == "blocked"


def test_vault_texts_override_the_wording(gh, vault) -> None:
    mirrored(gh, vault)
    vault.resolve("agents/texts.yaml").write_text(
        "taken: '▶️ **Übernommen: {agent}**'\nworkflow_heading: Workflow\n", encoding="utf-8"
    )
    gh.ticket(4, "Company – Role", Status="Active")
    team(gh, vault, "apps", {"write": lambda ctx: RunResult(report="ok")}).poll()
    assert "Übernommen: Writer" in gh.comments[0][1]


def test_owner_comments_have_no_marker() -> None:
    assert comments.agent_of(comments.marker("writer") + "\nhello") == "writer"
    assert comments.agent_of("Please make it shorter.") is None


def test_github_web_url() -> None:
    assert github_web_url("git@github.com:someone/vault.git") == "https://github.com/someone/vault"
    assert (
        github_web_url("git@github-vault:someone/vault.git") == "https://github.com/someone/vault"
    )
    assert (
        github_web_url("https://github.com/someone/vault.git") == "https://github.com/someone/vault"
    )
    assert github_web_url("/srv/other") is None


def test_numbered_steps_joins_wrapped_lines() -> None:
    section = "1. First step\n   continues here.\n2. Second.\n\nAfter the list."
    assert comments.numbered_steps(section) == "1. First step continues here.\n2. Second."


# -- billing: API or subscription ------------------------------------------------------------


def test_billing_defaults_to_api_and_is_validated(vault) -> None:
    assert load_config(vault).agents["writer"].billing == "api"
    path = vault.resolve("agents/writer.md")
    path.write_text(WRITER.replace("board: apps", "board: apps\nbilling: subscription"),
                    encoding="utf-8")  # fmt: skip
    assert load_config(vault).agents["writer"].billing == "subscription"
    path.write_text(WRITER.replace("board: apps", "board: apps\nbilling: free"), encoding="utf-8")
    cfg = load_config(vault)
    assert "writer" not in cfg.agents
    assert any("billing must be one of api, subscription" in e for e in cfg.errors)


def test_subscription_run_shows_tokens_usage_and_token_expiry(gh, vault) -> None:
    vault.resolve("agents/writer.md").write_text(
        WRITER.replace("board: apps", "board: apps\nbilling: subscription"), encoding="utf-8"
    )
    mirrored(gh, vault)
    gh.ticket(4, "Company – Role", Status="Active")
    now = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)

    def write(ctx: RunContext) -> RunResult:
        ctx.spend.tokens = 212_400
        ctx.spend.list_eur = 0.71
        ctx.spend.limits = {"five_hour": 0.43, "seven_day": 0.271}
        ctx.spend.credential_expires_at = "2026-12-10T08:00:00Z"
        return RunResult(report="ok", summary="Company")

    team(gh, vault, "apps", {"write": write}, clock=lambda: now).poll()
    start, report = (body for _, body in gh.comments)
    assert "💳 Paid via: Claude subscription" in start
    assert "212k tokens (API price 0.71 €)" in report
    assert "usage 43 % (5 h), 27 % (week)" in report
    assert "expires on 2026-12-10" in report and "claude setup-token" in report
    assert " · 0.00 €" not in report
    log = vault.resolve("agents/writer/runs.md").read_text(encoding="utf-8")
    assert "Subscription: 212k tokens (API price 0.71 €)" in log


def test_api_footer_keeps_the_cost() -> None:
    from lifeos.agents.spend import Spend

    text = comments.footer(comments.Texts(), "abc", 75, Spend(cost_eur=0.42))
    assert text == "<sub>Instructions `abc` · 1 min 15 s · 0.42 €</sub>"
    far = Spend(tokens=1000, credential_expires_at="2027-06-01T00:00:00Z")
    sub = comments.footer(comments.Texts(), "abc", 5, far, "subscription",
                          datetime(2026, 12, 1, tzinfo=UTC))  # fmt: skip
    assert "1k tokens" in sub and "expires" not in sub and "usage" not in sub
