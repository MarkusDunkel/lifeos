"""Thin GitHub GraphQL client for issues and Projects v2 (user or organisation boards)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

API = "https://api.github.com/graphql"
EDITABLE_FIELD_TYPES = {"SINGLE_SELECT", "TEXT", "NUMBER", "DATE"}


class GitHubError(Exception):
    pass


@dataclass
class ProjectField:
    id: str
    name: str
    type: str  # SINGLE_SELECT | TEXT | NUMBER | DATE
    options: dict[str, str] = field(default_factory=dict)  # option name -> id
    descriptions: dict[str, str] = field(default_factory=dict)  # option name -> description


@dataclass
class Project:
    id: str
    title: str
    url: str
    fields: dict[str, ProjectField]
    readme: str = ""
    short_description: str = ""


@dataclass
class RepoMeta:
    id: str
    labels: dict[str, str]  # name -> id
    assignees: dict[str, str]  # login -> id


class GitHubApi(Protocol):
    def project(self, owner: str, number: int) -> Project: ...
    def repo(self, repo: str) -> RepoMeta: ...
    def issues(self, repo: str) -> list[dict[str, Any]]: ...
    def issue(self, repo: str, number: int) -> dict[str, Any]: ...
    def update_issue(self, issue_id: str, changes: dict[str, Any]) -> None: ...
    def create_issue(self, repo_id: str, changes: dict[str, Any]) -> dict[str, Any]: ...
    def add_to_project(self, project_id: str, content_id: str) -> str: ...
    def set_field(
        self, project_id: str, item_id: str, f: ProjectField, value: str | None
    ) -> None: ...


FIELD_VALUES = """
fieldValues(first: 30) { nodes { __typename
  ... on ProjectV2ItemFieldSingleSelectValue { name field { ... on ProjectV2FieldCommon { name } } }
  ... on ProjectV2ItemFieldTextValue { text field { ... on ProjectV2FieldCommon { name } } }
  ... on ProjectV2ItemFieldNumberValue { number field { ... on ProjectV2FieldCommon { name } } }
  ... on ProjectV2ItemFieldDateValue { date field { ... on ProjectV2FieldCommon { name } } }
} }
"""

ISSUE_FIELDS = f"""
id number title body state url updatedAt
labels(first: 50) {{ nodes {{ name }} }}
assignees(first: 20) {{ nodes {{ login }} }}
projectItems(first: 20) {{ nodes {{ id project {{ id }} {FIELD_VALUES} }} }}
"""

ISSUES_QUERY = f"""
query($owner: String!, $name: String!, $cursor: String) {{
  repository(owner: $owner, name: $name) {{
    issues(first: 50, after: $cursor, orderBy: {{field: CREATED_AT, direction: ASC}}) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{ {ISSUE_FIELDS} }}
    }}
  }}
}}
"""

ISSUE_QUERY = f"""
query($owner: String!, $name: String!, $number: Int!) {{
  repository(owner: $owner, name: $name) {{ issue(number: $number) {{ {ISSUE_FIELDS} }} }}
}}
"""

REPO_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    id
    labels(first: 100) { nodes { id name } }
    assignableUsers(first: 100) { nodes { id login } }
  }
}
"""

PROJECT_BODY = """
projectV2(number: $number) {
  id title url readme shortDescription
  fields(first: 50) { nodes { __typename
    ... on ProjectV2FieldCommon { id name dataType }
    ... on ProjectV2SingleSelectField { options { id name description } }
  } }
}
"""


def split_repo(repo: str) -> tuple[str, str]:
    owner, _, name = repo.partition("/")
    if not owner or not name or "/" in name:
        raise GitHubError(f"repository must look like owner/name: {repo}")
    return owner, name


class GitHubClient:
    def __init__(self, token: str, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(
            headers={"Authorization": f"Bearer {token}", "User-Agent": "lifeos"},
            timeout=30,
            transport=transport,
        )

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        r = self._http.post(API, json={"query": query, "variables": variables})
        if r.status_code == 401:
            raise GitHubError("GitHub rejected the token (401)")
        r.raise_for_status()
        payload = r.json()
        if payload.get("errors"):
            raise GitHubError("; ".join(e.get("message", "?") for e in payload["errors"]))
        return payload["data"]

    # -- reads -------------------------------------------------------------------

    def project(self, owner: str, number: int) -> Project:
        node = None
        for kind in ("user", "organization"):
            q = (
                "query($login: String!, $number: Int!) "
                f"{{ {kind}(login: $login) {{ {PROJECT_BODY} }} }}"
            )
            try:
                data = self.graphql(q, {"login": owner, "number": number})
                node = (data.get(kind) or {}).get("projectV2")
            except GitHubError:
                node = None
            if node:
                break
        if not node:
            raise GitHubError(f"project {owner}/{number} not found (token needs the project scope)")
        fields = {}
        for f in node["fields"]["nodes"]:
            if f.get("dataType") in EDITABLE_FIELD_TYPES:
                raw = f.get("options") or []
                options = {o["name"]: o["id"] for o in raw}
                descriptions = {o["name"]: o.get("description") or "" for o in raw}
                fields[f["name"]] = ProjectField(
                    f["id"], f["name"], f["dataType"], options, descriptions
                )
        return Project(
            id=node["id"],
            title=node["title"],
            url=node["url"],
            fields=fields,
            readme=node.get("readme") or "",
            short_description=node.get("shortDescription") or "",
        )

    def repo(self, repo: str) -> RepoMeta:
        owner, name = split_repo(repo)
        data = self.graphql(REPO_QUERY, {"owner": owner, "name": name})["repository"]
        if data is None:
            raise GitHubError(f"repository {repo} not found")
        return RepoMeta(
            id=data["id"],
            labels={n["name"]: n["id"] for n in data["labels"]["nodes"]},
            assignees={n["login"]: n["id"] for n in data["assignableUsers"]["nodes"]},
        )

    def issues(self, repo: str) -> list[dict[str, Any]]:
        owner, name = split_repo(repo)
        out: list[dict[str, Any]] = []
        cursor = None
        while True:
            data = self.graphql(ISSUES_QUERY, {"owner": owner, "name": name, "cursor": cursor})
            if data["repository"] is None:
                raise GitHubError(f"repository {repo} not found")
            page = data["repository"]["issues"]
            out += page["nodes"]
            if not page["pageInfo"]["hasNextPage"]:
                return out
            cursor = page["pageInfo"]["endCursor"]

    def issue(self, repo: str, number: int) -> dict[str, Any]:
        owner, name = split_repo(repo)
        data = self.graphql(ISSUE_QUERY, {"owner": owner, "name": name, "number": number})
        issue = (data["repository"] or {}).get("issue")
        if issue is None:
            raise GitHubError(f"{repo}#{number} not found")
        return issue

    # -- writes ------------------------------------------------------------------

    def update_issue(self, issue_id: str, changes: dict[str, Any]) -> None:
        q = """mutation($input: UpdateIssueInput!) {
          updateIssue(input: $input) { issue { id } } }"""
        self.graphql(q, {"input": {"id": issue_id, **changes}})

    def create_issue(self, repo_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        q = """mutation($input: CreateIssueInput!) {
          createIssue(input: $input) { issue { id number url } } }"""
        data = self.graphql(q, {"input": {"repositoryId": repo_id, **changes}})
        return data["createIssue"]["issue"]

    def add_to_project(self, project_id: str, content_id: str) -> str:
        q = """mutation($p: ID!, $c: ID!) {
          addProjectV2ItemById(input: {projectId: $p, contentId: $c}) { item { id } } }"""
        return self.graphql(q, {"p": project_id, "c": content_id})["addProjectV2ItemById"]["item"][
            "id"
        ]

    # -- boards: setup, items, comments ----------------------------------------------

    def owner_id(self, login: str) -> str:
        for kind in ("user", "organization"):
            q = f"query($login: String!) {{ {kind}(login: $login) {{ id }} }}"
            try:
                node = self.graphql(q, {"login": login}).get(kind)
            except GitHubError:
                node = None
            if node:
                return node["id"]
        raise GitHubError(f"GitHub user or organisation {login} not found")

    def create_project(self, owner_id: str, title: str) -> dict[str, Any]:
        q = """mutation($o: ID!, $t: String!) {
          createProjectV2(input: {ownerId: $o, title: $t}) { projectV2 { id number url } } }"""
        return self.graphql(q, {"o": owner_id, "t": title})["createProjectV2"]["projectV2"]

    def link_repository(self, project_id: str, repo_id: str) -> None:
        q = """mutation($p: ID!, $r: ID!) {
          linkProjectV2ToRepository(input: {projectId: $p, repositoryId: $r}) {
            clientMutationId } }"""
        self.graphql(q, {"p": project_id, "r": repo_id})

    def create_field(
        self,
        project_id: str,
        name: str,
        data_type: str,
        options: list[str] | None = None,
        descriptions: dict[str, str] | None = None,
    ) -> None:
        q = """mutation($input: CreateProjectV2FieldInput!) {
          createProjectV2Field(input: $input) { clientMutationId } }"""
        payload: dict[str, Any] = {"projectId": project_id, "name": name, "dataType": data_type}
        if options is not None:
            payload["singleSelectOptions"] = _option_inputs(options, descriptions)
        self.graphql(q, {"input": payload})

    def set_options(
        self, field_id: str, options: list[str], descriptions: dict[str, str] | None = None
    ) -> None:
        """Replace the options of a single-select field (e.g. the built-in Status).

        GitHub clears every item's value of the field; callers restore them.
        """
        q = """mutation($input: UpdateProjectV2FieldInput!) {
          updateProjectV2Field(input: $input) { clientMutationId } }"""
        payload = {
            "fieldId": field_id,
            "singleSelectOptions": _option_inputs(options, descriptions),
        }
        self.graphql(q, {"input": payload})

    def rename_project(self, project_id: str, title: str) -> None:
        q = """mutation($p: ID!, $t: String!) {
          updateProjectV2(input: {projectId: $p, title: $t}) { projectV2 { id } } }"""
        self.graphql(q, {"p": project_id, "t": title})

    def set_readme(self, project_id: str, readme: str, short_description: str) -> None:
        q = """mutation($p: ID!, $r: String!, $s: String!) {
          updateProjectV2(input: {projectId: $p, readme: $r, shortDescription: $s}) {
            projectV2 { id } } }"""
        self.graphql(q, {"p": project_id, "r": readme, "s": short_description})

    def rename_field(self, field_id: str, name: str) -> None:
        q = """mutation($input: UpdateProjectV2FieldInput!) {
          updateProjectV2Field(input: $input) { clientMutationId } }"""
        self.graphql(q, {"input": {"fieldId": field_id, "name": name}})

    def rename_label(self, label_id: str, name: str) -> None:
        q = """mutation($id: ID!, $n: String!) {
          updateLabel(input: {id: $id, name: $n}) { label { id } } }"""
        self.graphql(q, {"id": label_id, "n": name})

    def create_label(self, repo_id: str, name: str, color: str = "0e8a16") -> str:
        q = """mutation($r: ID!, $n: String!, $c: String!) {
          createLabel(input: {repositoryId: $r, name: $n, color: $c}) { label { id } } }"""
        return self.graphql(q, {"r": repo_id, "n": name, "c": color})["createLabel"]["label"]["id"]

    def project_items(self, project_id: str) -> list[dict[str, Any]]:
        """All items of a board with their field values and the issue behind them."""
        q = f"""query($id: ID!, $cursor: String) {{ node(id: $id) {{ ... on ProjectV2 {{
          items(first: 100, after: $cursor) {{ pageInfo {{ hasNextPage endCursor }}
            nodes {{ id updatedAt {FIELD_VALUES}
              content {{ __typename ... on Issue {{ id number title url state body
                repository {{ nameWithOwner }} labels(first: 30) {{ nodes {{ name }} }} }} }}
            }} }} }} }} }}"""
        out: list[dict[str, Any]] = []
        cursor = None
        while True:
            node = self.graphql(q, {"id": project_id, "cursor": cursor})["node"]
            if node is None:
                raise GitHubError("project not found")
            page = node["items"]
            out += page["nodes"]
            if not page["pageInfo"]["hasNextPage"]:
                return out
            cursor = page["pageInfo"]["endCursor"]

    def issue_comments(self, issue_id: str) -> list[dict[str, Any]]:
        """The last 100 comments of an issue, oldest first: ``id, body, createdAt``."""
        q = """query($id: ID!) { node(id: $id) { ... on Issue {
          comments(last: 100) { nodes { id body createdAt } } } } }"""
        node = self.graphql(q, {"id": issue_id})["node"]
        if node is None:
            raise GitHubError("issue not found")
        return node["comments"]["nodes"]

    def add_comment(self, subject_id: str, body: str) -> None:
        q = """mutation($s: ID!, $b: String!) {
          addComment(input: {subjectId: $s, body: $b}) { clientMutationId } }"""
        self.graphql(q, {"s": subject_id, "b": body})

    def set_field(self, project_id: str, item_id: str, f: ProjectField, value: str | None) -> None:
        ids = {"p": project_id, "i": item_id, "f": f.id}
        if value is None:
            q = """mutation($p: ID!, $i: ID!, $f: ID!) { clearProjectV2ItemFieldValue(
              input: {projectId: $p, itemId: $i, fieldId: $f}) { projectV2Item { id } } }"""
            self.graphql(q, ids)
            return
        q = """mutation($p: ID!, $i: ID!, $f: ID!, $v: ProjectV2FieldValue!) {
          updateProjectV2ItemFieldValue(input: {projectId: $p, itemId: $i, fieldId: $f, value: $v})
          { projectV2Item { id } } }"""
        self.graphql(q, {**ids, "v": field_value(f, value)})


def _option_inputs(
    options: list[str], descriptions: dict[str, str] | None = None
) -> list[dict[str, str]]:
    descriptions = descriptions or {}
    return [
        {"name": name, "color": "GRAY", "description": descriptions.get(name, "")}
        for name in options
    ]


def field_value(f: ProjectField, value: str) -> dict[str, Any]:
    if f.type == "SINGLE_SELECT":
        if value not in f.options:
            raise GitHubError(f"'{value}' is not an option of {f.name}: {', '.join(f.options)}")
        return {"singleSelectOptionId": f.options[value]}
    if f.type == "NUMBER":
        try:
            return {"number": float(value)}
        except ValueError as exc:
            raise GitHubError(f"{f.name} needs a number, got '{value}'") from exc
    if f.type == "DATE":
        return {"date": value}
    return {"text": value}


def project_item(issue: dict[str, Any], project_id: str | None) -> dict[str, Any] | None:
    for item in issue.get("projectItems", {}).get("nodes", []):
        if project_id and item["project"]["id"] == project_id:
            return item
    return None


def item_values(item: dict[str, Any] | None, fields: dict[str, ProjectField]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for v in (item or {}).get("fieldValues", {}).get("nodes", []):
        name = (v.get("field") or {}).get("name")
        if name not in fields:
            continue
        for key in ("name", "text", "number", "date"):
            if v.get(key) is not None:
                values[name] = v[key]
    return values
