"""The agents page API: list agents and switch how their model calls are paid."""

from __future__ import annotations

from pathlib import Path

from lifeos.agents.admin import with_billing, with_history

AGENT = """---
name: writer
title: Writer
workflow: write
board: apps
model: claude-opus-5-5
---

# Writer

## History

- 2026-11-01: first version.

## Rejected

- nothing yet
"""

BOARD = """---
board: apps
title: Applications
project: someone/4
ai: {pickup: Active, working: Active, review: In Review}
columns:
  - {name: Active, owner: ai, agent: writer, description: "AI writes."}
  - {name: In Review, owner: Owner, description: "Owner reviews."}
---
"""


def setup_vault(root: Path, agent: str = AGENT, newline: str = "\n") -> Path:
    (root / "agents").mkdir(parents=True, exist_ok=True)
    (root / "boards").mkdir(parents=True, exist_ok=True)
    (root / "boards" / "apps.md").write_text(BOARD, encoding="utf-8")
    path = root / "agents" / "writer.md"
    path.write_bytes(agent.replace("\n", newline).encode("utf-8"))
    return path


def test_with_billing_replaces_or_adds_the_line() -> None:
    added = with_billing(AGENT, "subscription")
    assert "model: claude-opus-5-5\nbilling: subscription\n---\n" in added
    replaced = with_billing(added, "api")
    assert replaced.count("billing:") == 1 and "billing: api\n" in replaced
    assert replaced.endswith(AGENT[AGENT.index("\n---\n") + 1 :])  # body untouched


def test_with_history_appends_to_the_section_or_creates_it() -> None:
    text = with_history(AGENT, "History", "2026-11-18: switched.")
    assert "- 2026-11-01: first version.\n- 2026-11-18: switched.\n\n## Rejected" in text
    bare = with_history("# Writer\n", "History", "x")
    assert bare == "# Writer\n\n## History\n\n- x\n"


def test_overview_lists_agents_with_billing(client, vault_root: Path) -> None:
    setup_vault(vault_root)
    data = client.get("/api/agents").json()
    assert data["billingOptions"] == ["api", "subscription"]
    (agent,) = data["agents"]
    assert agent["name"] == "writer" and agent["boardTitle"] == "Applications"
    assert agent["billing"] == "api" and agent["revision"]


def test_switch_billing_edits_front_matter_and_history(client, vault_root: Path) -> None:
    path = setup_vault(vault_root, newline="\r\n")  # a Windows checkout keeps CRLF
    revision = client.get("/api/agents").json()["agents"][0]["revision"]
    res = client.put("/api/agents/writer/billing",
                     json={"billing": "subscription", "revision": revision})  # fmt: skip
    assert res.status_code == 200, res.text
    assert res.json()["billing"] == "subscription"
    raw = path.read_bytes().decode("utf-8")
    assert "\r\nbilling: subscription\r\n---\r\n" in raw
    assert "paid via Claude subscription instead of API (pay per use) (switched in LifeOS)." in raw
    assert "\n" not in raw.replace("\r\n", "")  # line endings kept
    assert client.get("/api/agents").json()["agents"][0]["billing"] == "subscription"


def test_switch_billing_rejects_stale_revision_and_bad_values(client, vault_root: Path) -> None:
    setup_vault(vault_root)
    stale = client.put("/api/agents/writer/billing",
                       json={"billing": "subscription", "revision": "old"})  # fmt: skip
    assert stale.status_code == 409
    assert client.put("/api/agents/writer/billing", json={"billing": "free"}).status_code == 422
    assert client.put("/api/agents/nobody/billing", json={"billing": "api"}).status_code == 404


def test_unchanged_billing_writes_nothing(client, vault_root: Path) -> None:
    path = setup_vault(vault_root)
    before = path.read_text(encoding="utf-8")
    res = client.put("/api/agents/writer/billing", json={"billing": "api", "revision": "x"})
    assert res.status_code == 200 and path.read_text(encoding="utf-8") == before


def test_agents_api_needs_login(anon) -> None:
    assert anon.get("/api/agents").status_code == 401
