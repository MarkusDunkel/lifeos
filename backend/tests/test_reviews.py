"""Review packages: review.yaml, the review page API, rendered views and feedback rounds."""

from __future__ import annotations

from pathlib import Path

import pytest

from lifeos.core.vault import Vault
from lifeos.modules.reviews.package import PackageError, parse_package
from lifeos.modules.reviews.service import ReviewService, ViewError

PKG = "ready/knowledge/Career/Applications/2026-01-01 Beispiel AG"
REVIEW = """\
title: Bewerbung – Beispiel AG
ticket: someone/board#7
documents:
  - label: Anschreiben
    source: Anschreiben.md
    view: {type: pdf-generated, generator: fake-letter, input: draft.json}
  - label: Notizen
    source: Notizen.md
    view: markdown
  - label: Alt
    source: Notizen.md
    view: {type: pdf, file: Alt.pdf}
"""


@pytest.fixture
def package(vault_root: Path) -> Path:
    folder = vault_root / PKG
    folder.mkdir(parents=True)
    (folder / "review.yaml").write_text(REVIEW, encoding="utf-8")
    (folder / "Anschreiben.md").write_text("# Betreff\n\nErster Absatz.\n\nZweiter.\n", "utf-8")
    (folder / "Notizen.md").write_text("# Notizen\n\n- Punkt\n", encoding="utf-8")
    (folder / "draft.json").write_text('{"letter": 1}', encoding="utf-8")
    (folder / "Alt.pdf").write_bytes(b"%PDF-1.7 old")
    return folder


def test_parse_package_checks_paths_and_types() -> None:
    pkg = parse_package(REVIEW, PKG)
    assert pkg.title == "Bewerbung – Beispiel AG" and pkg.ticket == "someone/board#7"
    assert [d.view.type for d in pkg.documents] == ["pdf-generated", "markdown", "pdf"]
    with pytest.raises(PackageError, match="inside the package"):
        parse_package("documents: [{source: ../secret.md}]", PKG)
    with pytest.raises(PackageError, match="unknown view type"):
        parse_package("documents: [{source: a.md, view: word}]", PKG)
    with pytest.raises(PackageError, match="no documents"):
        parse_package("title: x", PKG)


def test_list_and_read_a_package(client, package) -> None:
    (package.parent / ".hidden").mkdir()
    (package.parent / ".hidden" / "review.yaml").write_text(REVIEW, encoding="utf-8")
    listed = client.get("/api/reviews").json()
    assert [p["path"] for p in listed] == [PKG]
    assert listed[0]["title"] == "Bewerbung – Beispiel AG" and listed[0]["documents"] == 3

    data = client.get("/api/reviews/package", params={"path": PKG}).json()
    first = data["documents"][0]
    assert first["label"] == "Anschreiben" and first["source"] == f"{PKG}/Anschreiben.md"
    assert first["content"].startswith("# Betreff") and first["view"] == "pdf-generated"
    assert first["rounds"] == []


def test_paths_outside_ready_and_unknown_documents(client, package) -> None:
    assert client.get("/api/reviews/package", params={"path": "inbox/x"}).status_code == 400
    assert client.get("/api/reviews/package", params={"path": "ready/nothing"}).status_code == 404
    assert client.get("/api/reviews/view", params={"path": PKG, "doc": 9}).status_code == 404


def test_pdf_views(client, package) -> None:
    pdf = client.get("/api/reviews/view", params={"path": PKG, "doc": 2})
    assert pdf.status_code == 200 and pdf.content == b"%PDF-1.7 old"
    assert pdf.headers["content-type"] == "application/pdf"
    markdown = client.get("/api/reviews/view", params={"path": PKG, "doc": 1})
    assert markdown.status_code == 422  # rendered in the browser, not here


def test_generated_view_is_made_on_request_and_cached(vault_root, package) -> None:
    calls: list[Path] = []

    def fake(vault: Vault, source: Path) -> bytes:
        calls.append(source)
        return b"%PDF generated from " + source.read_bytes()

    service = ReviewService(Vault.open(vault_root), generators={"fake-letter": fake})
    assert service.view(PKG, 0) == b'%PDF generated from {"letter": 1}'
    assert service.view(PKG, 0) == b'%PDF generated from {"letter": 1}'
    assert len(calls) == 1  # cached by the input's content
    (package / "draft.json").write_text('{"letter": 2}', encoding="utf-8")
    assert service.view(PKG, 0).endswith(b'{"letter": 2}')
    assert not any(p.suffix == ".pdf" and p.name != "Alt.pdf" for p in package.iterdir())

    def broken(vault: Vault, source: Path) -> bytes:
        raise RuntimeError("cover letter has 2 pages")

    with pytest.raises(ViewError, match="2 pages"):
        ReviewService(Vault.open(vault_root), generators={"fake-letter": broken}).view(PKG, 0)


def test_feedback_round_on_a_document(client, vault_root, package) -> None:
    ref = {"path": PKG, "doc": 0}
    started = client.post("/api/reviews/rounds", json=ref).json()
    rid = started["round"]
    assert started["source"] == f"{PKG}/Anschreiben.md" and started["editable"]
    item = {**ref, "round": rid, "start_line": 3, "end_line": 3, "scope": "blocks",
            "feedback": "Zu allgemein."}  # fmt: skip
    data = client.post("/api/reviews/rounds/items", json=item).json()
    assert data["items"][0]["quote"] == "Erster Absatz."
    sealed = client.post("/api/reviews/rounds/seal", json={**ref, "round": rid}).json()
    assert sealed["state"] == "sealed"
    file = vault_root / ".md4h/feedback" / PKG / f"Anschreiben.md--{rid}" / "feedback.md"
    assert file.is_file() and sealed["feedbackFile"] == file.relative_to(vault_root).as_posix()
    rounds = client.get("/api/reviews/package", params={"path": PKG}).json()["documents"][0]
    assert rounds["rounds"][0]["state"] == "sealed" and rounds["rounds"][0]["items"] == 1
    # the other documents have their own rounds
    assert (
        client.get("/api/reviews/package", params={"path": PKG}).json()["documents"][1]["rounds"]
        == []
    )
