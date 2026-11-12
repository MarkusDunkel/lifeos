from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pptx import Presentation
from pptx.util import Inches

import lifeos.main
from lifeos.connectors.onedrive.auth import MicrosoftAuth, OneDriveError
from lifeos.connectors.onedrive.client import DriveFile, GraphClient
from lifeos.connectors.onedrive.extract import extract, front_matter
from lifeos.connectors.onedrive.sync import PresentationsSync
from lifeos.core.auth import hash_password
from lifeos.core.config import Settings
from lifeos.core.vault import Vault, VaultPathError
from lifeos.main import create_app

PASSWORD = "pw-for-tests"
TARGET = "ready/knowledge/Presentations"


def make_deck(title: str = "Quarterly review", bullet: str = "Revenue up", hidden=False) -> bytes:
    deck = Presentation()
    s1 = deck.slides.add_slide(deck.slide_layouts[1])  # title + content
    s1.shapes.title.text = title
    body = s1.placeholders[1].text_frame
    body.text = bullet
    sub = body.add_paragraph()
    sub.text = "# not a heading"
    sub.level = 1
    s1.notes_slide.notes_text_frame.text = "Say hello\nthen go on"

    s2 = deck.slides.add_slide(deck.slide_layouts[5])  # title only
    s2.shapes.title.text = "Numbers"
    table = s2.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(4), Inches(1)).table
    table.cell(0, 0).text, table.cell(0, 1).text = "Item", "Value"
    table.cell(1, 0).text, table.cell(1, 1).text = "A|B", "42"
    box = s2.shapes.add_textbox(Inches(1), Inches(4), Inches(4), Inches(1))
    box.text_frame.text = "A loose remark"
    if hidden:
        s2._element.set("show", "0")
    out = io.BytesIO()
    deck.save(out)
    return out.getvalue()


class FakeGraph:
    def __init__(self) -> None:
        self.files: dict[str, tuple[DriveFile, bytes]] = {}
        self.downloads: list[str] = []

    def put(self, item_id: str, path: str, data: bytes, ctag: str = "c1") -> None:
        url = f"https://example.invalid/{item_id}"
        self.files[item_id] = (DriveFile(item_id, path, ctag, url, "2026-01-01T00:00:00Z", 1), data)

    def account(self) -> str | None:
        return "Test User"

    def list_files(self, folder: str) -> list[DriveFile]:
        assert folder == "Decks"
        return [f for f, _ in self.files.values()]

    def download(self, item_id: str) -> bytes:
        self.downloads.append(item_id)
        return self.files[item_id][1]


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    (tmp_path / "vault" / "ready" / "knowledge").mkdir(parents=True)
    return Vault.open(tmp_path / "vault")


@pytest.fixture
def graph() -> FakeGraph:
    g = FakeGraph()
    g.put("ID1", "Quarterly.pptx", make_deck())
    g.put("ID2", "Team/Kickoff.pptx", make_deck("Kickoff", "Goals"))
    return g


@pytest.fixture
def mirror(vault: Vault, graph: FakeGraph) -> PresentationsSync:
    return PresentationsSync(vault, lambda: graph, "Decks", "Presentations")


def note(vault: Vault, rel: str) -> str:
    return (vault.root / TARGET / rel).read_text(encoding="utf-8")


# -- extraction ------------------------------------------------------------------


def test_extract_titles_bullets_tables_and_notes() -> None:
    slides = extract(make_deck(hidden=True))
    assert [s.title for s in slides] == ["Quarterly review", "Numbers"]
    assert slides[0].blocks == ["- Revenue up\n  - \\# not a heading"]
    assert slides[0].notes == "Say hello\nthen go on"
    assert slides[1].blocks[0].splitlines()[0] == "| Item | Value |"
    assert "| A\\|B | 42 |" in slides[1].blocks[0]
    assert slides[1].blocks[1] == "A loose remark"
    assert slides[1].hidden


def test_extract_rejects_garbage() -> None:
    with pytest.raises(Exception, match="not a readable"):
        extract(b"not a zip")


# -- sync ------------------------------------------------------------------------


def test_first_sync_creates_mirror_notes(vault: Vault, graph: FakeGraph, mirror) -> None:
    report = mirror.sync()
    assert report.created == ["Presentations/Quarterly.md", "Presentations/Team/Kickoff.md"]
    text = note(vault, "Quarterly.md")
    meta = front_matter(text)
    assert meta["type"] == "presentation" and meta["drive_item_id"] == "ID1"
    assert meta["url"] == "https://example.invalid/ID1"
    assert meta["onedrive_path"] == "Decks/Quarterly.pptx"
    assert meta["slides"] == 2
    assert "## Slide 1 · Quarterly review" in text
    assert "> **Notes:** Say hello\n> then go on" in text
    assert mirror.status()["presentations"] == 2
    assert mirror.status()["account"] == "Test User"


def test_unchanged_files_are_not_downloaded_again(graph: FakeGraph, mirror) -> None:
    mirror.sync()
    graph.downloads.clear()
    report = mirror.sync()
    assert report.unchanged == 2 and not report.created and not report.updated
    assert graph.downloads == []


def test_changed_file_updates_its_note(vault: Vault, graph: FakeGraph, mirror) -> None:
    mirror.sync()
    graph.put("ID1", "Quarterly.pptx", make_deck(bullet="Revenue down"), ctag="c2")
    report = mirror.sync()
    assert report.updated == ["Presentations/Quarterly.md"]
    assert "Revenue down" in note(vault, "Quarterly.md")


def test_renamed_file_moves_its_note(vault: Vault, graph: FakeGraph, mirror) -> None:
    mirror.sync()
    graph.put("ID1", "Archive/Q3.pptx", make_deck())
    report = mirror.sync()
    assert report.renamed == ["Presentations/Quarterly.md → Presentations/Archive/Q3.md"]
    assert not (vault.root / TARGET / "Quarterly.md").exists()
    assert front_matter(note(vault, "Archive/Q3.md"))["onedrive_path"] == "Decks/Archive/Q3.pptx"


def test_deleted_file_keeps_its_note_but_flags_it(vault: Vault, graph: FakeGraph, mirror) -> None:
    mirror.sync()
    del graph.files["ID2"]
    report = mirror.sync()
    assert report.deleted == ["Presentations/Team/Kickoff.md"]
    assert front_matter(note(vault, "Team/Kickoff.md"))["status"] == "deleted"
    assert "Goals" in note(vault, "Team/Kickoff.md")
    assert mirror.sync().deleted == []  # reported once
    # Restored from the recycle bin: the note comes back to life.
    graph.put("ID2", "Team/Kickoff.pptx", make_deck("Kickoff", "Goals"))
    mirror.sync()
    assert "status" not in front_matter(note(vault, "Team/Kickoff.md"))


def test_own_notes_are_never_overwritten(vault: Vault, graph: FakeGraph, mirror) -> None:
    mine = vault.root / TARGET / "Quarterly.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("# My own thoughts\n", encoding="utf-8")
    report = mirror.sync()
    assert report.errors and "different note already exists" in report.errors[0]
    assert mine.read_text(encoding="utf-8") == "# My own thoughts\n"
    assert report.created == ["Presentations/Team/Kickoff.md"]


def test_broken_file_is_reported_and_others_continue(graph: FakeGraph, mirror) -> None:
    graph.put("ID3", "Broken.pptx", b"garbage")
    report = mirror.sync()
    assert len(report.created) == 2
    assert report.errors == ["Broken.pptx: not a readable .pptx (password-protected or damaged?)"]


def test_target_folder_must_stay_inside_the_knowledge_hub(vault: Vault) -> None:
    with pytest.raises(VaultPathError):
        PresentationsSync(vault, None, "Decks", "../notes")


def test_unconfigured_sync_fails_cleanly(vault: Vault) -> None:
    with pytest.raises(OneDriveError, match="not set up"):
        PresentationsSync(vault, None, "Decks", "Presentations").sync()


# -- Microsoft sign-in and Graph client ---------------------------------------------


def test_device_flow_stores_the_token_outside_the_vault(tmp_path: Path) -> None:
    answers = iter(
        [
            (200, {"device_code": "D", "user_code": "ABCD", "verification_uri": "https://v"}),
            (400, {"error": "authorization_pending"}),
            (200, {"access_token": "A1", "refresh_token": "R1", "expires_in": 3600}),
            (200, {"access_token": "A2", "refresh_token": "R2", "expires_in": 3600}),
        ]
    )
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(httpx.QueryParams(request.content.decode())))
        status, body = next(answers)
        return httpx.Response(status, json=body)

    clock = [1000.0]
    auth = MicrosoftAuth(
        "client", tmp_path / "state", httpx.MockTransport(handler), lambda: clock[0]
    )
    assert auth.start()["user_code"] == "ABCD"
    assert auth.poll() == "pending"
    assert auth.poll() == "connected"
    stored = json.loads((tmp_path / "state" / "onedrive-token.json").read_text())
    assert stored["refresh_token"] == "R1"
    assert auth.access_token() == "A1"  # cached
    clock[0] += 7200
    assert auth.access_token() == "A2"  # refreshed
    assert seen[-1]["grant_type"] == "refresh_token" and seen[-1]["refresh_token"] == "R1"
    auth.disconnect()
    assert not auth.connected
    with pytest.raises(OneDriveError, match="not connected"):
        auth.access_token()


def test_graph_client_walks_folders_and_keeps_only_presentations() -> None:
    pages = {
        "/v1.0/me/drive/root:/My Decks:/children": {
            "value": [
                {"id": "F", "name": "Sub", "folder": {}},
                {"id": "1", "name": "A.pptx", "file": {}, "cTag": "c", "webUrl": "u"},
                {"id": "2", "name": "notes.docx", "file": {}},
            ]
        },
        "/v1.0/me/drive/items/F/children": {
            "value": [{"id": "3", "name": "B.PPTX", "file": {}, "cTag": "d", "webUrl": "w"}]
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer T"
        return httpx.Response(200, json=pages[request.url.path])

    client = GraphClient(lambda: "T", httpx.MockTransport(handler))
    files = client.list_files("/My Decks/")
    assert [(f.id, f.path) for f in files] == [("1", "A.pptx"), ("3", "Sub/B.PPTX")]


def test_graph_client_reports_a_missing_folder() -> None:
    client = GraphClient(lambda: "T", httpx.MockTransport(lambda r: httpx.Response(404, json={})))
    with pytest.raises(OneDriveError, match="folder 'Decks' not found"):
        client.list_files("Decks")


# -- API ---------------------------------------------------------------------------


@pytest.fixture
def api(tmp_path: Path, graph: FakeGraph, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    root = tmp_path / "vault"
    (root / "ready" / "knowledge").mkdir(parents=True)
    state = tmp_path / "deploy-state"
    state.mkdir()
    (state / "onedrive-token.json").write_text(
        json.dumps({"access_token": "T", "expires_at": 9e12}), encoding="utf-8"
    )
    monkeypatch.setattr(lifeos.main, "GraphClient", lambda token: graph)
    settings = Settings(
        vault_root=root,
        session_secret="x" * 40,
        password_hash=hash_password(PASSWORD),
        ms_client_id="client",
        state_dir=state,
        onedrive_folder="Decks",
    )
    c = TestClient(create_app(settings))
    assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return c


def test_api_status_and_sync(api: TestClient, tmp_path: Path) -> None:
    status = api.get("/api/presentations/status").json()
    assert status["configured"] and status["connected"] and status["missing"] == []
    report = api.post("/api/presentations/sync").json()
    assert len(report["created"]) == 2
    status = api.get("/api/presentations/status").json()
    assert status["presentations"] == 2 and status["last"]["synced_at"]
    doc = api.get("/api/knowledge/file", params={"path": "Presentations/Quarterly.md"}).json()
    assert "Quarterly review" in doc["content"]
    # The token never lands in the vault.
    assert not list((tmp_path / "vault").rglob("*token*"))


def test_api_without_configuration(client: TestClient) -> None:
    status = client.get("/api/presentations/status").json()
    assert not status["configured"] and "LIFEOS_MS_CLIENT_ID" in status["missing"]
    r = client.post("/api/presentations/connect")
    assert r.status_code == 400 and "LIFEOS_MS_CLIENT_ID" in r.json()["detail"]
    assert client.post("/api/presentations/sync").status_code == 400
