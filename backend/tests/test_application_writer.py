from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_agents import APPS, FakeGit, FakeGitHub, mirrored, team
from test_applications_render import MASTER, PERSONAL, draft

from lifeos.agents import comments
from lifeos.core.vault import Vault
from lifeos.modules.applications.model import ChosenBullet
from lifeos.modules.applications.profile import GUIDE_FILE, MASTER_FILE, PERSONAL_FILE
from lifeos.modules.applications.service import INDEX_FILE
from lifeos.modules.applications.writer import (
    Check,
    FeedbackItem,
    Notes,
    Review,
    Usage,
    WriterOutput,
    make_application_writer,
    usage_usd,
)

FOLDER = "ready/knowledge/Career/Applications/2026-01-01 Beispiel AG – Data Engineer"
WRITER = """---
name: writer
title: Writer
runner: workflow
workflow: application-writer
board: apps
max_searches: 3
max_revisions: 2
budget_eur: 5
files: {letter: Letter.pdf, cv: Resume.pdf}
---

## Workflow

1. Research, write, review.

## Review checklist

1. Facts from the master CV?
2. Concrete company reference?
"""


class FakeLlm:
    """Scripted model: research text, then writer outputs and reviews in order."""

    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = outputs
        self.calls: list[tuple[str, str, str]] = []  # (kind, system, prompt)
        self.images: list[list[bytes]] = []  # per parse call

    def research(self, system, prompt, max_searches, effort):
        self.calls.append(("research", system, prompt))
        return "Beispiel AG builds data platforms.\n\nSources: https://example.org", Usage(0.05, 2)

    def parse(self, system, prompt, schema, effort, images=None):
        self.calls.append((schema.__name__, system, prompt))
        self.images.append(images or [])
        out = self.outputs.pop(0)
        assert isinstance(out, schema), (schema, out)
        return out, Usage(0.10, 0)


def output(bullet: str = "acme-sql", feedback: list | None = None) -> WriterOutput:
    return WriterOutput(
        draft=draft(bullet=bullet),
        notes=Notes(decisions=["SQL betont"], uncertain=["Ansprechperson geraten"],
                    please_check=["Gehalt weggelassen"]),
        feedback=feedback or [],
    )  # fmt: skip


def review(*failed: int, fixes: list[str] | None = None) -> Review:
    return Review(
        checks=[Check(number=n, passed=n not in failed, reason="") for n in (1, 2)],
        fixes=fixes or [],
    )


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    root = tmp_path / "vault"
    files = {
        "boards/apps.md": APPS.replace("agent: writer", "agent: writer"),
        "agents/writer.md": WRITER,
        PERSONAL_FILE: PERSONAL,
        MASTER_FILE: MASTER,
        GUIDE_FILE: "# Guide\n\n- Write concretely.\n",
        f"{FOLDER}/Posting.md": "# Data Engineer – Beispiel AG\n\nWe need SQL.\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    index = {"1": {"folder": FOLDER, "title": "Data Engineer", "company": "Beispiel AG"}}
    (root / INDEX_FILE).parent.mkdir(parents=True, exist_ok=True)
    (root / INDEX_FILE).write_text(json.dumps(index), encoding="utf-8")
    return Vault.open(root)


class CommentingGitHub(FakeGitHub):
    def issue_comments(self, issue_id: str) -> list[dict[str, Any]]:
        mine = [body for subject, body in self.comments if subject == issue_id]
        return [{"id": str(i), "body": body, "createdAt": ""} for i, body in enumerate(mine)]


@pytest.fixture
def gh() -> CommentingGitHub:
    fake = CommentingGitHub()
    fake.add_project(4, ["New", "Active", "In Review"])
    fake.add_project(3, ["Todo"])
    return fake


def run(gh, vault, llm) -> list[str]:
    workflow = make_application_writer(llm_factory=lambda agent: llm)
    return team(gh, vault, "apps", {"application-writer": workflow}, git=FakeGit()).poll()


def test_writes_documents_and_reports(gh, vault) -> None:
    mirrored(gh, vault)
    item = gh.ticket(4, "Beispiel AG – Data Engineer", Status="Active")
    llm = FakeLlm([output(), review()])
    events = run(gh, vault, llm)

    assert events == ["#1 writer: done – Beispiel AG"]
    assert gh.values(item) == {"Status": "In Review"}
    assert [c[0] for c in llm.calls] == ["research", "WriterOutput", "Review"]
    kind, system, prompt = llm.calls[1]
    assert "<master_cv>" in system and "acme-sql" in system and "Write concretely." in system
    assert "<posting>" in prompt and "We need SQL." in prompt
    assert "Beispiel AG builds data platforms." in prompt  # research goes to the writer
    assert "<owner_feedback>" not in prompt

    folder = vault.resolve(FOLDER)
    assert not (folder / "Letter.pdf").exists() and not (folder / "Resume.pdf").exists()
    assert (folder / "Cover-Letter.md").is_file() and (folder / "CV.md").is_file()
    assert json.loads((folder / "draft.json").read_text(encoding="utf-8"))["letter"]
    assert "data platforms" in (folder / "Research.md").read_text(encoding="utf-8")

    report = gh.comments[-1][1]
    assert "SQL betont" in report and "Ansprechperson geraten" in report
    assert "Gehalt weggelassen" in report and "2 of 2 checks passed" in report
    assert "https://github.com/someone/vault/blob/main/" in report and "Cover-Letter.md" in report
    assert "0.25 €" not in report  # cost is in the footer, in EUR
    assert "0.23 €" in report  # (0.05 + 0.10 + 0.10) USD × 0.9


def test_review_findings_lead_to_a_revision(gh, vault) -> None:
    mirrored(gh, vault)
    gh.ticket(4, "Beispiel AG – Data Engineer", Status="Active")
    llm = FakeLlm([output(), review(2, fixes=["Firmenbezug konkretisieren"]), output(), review()])
    run(gh, vault, llm)
    assert [c[0] for c in llm.calls] == ["research", "WriterOutput", "Review", "WriterOutput",
                                         "Review"]  # fmt: skip
    assert "Firmenbezug konkretisieren" in llm.calls[3][2]
    report = gh.comments[-1][1]
    assert "Firmenbezug konkretisieren" in report and "2 of 2 checks passed" in report


def test_invented_bullet_is_caught_before_review(gh, vault) -> None:
    mirrored(gh, vault)
    gh.ticket(4, "Beispiel AG – Data Engineer", Status="Active")
    bad = output()
    bad.draft.cv.positions[0].bullets.append(ChosenBullet(base="made-up", text="Erfunden"))
    llm = FakeLlm([bad, output(), review()])
    run(gh, vault, llm)
    assert [c[0] for c in llm.calls] == ["research", "WriterOutput", "WriterOutput", "Review"]
    assert "made-up" in llm.calls[2][2]


def test_still_invalid_after_revisions_blocks(gh, vault) -> None:
    mirrored(gh, vault)
    item = gh.ticket(4, "Beispiel AG – Data Engineer", Status="Active")
    bad = output()
    bad.draft.cv.positions[0].bullets.append(ChosenBullet(base="made-up", text="Erfunden"))
    llm = FakeLlm([bad, bad, bad])
    assert "error" in run(gh, vault, llm)[0]
    assert gh.values(item)["AI"] == "blocked"
    assert "made-up" in gh.comments[-1][1]


def test_revision_uses_owner_feedback_and_updates_the_research(gh, vault) -> None:
    mirrored(gh, vault)
    item = gh.ticket(4, "Beispiel AG – Data Engineer", Status="Active")
    run(gh, vault, FakeLlm([output(), review()]))
    gh.add_comment("I1", "Bitte die Einleitung kürzer und die Adresse recherchieren.")
    gh.values(item)["Status"] = "Active"
    llm = FakeLlm([output(), review()])
    run(gh, vault, llm)
    assert [c[0] for c in llm.calls] == ["research", "WriterOutput", "Review"]
    _, research_system, research_prompt = llm.calls[0]
    assert "update the research notes" in research_system
    assert "<research>" in research_prompt and "data platforms" in research_prompt  # so far
    assert "Adresse recherchieren" in research_prompt  # the owner's comments steer it
    prompt = llm.calls[1][2]
    assert "<owner_feedback>" in prompt and "Einleitung kürzer" in prompt
    assert "<previous_draft>" in prompt and "leave everything nobody commented on" in prompt
    assert "Übernommen" not in prompt and comments.marker("writer") not in prompt
    review_prompt = llm.calls[2][2]
    assert "<previous_draft>" in review_prompt  # the reviewer checks what stayed
    assert llm.images[1] and llm.images[1][0][1:4] == b"PNG"  # and sees the pages


def test_feedback_is_reported_as_read_and_general_points_become_proposals(gh, vault) -> None:
    mirrored(gh, vault)
    item = gh.ticket(4, "Beispiel AG – Data Engineer", Status="Active")
    run(gh, vault, FakeLlm([output(), review()]))
    gh.add_comment("I1", "Einleitung kürzer.")
    gh.add_comment("I1", "Und generell: nie das Gehalt nennen.")
    gh.values(item)["Status"] = "Active"
    read = [
        FeedbackItem(number=1, comment="Einleitung kürzer", applied=True, understood="Gekürzt.",
                     general=False, proposal_title="", proposal_body=""),
        FeedbackItem(number=2, comment="nie das Gehalt nennen", applied=True,
                     understood="Gilt künftig immer.",
                     general=True, proposal_title="Writer: never name a salary",
                     proposal_body="Occasion: comment on #1."),
    ]  # fmt: skip
    llm = FakeLlm([output(feedback=read), review()])
    run(gh, vault, llm)
    assert "Comment 1:\nEinleitung kürzer." in llm.calls[1][2]
    report = gh.comments[-1][1]
    assert "“Einleitung kürzer” → for this ticket: Gekürzt." in report
    assert "“nie das Gehalt nennen” → general: Gilt künftig immer." in report


def test_unknown_folder_asks(gh, vault) -> None:
    mirrored(gh, vault)
    gh.ticket(4, "Other – Role", Status="Active")
    gh.ticket(4, "Beispiel AG – Data Engineer", Status="Active")  # issue #2, not in index
    vault.resolve(INDEX_FILE).write_text("{}", encoding="utf-8")
    events = run(gh, vault, FakeLlm([]))
    assert all("blocked" in e for e in events)
    assert "folder" in gh.comments[-1][1]


def test_usage_cost() -> None:
    class U:
        input_tokens = 1_000_000
        output_tokens = 100_000
        cache_creation_input_tokens = 0
        cache_read_input_tokens = 1_000_000

        class server_tool_use:  # noqa: N801
            web_search_requests = 3

    used = usage_usd("claude-opus-5-5", U)
    assert used.searches == 3
    assert round(used.usd, 2) == round(4 + 2 + 0.2 + 0.03, 2)


def test_billing_selects_the_model_access(monkeypatch) -> None:
    from lifeos.agents.config import parse_agent
    from lifeos.modules.applications.writer import (
        AnthropicLlm,
        ClaudeCodeLlm,
        Settings,
        default_llm,
    )

    head = "---\nname: w\nmodel: claude-opus-5-5\n"
    sub = parse_agent(head + "billing: subscription\n---\nbody", "agents/w.md")
    llm = default_llm(sub, Settings(claude_command="/opt/claude --debug", claude_timeout=60))
    assert isinstance(llm, ClaudeCodeLlm)
    assert llm.cli.command == ["/opt/claude", "--debug"] and llm.cli.timeout == 60
    assert llm.model == "claude-opus-5-5"
    api = parse_agent(head + "---\nbody", "agents/w.md")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")  # the client needs one to start
    assert isinstance(default_llm(api, Settings()), AnthropicLlm)


def test_writer_leaves_a_review_package(gh, vault) -> None:
    from lifeos.modules.reviews.service import ReviewService

    mirrored(gh, vault)
    gh.ticket(4, "Beispiel AG – Data Engineer", Status="Active")
    run(gh, vault, FakeLlm([output(), review()]))
    package = ReviewService(vault, generators={}).package(FOLDER)
    assert package["ticket"] == "someone/board#1"
    labels = [d["label"] for d in package["documents"]]
    assert labels == ["Cover letter", "CV", "Notes", "Research"]
    letter, cv = package["documents"][0], package["documents"][1]
    assert letter["source"].endswith("/Cover-Letter.md") and letter["view"] == "pdf-generated"
    assert letter["content"].startswith("# ") and "Mit freundlichen Grüßen" in letter["content"]
    assert cv["content"].startswith("# ") and "## Arbeitserfahrung" in cv["content"]


def test_review_feedback_is_read_from_the_rounds_and_answered(gh, vault) -> None:
    from lifeos.modules.reviews.service import ReviewService
    from lifeos.modules.workspaces.rounds import FeedbackRounds

    mirrored(gh, vault)
    item = gh.ticket(4, "Beispiel AG – Data Engineer", Status="Active")
    run(gh, vault, FakeLlm([output(), review()]))
    gh.issue = lambda repo, number: {"id": "I1", "url": "https://github.com/x/issues/1"}
    service = ReviewService(vault, generators={}, github=lambda: gh)
    rid = service.start(FOLDER, 0)["round"]
    service.add_item(FOLDER, 0, rid, start_line=1, end_line=1, scope="blocks",
                     feedback="Betreff zu lang.")  # fmt: skip
    rid2 = service.start(FOLDER, 1)["round"]
    service.add_item(FOLDER, 1, rid2, start_line=1, end_line=1, scope="blocks",
                     feedback="Titel passt nicht.")  # fmt: skip
    service.send(FOLDER)
    assert gh.values(item)["Status"] == "In Review"  # sending does not move the ticket
    gh.add_comment("I1", "Und kürzer bitte.")  # a plain comment as well
    gh.values(item)["Status"] = "Active"

    read = [
        FeedbackItem(number=1, comment="kürzer", applied=True, understood="Gekürzt.",
                     general=False, proposal_title="", proposal_body=""),
        FeedbackItem(number=2, comment="Betreff", applied=True, understood="Betreff gekürzt.",
                     general=False, proposal_title="", proposal_body=""),
        FeedbackItem(number=3, comment="Titel", applied=False,
                     understood="Der Titel steht so im Master-Lebenslauf.",
                     general=False, proposal_title="", proposal_body=""),
    ]  # fmt: skip
    llm = FakeLlm([output(feedback=read), review()])
    run(gh, vault, llm)

    prompt = llm.calls[1][2]
    assert "Comment 1:\nUnd kürzer bitte." in prompt
    assert "Comment 2 (review page, Cover letter, Cover-Letter.md lines 1-1)" in prompt
    assert "Betreff zu lang." in prompt and "Comment 3 (review page, CV," in prompt
    assert "Feedback from the review" not in prompt  # the summary is not read twice
    report = gh.comments[-1][1]
    assert "“kürzer” → for this ticket: Gekürzt." in report
    assert "Cover letter F1" in report and "✅ done: Betreff gekürzt." in report
    assert "↩️ not done: Der Titel steht so im Master-Lebenslauf." in report
    rounds = FeedbackRounds(vault)
    answers = rounds.response(f"{FOLDER}/Cover-Letter.md", rid)
    assert [(a.id, a.status, a.text) for a in answers] == [("F1", "done", "Betreff gekürzt.")]
    assert rounds.response(f"{FOLDER}/CV.md", rid2)[0].status == "declined"


def test_area_comment_prompt_names_page_and_area() -> None:
    from lifeos.modules.applications.writer import Reviewed
    from lifeos.modules.reviews.handoff import SentRound
    from lifeos.modules.workspaces.feedback import Item, Region, Round

    item = Item("F2", 0, 0, "rendered-region", "", "Logo kleiner.", region=Region(1, 5, 5, 20, 8))
    sent = SentRound("Cover letter", f"{FOLDER}/Cover-Letter.md",
                     Round("r", "s", "x", "sealed", "", "F3", items=[item]))  # fmt: skip
    prompt = Reviewed(4, sent, item).prompt()
    assert prompt.startswith("Comment 4 (review page, Cover letter, on the rendered PDF: page 1 ·")
    assert "(no text)" in prompt and "Logo kleiner." in prompt


def test_pdfs_of_earlier_runs_are_removed_and_sent_applications_are_not_rewritten(
    gh, vault
) -> None:
    from lifeos.modules.reviews.service import ReviewService

    mirrored(gh, vault)
    item = gh.ticket(4, "Beispiel AG – Data Engineer", Status="Active")
    folder = vault.resolve(FOLDER)
    (folder / "Letter.pdf").write_bytes(b"%PDF old")  # from the time PDFs were stored
    run(gh, vault, FakeLlm([output(), review()]))
    assert not (folder / "Letter.pdf").exists()

    fake = {
        "application-letter": lambda v, p: b"%PDF letter",
        "application-cv": lambda v, p: b"%PDF cv",
    }
    ReviewService(vault, generators=fake).freeze(FOLDER, "2026-02-01")
    gh.values(item)["Status"] = "Active"
    llm = FakeLlm([])
    [event] = run(gh, vault, llm)
    assert event.startswith("#1 writer: blocked – This application was sent on 2026-02-01")
    assert "start a new application" in gh.comments[-1][1]
    assert llm.calls == [] and (folder / "Cover-Letter.pdf").read_bytes() == b"%PDF letter"


def test_sent_freezes_the_pdfs_and_keeps_the_letter_as_an_example(gh, vault) -> None:
    from types import SimpleNamespace

    from lifeos.modules.applications.profile import EXAMPLES_DIR
    from lifeos.modules.applications.service import Applications
    from lifeos.modules.jobsearch.store import JobStore

    mirrored(gh, vault)
    gh.ticket(4, "Beispiel AG – Data Engineer", Status="Active")
    run(gh, vault, FakeLlm([output(), review()]))
    folder = vault.resolve(FOLDER)
    (folder / "Application.md").write_text("# Application\n\n## History\n\n- 2026-01-01: new\n",
                                           encoding="utf-8")  # fmt: skip
    commits: list = []
    apps = Applications(vault, JobStore(vault), None, None, None,
                        committer=lambda paths, msg: commits.append((paths, msg)))  # fmt: skip

    note = apps.sent(SimpleNamespace(number=1))

    assert note == "frozen: Cover-Letter.pdf, CV.pdf"
    assert (folder / "Cover-Letter.pdf").read_bytes().startswith(b"%PDF")
    assert (folder / "CV.pdf").read_bytes().startswith(b"%PDF")
    example = vault.resolve(f"{EXAMPLES_DIR}/{folder.name}.md").read_text(encoding="utf-8")
    assert "company: Beispiel AG" in example and "Mit freundlichen Grüßen" in example
    cv = vault.resolve(f"{EXAMPLES_DIR}/CV/{folder.name}.md").read_text(encoding="utf-8")
    assert "company: Beispiel AG" in cv and "## Arbeitserfahrung" in cv
    assert "## Selection" in cv and '"base": "acme-sql"' in cv  # ids for the next selection
    history = (folder / "Application.md").read_text(encoding="utf-8")
    assert "sent; frozen: Cover-Letter.pdf, CV.pdf" in history
    [(paths, message)] = commits
    assert f"{FOLDER}/review.yaml" in paths and message.startswith("application: sent – ")
    assert apps.sent(SimpleNamespace(number=1)) == "already frozen"
    assert apps.sent(SimpleNamespace(number=9)) == "no review package, nothing frozen"


def test_owner_facts_update_the_master_and_the_reviewer_sees_the_comments(gh, vault) -> None:
    import yaml

    from lifeos.modules.applications.master import MasterUpdate

    mirrored(gh, vault)
    item = gh.ticket(4, "Beispiel AG – Data Engineer", Status="Active")
    run(gh, vault, FakeLlm([output(), review()]))
    gh.add_comment("I1", "Die Stelle bei ACME endet 2026: bitte '2024 – 2026'.")
    gh.values(item)["Status"] = "Active"
    read = [FeedbackItem(number=1, comment="Zeitraum", applied=True, understood="Übernommen.",
                         general=False, proposal_title="", proposal_body="")]  # fmt: skip
    out = output(feedback=read)
    out.master_updates = [
        MasterUpdate(comment=1, op="set_period", entry="acme", text="2024 – 2026"),
        MasterUpdate(comment=5, op="set_period", entry="tu", text="1999"),  # no such comment
    ]
    llm = FakeLlm([out, review()])
    run(gh, vault, llm)

    review_prompt = llm.calls[2][2]
    assert "<owner_feedback>" in review_prompt and "endet 2026" in review_prompt
    assert "2024 – 2026" in llm.calls[2][1]  # the reviewer checks against the updated master
    master = yaml.safe_load(vault.resolve(MASTER_FILE).read_text(encoding="utf-8"))
    acme = master["positions"][0]
    assert acme["period"] == "2024 – 2026"
    assert acme["origin"].startswith("someone/board#1 · comment 1 · ")
    assert master["education"][0]["period"] == "2018 – 2020"
    cv = vault.resolve(f"{FOLDER}/CV.md").read_text(encoding="utf-8")
    assert "2024 – 2026" in cv  # used in the same run
    report = gh.comments[-1][1]
    assert "Master CV updated" in report and "period of acme → 2024 – 2026" in report
    assert "Not taken into the master CV" in report and "comment 5 is not from" in report


def test_without_owner_updates_the_master_stays_untouched(gh, vault) -> None:
    mirrored(gh, vault)
    gh.ticket(4, "Beispiel AG – Data Engineer", Status="Active")
    before = vault.resolve(MASTER_FILE).read_text(encoding="utf-8")
    run(gh, vault, FakeLlm([output(), review()]))
    assert vault.resolve(MASTER_FILE).read_text(encoding="utf-8") == before


def test_sent_cvs_are_examples_newest_first(gh, vault) -> None:
    from lifeos.modules.applications.profile import EXAMPLE_CVS_DIR

    for name in ("2026-01-01 Old AG – Dev", "2026-03-01 New AG – Dev"):
        path = vault.resolve(f"{EXAMPLE_CVS_DIR}/{name}.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# CV for {name}\n", encoding="utf-8")
    mirrored(gh, vault)
    gh.ticket(4, "Beispiel AG – Data Engineer", Status="Active")
    llm = FakeLlm([output(), review()])
    run(gh, vault, llm)
    system = llm.calls[1][1]
    block = system[system.index("<example_cvs>") :]
    assert block.index("1. 2026-03-01 New AG – Dev (newest)") < block.index("2. 2026-01-01 Old")
    assert "<example_letters>\n(none)" in system  # cover letters are separate
