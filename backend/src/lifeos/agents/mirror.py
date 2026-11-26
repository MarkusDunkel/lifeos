"""Mirror board contracts from the vault to GitHub (vault → GitHub only).

Each run compares what GitHub shows (columns with descriptions, contract fields, README)
with what the contract asks for:

- equal: nothing to do;
- the contract changed since it was last applied: apply it;
- the contract did not change but GitHub did (someone edited the board there): do not
  overwrite silently, report the drift once (the job turns it into a ticket in New).

State per board lives in ``.lifeos/agents/boards.json``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lifeos.agents.config import AgentConfig, BoardContract, FieldSpec, ViewSpec
from lifeos.connectors.github.client import Project, item_values
from lifeos.core.vault import write_atomic

log = logging.getLogger("lifeos.agents")

STATUS = "Status"
SHORT_DESCRIPTION_LIMIT = 250
# GitHub clears the items' values of a field whose options were replaced *asynchronously*:
# values written back right away can be wiped again. So wait, write back, check, repeat.
RESTORE_PAUSE = 3.0
RESTORE_ROUNDS = 5


def split_project(ref: str) -> tuple[str, int]:
    owner, _, number = ref.partition("/")
    if not owner or not number.isdigit():
        raise ValueError(f"project must look like owner/number: {ref}")
    return owner, int(number)


def field_options(spec: FieldSpec, contract: BoardContract, cfg: AgentConfig) -> list[str]:
    options = list(spec.options)
    if spec.options_from == "agents":
        options += [a.responsible_option for a in cfg.agents_of(contract.board)]
    return list(dict.fromkeys(options))


def short_description(contract: BoardContract) -> str:
    first = next((ln.strip() for ln in contract.readme.splitlines() if ln.strip()), "")
    text = f"{first} (vault: {contract.path})" if first else f"vault: {contract.path}"
    return text[:SHORT_DESCRIPTION_LIMIT]


def desired_state(contract: BoardContract, cfg: AgentConfig) -> dict[str, Any]:
    fields = {}
    for spec in contract.fields:
        entry: dict[str, Any] = {"type": spec.github_type}
        if spec.github_type == "SINGLE_SELECT":
            entry["options"] = field_options(spec, contract, cfg)
        fields[spec.name] = entry
    return {
        "title": contract.title,
        "columns": [[c.name, c.description] for c in contract.columns],
        "fields": fields,
        "readme": contract.readme.strip(),
        "short": short_description(contract),
    }


def current_state(project: Project, contract: BoardContract) -> dict[str, Any]:
    status = project.fields.get(STATUS)
    columns = (
        [[name, (status.descriptions.get(name) or "").strip()] for name in status.options]
        if status
        else []
    )
    fields = {}
    for spec in contract.fields:
        f = project.fields.get(spec.name)
        if f is None:
            continue
        entry: dict[str, Any] = {"type": f.type}
        if f.type == "SINGLE_SELECT":
            entry["options"] = list(f.options)
        fields[spec.name] = entry
    return {
        "title": project.title,
        "columns": columns,
        "fields": fields,
        "readme": project.readme.strip(),
        "short": project.short_description.strip(),
    }


def digest(data: Any) -> str:
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def differences(current: dict[str, Any], desired: dict[str, Any]) -> list[str]:
    out = []
    if current.get("title") != desired.get("title"):
        out.append("title")
    if current["columns"] != desired["columns"]:
        if [c[0] for c in current["columns"]] != [c[0] for c in desired["columns"]]:
            out.append("columns (names or order)")
        else:
            out.append("column descriptions")
    for name, want in desired["fields"].items():
        have = current["fields"].get(name)
        if have is None:
            out.append(f"field '{name}' missing")
        elif have != want:
            out.append(f"field '{name}' (type or options)")
    if current["readme"] != desired["readme"]:
        out.append("README")
    if current["short"] != desired["short"]:
        out.append("short description")
    return out


class BoardMirror:
    def __init__(
        self,
        gh: Any,
        state_file: Path,
        on_drift: Callable[[BoardContract, list[str]], None] | None = None,
        on_views: Callable[[BoardContract, list[ViewSpec]], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.gh = gh
        self.state_file = state_file
        self.sleep = sleep
        self.on_drift = on_drift
        self.on_views = on_views  # views were created that need settings the API cannot make

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, state: dict[str, Any]) -> None:
        write_atomic(self.state_file, json.dumps(state, ensure_ascii=False, indent=1))

    def sync(self, contract: BoardContract, cfg: AgentConfig) -> list[str]:
        owner, number = split_project(contract.project)
        project = self.gh.project(owner, number)
        desired = desired_state(contract, cfg)
        current = current_state(project, contract)
        state = self._load()
        entry = state.setdefault(contract.board, {})
        wanted = digest(desired)
        events: list[str] = []
        if current == desired:
            entry["applied"] = wanted
            entry.pop("drift", None)
        elif entry.get("applied") != wanted:
            renames = {old: a.responsible_option for a in cfg.agents.values()
                       for old in a.former_options}  # fmt: skip
            events += self.apply(project, owner, number, contract, desired, current, renames)
            entry["applied"] = wanted
            entry.pop("drift", None)
        else:
            diff = differences(current, desired)
            signature = digest(current)
            if entry.get("drift") != signature:
                entry["drift"] = signature
                listed = ", ".join(diff)
                events.append(f"{contract.board}: GitHub differs from the vault ({listed})")
                if self.on_drift:
                    self.on_drift(contract, diff)
        if contract.views:
            project = self.gh.project(owner, number)  # fields may just have been created
            events += self.sync_views(project, contract, entry)
        self._save(state)
        return events

    def sync_views(
        self, project: Project, contract: BoardContract, entry: dict[str, Any]
    ) -> list[str]:
        """Create missing views and update those whose contract entry changed.

        Views are matched by name; views that are not in the contract stay untouched, and so
        does a view the owner adjusted on GitHub as long as its contract entry is unchanged
        (grouping and sorting can only be set there).
        """
        events: list[str] = []
        applied: dict[str, str] = entry.setdefault("views", {})
        existing = {v["name"]: v for v in self.gh.views(project.id)}
        created: list[ViewSpec] = []
        for spec in contract.views:
            wanted = digest([spec.layout, spec.filter, spec.fields])
            # roadmaps have no visible fields on GitHub
            fields = [] if spec.layout == "roadmap" else spec.fields
            ids = [project.field_ids[f] for f in fields if f in project.field_ids]
            missing = [f for f in fields if f not in project.field_ids]
            if missing:
                events.append(f"{contract.board}: view '{spec.name}': unknown field(s) {missing}")
            view = existing.get(spec.name)
            if view is None:
                view_id = self.gh.create_view(project.id, spec.name, spec.github_layout, ids)
                if spec.filter:
                    self.gh.update_view(view_id, filter=spec.filter)
                applied[spec.name] = wanted
                events.append(f"{contract.board}: view '{spec.name}' created")
                if spec.manual:
                    created.append(spec)
            elif applied.get(spec.name) != wanted:
                self.gh.update_view(view["id"], spec.github_layout, spec.filter, ids or None)
                applied[spec.name] = wanted
                events.append(f"{contract.board}: view '{spec.name}' updated")
        if created and self.on_views:
            self.on_views(contract, created)
        return events

    def apply(
        self,
        project: Project,
        owner: str,
        number: int,
        contract: BoardContract,
        desired: dict[str, Any],
        current: dict[str, Any],
        renames: dict[str, str] | None = None,
    ) -> list[str]:
        """``renames``: old option → new one (an agent got a new title), so tickets keep
        their value when the options are replaced."""
        events: list[str] = []
        names = [c[0] for c in desired["columns"]]
        descriptions = {c[0]: c[1] for c in desired["columns"]}
        if current["columns"] != desired["columns"]:
            self._replace_options(project, owner, number, STATUS, names, descriptions)
            project = self.gh.project(owner, number)
            events.append(f"{contract.board}: columns mirrored")
        for name, want in desired["fields"].items():
            have = current["fields"].get(name)
            if have is None:
                self.gh.create_field(project.id, name, want["type"], want.get("options"))
                events.append(f"{contract.board}: field '{name}' created")
            elif have["type"] != want["type"]:
                events.append(
                    f"{contract.board}: field '{name}' has type {have['type']}, "
                    f"expected {want['type']} – check it on GitHub"
                )
            elif have != want:
                self._replace_options(
                    project, owner, number, name, want["options"], {}, renames or {}
                )
                events.append(f"{contract.board}: options of '{name}' mirrored")
            project = self.gh.project(owner, number)
        if current.get("title") != desired["title"]:
            self.gh.rename_project(project.id, desired["title"])
            events.append(f"{contract.board}: title mirrored")
        if current["readme"] != desired["readme"] or current["short"] != desired["short"]:
            self.gh.set_readme(project.id, desired["readme"], desired["short"])
            events.append(f"{contract.board}: README mirrored")
        return events

    def _replace_options(
        self,
        project: Project,
        owner: str,
        number: int,
        field_name: str,
        options: list[str],
        descriptions: dict[str, str],
        renames: dict[str, str] | None = None,
    ) -> None:
        """Replace a single-select field's options and restore every item's value (a value
        whose option was renamed gets the new name)."""
        f = project.fields[field_name]
        before = {
            node["id"]: item_values(node, project.fields).get(field_name)
            for node in self.gh.project_items(project.id)
        }
        # a copy of the old values, so they can be restored by hand if all else fails
        backup = self.state_file.with_name(f"values-before-{owner}-{number}-{field_name}.json")
        write_atomic(backup, json.dumps(before, ensure_ascii=False, indent=1))
        wanted = {
            item_id: (renames or {}).get(value, value)
            for item_id, value in before.items()
            if (renames or {}).get(value, value) in options
        }
        self.gh.set_options(f.id, options, descriptions)
        fresh = self.gh.project(owner, number)
        new_field = fresh.fields[field_name]
        missing = dict(wanted)
        for _ in range(RESTORE_ROUNDS):
            self.sleep(RESTORE_PAUSE)
            for item_id, value in missing.items():
                self.gh.set_field(fresh.id, item_id, new_field, value)
            self.sleep(RESTORE_PAUSE)
            now = {
                node["id"]: item_values(node, fresh.fields).get(field_name)
                for node in self.gh.project_items(fresh.id)
            }
            missing = {i: v for i, v in wanted.items() if now.get(i) != v}
            if not missing:
                return
        log.warning("%s: %d value(s) of '%s' could not be restored, see %s",
                    f"{owner}/{number}", len(missing), field_name, backup)  # fmt: skip
