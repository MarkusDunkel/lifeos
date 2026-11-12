from pathlib import Path

from fastapi.testclient import TestClient

from lifeos.modules.knowledge.index import link_matches, link_targets


def test_tree_hides_dotfolders(client: TestClient) -> None:
    tree = client.get("/api/knowledge/tree").json()
    names = [c["name"] for c in tree["children"]]
    assert names == ["Studium", "Kosten.md"]  # dirs first, .obsidian hidden


def test_read_edit_conflict(client: TestClient) -> None:
    doc = client.get("/api/knowledge/file", params={"path": "Kosten.md"}).json()
    r = client.put(
        "/api/knowledge/file",
        json={
            "path": "Kosten.md",
            "content": doc["content"] + "Neu\n",
            "revision": doc["revision"],
        },
    )
    assert r.status_code == 200
    stale = client.put(
        "/api/knowledge/file",
        json={"path": "Kosten.md", "content": "x", "revision": doc["revision"]},
    )
    assert stale.status_code == 409
    assert stale.json()["currentRevision"] == r.json()["revision"]


def test_create_and_delete(client: TestClient, vault_root: Path) -> None:
    r = client.put("/api/knowledge/file", json={"path": "Neu/Idee.md", "content": "# Idee\n"})
    assert r.status_code == 200
    assert (vault_root / "ready/knowledge/Neu/Idee.md").exists()
    d = client.post(
        "/api/knowledge/delete", json={"path": "Neu/Idee.md", "revision": r.json()["revision"]}
    )
    assert d.status_code == 200


def test_rejects_traversal_and_binary_edit(client: TestClient) -> None:
    assert client.get("/api/knowledge/file", params={"path": "../../x.md"}).status_code == 400
    r = client.put("/api/knowledge/file", json={"path": "evil.sh", "content": "x"})
    assert r.status_code == 400


def test_search_finds_umlaut_free_prefix(client: TestClient) -> None:
    hits = client.get("/api/knowledge/search", params={"q": "kostenrech"}).json()
    assert [h["path"] for h in hits] == ["Studium/BWL.md"]
    assert "[[[" in hits[0]["snippet"]


def test_search_sees_external_edits(client: TestClient, vault_root: Path) -> None:
    (vault_root / "ready/knowledge/Extern.md").write_text("Zebrastreifen\n", encoding="utf-8")
    hits = client.get("/api/knowledge/search", params={"q": "zebra"}).json()
    assert hits[0]["path"] == "Extern.md"


def test_backlinks(client: TestClient) -> None:
    links = client.get("/api/knowledge/backlinks", params={"path": "Kosten.md"}).json()
    assert links == [{"path": "Studium/BWL.md", "title": "Betriebswirtschaftslehre"}]


def test_link_parsing() -> None:
    content = "[[Note]] [[Dir/Other|alias]] ![[img.png]] [[X#Heading]] [md](../Up.md)"
    assert link_targets("A/B.md", content) == {"note", "dir/other", "img.png", "x", "up"}
    assert link_matches("note", "Sub/Note.md")
    assert link_matches("dir/other", "Root/Dir/Other.md")
    assert not link_matches("note", "Notebook.md")
