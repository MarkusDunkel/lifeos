"""Review packages: review.yaml, the review page API, rendered views and feedback rounds."""

from __future__ import annotations

from pathlib import Path

import pytest

from lifeos.agents import comments
from lifeos.connectors.github.client import GitHubError
from lifeos.core.vault import Vault
from lifeos.modules.reviews import handoff
from lifeos.modules.reviews.package import PackageError, parse_package
from lifeos.modules.reviews.service import ReviewService, ViewError
from lifeos.modules.workspaces.feedback import Region
from lifeos.modules.workspaces.rounds import Answer, FeedbackError

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


class TicketGitHub:
    """Just enough GitHub for "Send feedback": find the issue, add a comment."""

    def __init__(self, fail: bool = False) -> None:
        self.comments: list[tuple[str, str]] = []
        self.fail = fail

    def issue(self, repo: str, number: int) -> dict:
        return {"id": f"I{number}", "url": f"https://github.com/{repo}/issues/{number}"}

    def add_comment(self, subject_id: str, body: str) -> None:
        if self.fail:
            raise GitHubError("boom")
        self.comments.append((subject_id, body))


def sending(vault_root: Path, gh: TicketGitHub, commits: list | None = None) -> ReviewService:
    return ReviewService(
        Vault.open(vault_root), generators={}, github=lambda: gh,
        committer=(lambda paths, msg: commits.append((paths, msg))) if commits is not None
        else None, public_url="https://lifeos.example/",
    )  # fmt: skip


def comment(service: ReviewService, doc: int, line: int, text: str) -> str:
    rid = service.start(PKG, doc)["round"]
    service.add_item(PKG, doc, rid, start_line=line, end_line=line, scope="blocks", feedback=text)
    return rid


def test_send_feedback_posts_to_the_ticket_and_seals(vault_root, package) -> None:
    gh, commits = TicketGitHub(), []
    service = sending(vault_root, gh, commits)
    letter = comment(service, 0, 3, "Zu allgemein.\nKonkreter werden.")
    notes = comment(service, 1, 3, "Stimmt nicht.")
    assert service.start(PKG, 2)["round"] == notes  # same file as "Notizen": same round, once

    result = service.send(PKG)

    assert result["comments"] == 2 and result["url"].endswith("/board/issues/7")
    [(issue, body)] = gh.comments
    assert issue == "I7" and comments.agent_of(body) is None  # the owner's feedback
    assert "**Anschreiben**" in body and "F1 · “Erster Absatz.”" in body
    assert "→ Zu allgemein.\n  Konkreter werden." in body
    assert "https://lifeos.example/reviews/ready/knowledge/Career/" in body
    payload = handoff.parse_marker(body)
    assert payload is not None and payload["package"] == PKG
    sent = handoff.load_sent(Vault.open(vault_root), payload)
    assert [(s.label, s.round.round, s.round.state) for s in sent] == [
        ("Anschreiben", letter, "sealed"), ("Notizen", notes, "sealed")]  # fmt: skip
    assert sent[0].round.items[0].quote == "Erster Absatz."
    [(paths, message)] = commits
    assert len(paths) == 2 and all(p.startswith(".md4h/feedback/") for p in paths)
    assert "Bewerbung – Beispiel AG" in message
    with pytest.raises(FeedbackError, match="no comments"):
        service.send(PKG)  # nothing new


def test_send_feedback_keeps_drafts_when_github_fails(vault_root, package) -> None:
    service = sending(vault_root, TicketGitHub(fail=True))
    rid = comment(service, 0, 3, "Zu allgemein.")
    with pytest.raises(GitHubError):
        service.send(PKG)
    assert service.round(PKG, 0, rid)["state"] == "draft"  # try again later


def test_send_feedback_needs_a_ticket_and_github(client, vault_root, package) -> None:
    rid = client.post("/api/reviews/rounds", json={"path": PKG, "doc": 0}).json()["round"]
    item = {"path": PKG, "doc": 0, "round": rid, "start_line": 3, "end_line": 3,
            "scope": "blocks", "feedback": "x"}  # fmt: skip
    client.post("/api/reviews/rounds/items", json=item)
    assert client.post("/api/reviews/send", json={"path": PKG}).status_code == 503  # no token
    (package / "review.yaml").write_text(REVIEW.replace("ticket: someone/board#7\n", ""), "utf-8")
    response = client.post("/api/reviews/send", json={"path": PKG})
    assert response.status_code == 400 and "no ticket" in response.json()["detail"]


def test_agent_answers_are_stored_next_to_the_sealed_round(vault_root, package) -> None:
    service = sending(vault_root, TicketGitHub())
    rid = comment(service, 0, 3, "Zu allgemein.")
    source = f"{PKG}/Anschreiben.md"
    with pytest.raises(FeedbackError, match="not sealed"):
        service.feedback.respond(source, rid, "writer", [])
    service.send(PKG)
    answers = [Answer("F1", "done", "Konkreter:\n\nzwei Absätze."), Answer("F9", "done", "x")]
    rel = service.feedback.respond(source, rid, "writer", answers)
    assert rel.endswith(f"Anschreiben.md--{rid}/response.md")
    assert service.feedback.response(source, rid) == [answers[0]]  # unknown ids dropped
    text = (vault_root / rel).read_text(encoding="utf-8")
    assert "schema: lifeos-feedback-response/v1" in text and "## F1 · done" in text
    assert service.round(PKG, 0, rid)["state"] == "sealed"  # feedback.md untouched


def test_area_comments_on_the_pdf_view(client, vault_root, package) -> None:
    ref = {"path": PKG, "doc": 0}
    rid = client.post("/api/reviews/rounds", json=ref).json()["round"]
    area = {**ref, "round": rid, "scope": "rendered-region", "feedback": "Zu wenig Abstand.",
            "quote": "  Erster Absatz.  ",
            "region": {"page": 1, "x": 10.04, "y": 20, "w": 50, "h": 7.56}}  # fmt: skip
    data = client.post("/api/reviews/rounds/items", json=area).json()
    item = data["items"][0]
    assert item["scope"] == "rendered-region" and item["quote"] == "Erster Absatz."
    assert item["region"] == {"page": 1, "x": 10.0, "y": 20.0, "w": 50.0, "h": 7.6}
    assert item["start_line"] == 0
    # stored in md4h form and read back the same way
    file = vault_root / ".md4h/feedback" / PKG / f"Anschreiben.md--{rid}" / "feedback.md"
    text = file.read_text(encoding="utf-8")
    assert "**Region:** page 1 · x 10.0% · y 20.0% · w 50.0% · h 7.6%" in text
    assert "**Source lines:**" not in text and '"kind":"rendered-region"' in text
    again = client.get("/api/reviews/round", params={**ref, "round": rid}).json()["items"][0]
    assert {**again, "target": ""} == item
    # an empty area is fine (e.g. a margin); areas outside the page or without a region are not
    empty = {**area, "quote": None, "region": {"page": 2, "x": 0, "y": 90, "w": 100, "h": 10}}
    assert client.post("/api/reviews/rounds/items", json=empty).status_code == 200
    bad = {**area, "region": {"page": 1, "x": 80, "y": 0, "w": 30, "h": 5}}
    assert client.post("/api/reviews/rounds/items", json=bad).status_code == 400
    assert (
        client.post("/api/reviews/rounds/items", json={**area, "region": None}).status_code == 400
    )
    # only PDF views have areas
    rid2 = client.post("/api/reviews/rounds", json={"path": PKG, "doc": 1}).json()["round"]
    md = {**area, "doc": 1, "round": rid2}
    response = client.post("/api/reviews/rounds/items", json=md)
    assert response.status_code == 400 and "PDF" in response.json()["detail"]


def test_area_comments_in_the_ticket_summary(vault_root, package) -> None:
    gh = TicketGitHub()
    service = sending(vault_root, gh)
    rid = service.start(PKG, 0)["round"]
    service.add_item(PKG, 0, rid, start_line=0, end_line=0, scope="rendered-region",
                     feedback="Logo zu groß.", quote="",
                     region=Region(1, 0, 0, 30, 10))  # fmt: skip
    service.send(PKG)
    body = gh.comments[0][1]
    assert "- F1 · “area on page 1”\n  → Logo zu groß." in body
    [sent] = handoff.load_sent(Vault.open(vault_root), handoff.parse_marker(body) or {})
    assert sent.round.items[0].region == Region(1, 0.0, 0.0, 30.0, 10.0)


def test_freeze_stores_the_generated_views_once(vault_root, package) -> None:
    service = ReviewService(
        Vault.open(vault_root), generators={"fake-letter": lambda v, p: b"%PDF x"}
    )
    written = service.freeze(PKG, "2026-02-01")
    assert written == [f"{PKG}/Anschreiben.pdf", f"{PKG}/review.yaml"]
    assert (package / "Anschreiben.pdf").read_bytes() == b"%PDF x"
    frozen = service.package(PKG)
    assert frozen["frozen"] == "2026-02-01" and frozen["documents"][0]["view"] == "pdf"
    assert service.view(PKG, 0) == b"%PDF x"  # from the file now
    assert service.list()[0]["frozen"] == "2026-02-01"
    assert service.freeze(PKG, "2026-03-01") == []  # once only


def test_freeze_writes_nothing_when_a_view_fails(vault_root, package) -> None:
    def broken(vault: Vault, source: Path) -> bytes:
        raise RuntimeError("2 pages")

    with pytest.raises(ViewError):
        ReviewService(Vault.open(vault_root), generators={"fake-letter": broken}).freeze(PKG, "x")
    assert not (package / "Anschreiben.pdf").exists()
    assert "frozen" not in (package / "review.yaml").read_text(encoding="utf-8")
