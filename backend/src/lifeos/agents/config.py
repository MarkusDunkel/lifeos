"""Board contracts (``boards/*.md``) and agent definitions (``agents/*.md``) from the vault.

Both are Markdown files with YAML front matter: the front matter is the machine-readable
part, the body is written for humans. A contract's README section (a ``## README …``
heading) is mirrored to the GitHub project.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from lifeos.core.extract.text import front_matter
from lifeos.core.vault import Vault

BOARDS_DIR = "boards"
AGENTS_DIR = "agents"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
AI_FIELD = "AI"
RUNNING = "running"
BLOCKED = "blocked"
BILLING = ("api", "subscription")  # how an agent's model calls are paid
AI_OPTIONS = [RUNNING, BLOCKED]
OWNER_AI = "ai"
# whose turn a ticket in a column is (mirrored to the issue: owner = assignee, ai = label)
TURNS = ("owner", "ai", "responsible", "responsible-ai", "none")
VIEW_LAYOUTS = {"table": "TABLE_LAYOUT", "board": "BOARD_LAYOUT", "roadmap": "ROADMAP_LAYOUT"}
README_HEADING = re.compile(r"^##\s+README\b.*$", re.M)


class ConfigError(ValueError):
    pass


@dataclass
class Column:
    name: str
    owner: str  # a person, "ai" or "responsible" (whoever the Responsible field names)
    description: str = ""
    agent: str | None = None  # the agent that works on tickets in this column
    # owner | ai | responsible (AI: … → ai, else owner) | responsible-ai (AI: … → ai, else
    # nobody) | none
    turn: str = "none"


@dataclass
class FieldSpec:
    name: str
    type: str  # single_select | text | number | date
    options: list[str] = field(default_factory=list)
    options_from: str | None = None  # "agents": one option "AI: <title>" per agent of the board
    description: str = ""

    @property
    def github_type(self) -> str:
        return self.type.upper()


@dataclass
class ViewSpec:
    name: str
    layout: str  # table | board | roadmap
    filter: str = ""
    fields: list[str] = field(default_factory=list)  # visible fields (empty = leave as is)
    # what the API cannot set (grouping, sorting, roadmap dates): told to the owner once
    manual: str = ""

    @property
    def github_layout(self) -> str:
        return VIEW_LAYOUTS[self.layout]


@dataclass
class AiColumns:
    pickup: str  # where the AI takes tickets
    working: str  # where they sit while it works (may equal pickup)
    review: str  # where it hands them over
    responsible_field: str | None = None  # boards with mixed tickets: who does the work
    # moving a ticket here undoes what the AI made live for it (vault commits, merged app
    # changes); None = no undo on this board
    discard: str | None = None


@dataclass
class BoardContract:
    board: str
    title: str
    project: str  # owner/number
    path: str
    ai: AiColumns
    columns: list[Column]
    fields: list[FieldSpec]
    readme: str
    body: str
    inbox: str | None = None  # column where new tickets of the AI land (proposals, notices)
    views: list[ViewSpec] = field(default_factory=list)

    def column(self, name: str | None) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


@dataclass
class AgentDef:
    name: str
    title: str
    path: str
    runner: str
    workflow: str | None
    board: str | None
    model: str
    raw: dict[str, Any]
    body: str
    billing: str = "api"  # api (pay per token) | subscription (Claude Code, owner's plan)

    @property
    def responsible_option(self) -> str:
        return f"AI: {self.title}"

    @property
    def former_options(self) -> list[str]:
        """Options under earlier titles (``former_titles:``): tickets still set to one of
        them belong to this agent, and the board mirror renames them."""
        former = self.raw.get("former_titles") or []
        return [f"AI: {t}" for t in (former if isinstance(former, list) else [former])]

    def section(self, heading: str) -> str:
        """The text of the ``## <heading>`` section (empty if missing)."""
        return section(self.body, heading)


def split_front(text: str) -> tuple[dict[str, Any], str]:
    front = front_matter(text)
    if not front:
        return {}, text
    end = text.find("\n---\n", 4)
    return front, text[end + 5 :]


def section(body: str, heading: str) -> str:
    match = re.search(rf"^##\s+{re.escape(heading)}\s*$", body, re.M)
    if not match:
        return ""
    rest = body[match.end() :]
    nxt = re.search(r"^##\s", rest, re.M)
    return (rest[: nxt.start()] if nxt else rest).strip()


def readme_of(body: str) -> str:
    match = README_HEADING.search(body)
    if not match:
        return ""
    rest = body[match.end() :]
    nxt = re.search(r"^##\s", rest, re.M)
    return (rest[: nxt.start()] if nxt else rest).strip()


def parse_contract(text: str, path: str) -> BoardContract:
    front, body = split_front(text)
    try:
        board = str(front["board"])
        ai = front["ai"]
        columns = [
            Column(
                name=str(c["name"]),
                owner=str(c.get("owner", "")),
                description=str(c.get("description", "")).strip(),
                agent=c.get("agent"),
                turn=str(c.get("turn", "none")),
            )
            for c in front["columns"]
        ]
        fields = [
            FieldSpec(
                name=str(f["name"]),
                type=str(f.get("type", "text")).lower(),
                options=[str(o) for o in f.get("options") or []],
                options_from=f.get("options_from"),
                description=str(f.get("description", "")).strip(),
            )
            for f in front.get("fields") or []
        ]
        views = [
            ViewSpec(
                name=str(v["name"]),
                layout=str(v.get("layout", "table")).lower(),
                filter=str(v.get("filter") or "").strip(),
                fields=[str(x) for x in v.get("fields") or []],
                manual=str(v.get("manual") or "").strip(),
            )
            for v in front.get("views") or []
        ]
        contract = BoardContract(
            board=board,
            title=str(front["title"]),
            project=str(front["project"]),
            path=path,
            ai=AiColumns(
                pickup=str(ai["pickup"]),
                working=str(ai.get("working", ai["pickup"])),
                review=str(ai["review"]),
                responsible_field=ai.get("responsible_field"),
                discard=ai.get("discard"),
            ),
            columns=columns,
            fields=fields,
            readme=readme_of(body),
            body=body,
            inbox=front.get("inbox"),
            views=views,
        )
    except (KeyError, TypeError) as exc:
        raise ConfigError(f"{path}: missing or invalid entry {exc}") from exc
    if not NAME_RE.match(board):
        raise ConfigError(f"{path}: board name must be lower-case letters, digits and '-'")
    names = contract.column_names
    for col in (contract.ai.pickup, contract.ai.working, contract.ai.review,
                *([contract.ai.discard] if contract.ai.discard else [])):  # fmt: skip
        if col not in names:
            raise ConfigError(f"{path}: column '{col}' (ai) is not in columns")
    for col in contract.columns:
        if col.turn not in TURNS:
            raise ConfigError(
                f"{path}: column '{col.name}': turn must be one of {', '.join(TURNS)}"
            )
        if col.turn.startswith("responsible") and not contract.ai.responsible_field:
            raise ConfigError(
                f"{path}: column '{col.name}': turn '{col.turn}' needs responsible_field"
            )
    seen_views: set[str] = set()
    for view in contract.views:
        if view.layout not in VIEW_LAYOUTS:
            raise ConfigError(f"{path}: view '{view.name}': layout must be table, board or roadmap")
        if view.name in seen_views:
            raise ConfigError(f"{path}: view '{view.name}' appears twice")
        seen_views.add(view.name)
    if contract.inbox and contract.inbox not in names:
        raise ConfigError(f"{path}: inbox column '{contract.inbox}' is not in columns")
    if contract.ai.responsible_field and contract.ai.responsible_field not in {
        f.name for f in fields
    }:
        raise ConfigError(f"{path}: responsible_field is not in fields")
    return contract


def parse_agent(text: str, path: str) -> AgentDef:
    front, body = split_front(text)
    name = str(front.get("name", ""))
    if not NAME_RE.match(name):
        raise ConfigError(f"{path}: agent name must be lower-case letters, digits and '-'")
    if path.rsplit("/", 1)[-1] != f"{name}.md":
        raise ConfigError(f"{path}: file name must be {name}.md")
    billing = str(front.get("billing", "api"))
    if billing not in BILLING:
        raise ConfigError(f"{path}: billing must be one of {', '.join(BILLING)}")
    return AgentDef(
        name=name,
        title=str(front.get("title") or name),
        path=path,
        runner=str(front.get("runner", "workflow")),
        workflow=front.get("workflow"),
        board=front.get("board"),
        model=str(front.get("model", "claude-opus-5-5")),
        raw=front,
        body=body,
        billing=billing,
    )


@dataclass
class AgentConfig:
    boards: dict[str, BoardContract]
    agents: dict[str, AgentDef]
    errors: list[str]

    @property
    def inbox(self) -> BoardContract | None:
        return next((b for b in self.boards.values() if b.inbox), None)

    def agents_of(self, board: str) -> list[AgentDef]:
        return [a for a in self.agents.values() if a.board == board]


def load_config(vault: Vault) -> AgentConfig:
    """All contracts and definitions; broken files are reported, not fatal."""
    boards: dict[str, BoardContract] = {}
    agents: dict[str, AgentDef] = {}
    errors: list[str] = []
    for folder, parse, target in (
        (BOARDS_DIR, parse_contract, boards),
        (AGENTS_DIR, parse_agent, agents),
    ):
        root = vault.resolve(folder)
        if not root.is_dir():
            continue
        for file in sorted(root.glob("*.md")):
            rel = f"{folder}/{file.name}"
            try:
                item = parse(file.read_text(encoding="utf-8"), rel)
            except (OSError, ConfigError) as exc:
                errors.append(str(exc))
                continue
            key = item.board if isinstance(item, BoardContract) else item.name
            target[key] = item  # type: ignore[index]
    for contract in boards.values():
        for col in contract.columns:
            if col.agent and col.agent not in agents:
                errors.append(f"{contract.path}: column '{col.name}' names unknown agent")
    return AgentConfig(boards, agents, errors)
