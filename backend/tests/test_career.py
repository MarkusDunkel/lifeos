from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lifeos.agents.comments import Texts
from lifeos.agents.config import AgentDef
from lifeos.agents.llm import Usage
from lifeos.agents.team import RunContext, Ticket
from lifeos.core.vault import Vault
from lifeos.modules.applications.profile import MASTER_FILE
from lifeos.modules.applications.writer import Check, Notes, Review
from lifeos.modules.career import curriculum, documents, tables
from lifeos.modules.career.designer import RESEARCH_FILE, check, make_profile_designer
from lifeos.modules.career.market import market_stats, title_stem
from lifeos.modules.career.profiles import (
    OVERVIEW_FILE,
    PROFILES_DIR,
    CareerFiles,
    parse_overview,
    parse_profile,
)
from lifeos.modules.career.router import CareerService
from lifeos.modules.career.schema import DesignerOutput, LevelProposal, ProfileDraft, SkillLine

PROFILE = """# Test Automation Engineer

## Skills

| Skill | Anteil | Begründung |
|---|---|---|
| Python | Kern | most postings |
| Playwright | neben | web tests |
| `CI/CD` | Rand | pipelines |
| Cobol | sometimes | ? |
"""

OVERVIEW = """# Overview

| Profil | Gewicht % | Status |
|---|---|---|
| [[Profiles/qa/Profile|Test Automation]] | 60 | aktiv |
| `data` | 40 % | active |
| `old` | 10 | verworfen |

| Skill | Stand | Synonyme | Notiz |
|---|---|---|---|
| Python | 2 | py, Python 3 | |
| SQL | 3 | | |
| Playwright | | | |
"""


def test_tables_split_rows_with_wiki_links_and_escapes() -> None:
    assert tables.split_row("| [[a|b]] | x \\| y | z |") == ["[[a|b]]", "x | y", "z"]
    assert tables.wiki_label("[[Profiles/qa/Profile|QA]]") == "QA"
    assert tables.wiki_target("[[Profiles/qa/Profile|QA]]") == "Profiles/qa/Profile"


def test_profile_skills_are_read_in_german_and_english() -> None:
    p = parse_profile(PROFILE, "qa", "x/qa/Profile.md")
    assert p.title == "Test Automation Engineer"
    assert [(s.name, s.share) for s in p.skills] == [("Python", 3), ("Playwright", 2),
                                                     ("CI/CD", 1)]  # fmt: skip
    assert p.skills[0].reason == "most postings"
    assert p.problems == ["Cobol: unknown share “sometimes”"]


def test_overview_reads_weights_statuses_levels_and_aliases() -> None:
    o = parse_overview(OVERVIEW)
    assert [(e.slug, e.weight, e.status) for e in o.entries] == [
        ("qa", 60, "active"), ("data", 40, "active"), ("old", 10, "dropped")]  # fmt: skip
    assert o.aliases()["py"] == "python"
    assert o.level_of()["playwright"].level is None
    assert o.level_of()["sql"].level == 3


def _profile(text: str, slug: str):
    return parse_profile(text, slug, f"{slug}/Profile.md")


def test_curriculum_weights_profiles_and_gaps() -> None:
    data = _profile("# Data\n\n| Skill | Share |\n|---|---|\n| py | core |\n| SQL | core |\n"
                    "| Power BI | edge |\n", "data")  # fmt: skip
    qa = _profile(PROFILE, "qa")
    result = curriculum.compute([qa, data], parse_overview(OVERVIEW))
    items = {i.key: i for i in result.items}
    # python: 0.6 × 3/3 + 0.4 × 3/3 = 1.0 importance, level 2 → gap 1/3
    assert items["python"].importance == 1.0
    assert items["python"].priority == pytest.approx(0.3333, abs=1e-3)
    assert items["python"].name == "Python"  # canonical name from the overview
    assert [s.slug for s in items["python"].sources] == ["qa", "data"]
    # playwright: 0.6 × 2/3 = 0.4, no level → counts as 0
    assert items["playwright"].priority == pytest.approx(0.4)
    assert items["playwright"].bucket == "now"
    assert items["sql"].bucket == "covered"
    assert result.items[0].key == "playwright"
    assert any("no own level" in w for w in result.warnings)


def test_curriculum_warns_about_weights_and_unknown_profiles() -> None:
    overview = parse_overview("| Profile | Weight | Status |\n|---|---|---|\n| qa | 70 | active |\n"
                              "| ghost | 10 | active |\n")  # fmt: skip
    result = curriculum.compute([_profile(PROFILE, "qa"), _profile(PROFILE, "new")], overview)
    assert any("ghost" in w for w in result.warnings)
    assert any("not in the overview" in w for w in result.warnings)
    weighted = {w.slug: w for w in result.profiles}
    assert weighted["qa"].share == 1.0 and weighted["new"].status == "draft"
    # ghost has no folder and "new" is a draft: only qa counts, 70 % is not 100 %
    assert any("add up to 70 %" in w for w in result.warnings)


def test_curriculum_block_is_replaced_in_place() -> None:
    result = curriculum.compute([_profile(PROFILE, "qa")], parse_overview(OVERVIEW))
    block = curriculum.block(result, Texts(), "2026-11-23")
    once = curriculum.with_block(OVERVIEW, block)
    assert curriculum.BLOCK_START in once and "Playwright" in once
    again = curriculum.with_block(once, block.replace("2026-11-23", "2026-11-24"))
    assert again.count(curriculum.BLOCK_START) == 1 and "2026-11-24" in again
    assert again.startswith(OVERVIEW.rstrip())  # the owner's part stays


def test_overview_gets_only_new_rows() -> None:
    text = documents.add_to_overview(
        OVERVIEW, Texts(), [("qa", "QA", 50), ("web", "Web Dev", 20)],
        [LevelProposal(skill="py", level=1, reason="x"),
         LevelProposal(skill="Docker", level=1, reason="CV: containers")],
    )  # fmt: skip
    o = parse_overview(text)
    assert [(e.slug, e.weight, e.status) for e in o.entries][-1] == ("web", 20, "draft")
    assert len(o.entries) == 4
    assert [lv.name for lv in o.levels] == ["Python", "SQL", "Playwright", "Docker"]
    assert o.level_of()["python"].level == 2  # existing level untouched


def test_new_overview_parses() -> None:
    text = documents.add_to_overview("", Texts(), [("qa", "QA", 100)], [])
    assert [e.slug for e in parse_overview(text).entries] == ["qa"]


def draft(slug: str = "qa", action: str = "create", change: str = "created") -> ProfileDraft:
    return ProfileDraft(
        slug=slug, action=action, title="Test Automation Engineer", summary="Tests.",
        direction="Product companies.", fit=["QA background"], titles=["QA Engineer"],
        exclusions=["manual testing only"],
        skills=[SkillLine(skill=s, share="core" if i < 2 else "edge", reason="r")
                for i, s in enumerate(["Python", "Playwright", "SQL", "Git", "Jira"])],
        market="12 postings", gaps=["Playwright"], change=change, weight=60,
    )  # fmt: skip


def test_rendered_profile_parses_and_keeps_history() -> None:
    t = Texts()
    text = documents.render_profile(draft(), t, [], "- 2026-11-01 · #1 · created")
    p = parse_profile(text, "qa", "x")
    assert [s.name for s in p.skills][:2] == ["Python", "Playwright"] and not p.problems
    past = documents.history(text, t)
    again = documents.render_profile(draft(), t, past, "- 2026-11-23 · #2 · sharper")
    assert documents.history(again, t) == ["- 2026-11-01 · #1 · created",
                                           "- 2026-11-23 · #2 · sharper"]  # fmt: skip


def test_check_catches_slug_mistakes() -> None:
    out = output(draft("qa", "create"))
    assert any("exists already" in p for p in check(out, {"qa"}))
    assert any("no existing profile" in p for p in check(
        out.model_copy(update={"profiles": [draft("qa", "update")]}), set()))  # fmt: skip
    assert any("missing" in p for p in check(out, {"other"}))


def test_market_stats_count_primary_postings() -> None:
    index = {
        "a": {"title": "Senior QA Engineer (m/w/d) – Wien", "skills": ["Python", "python"]},
        "b": {"title": "QA Engineer", "skills": ["Python", "SQL"], "duplicateOf": "a"},
        "c": {"title": "Full-Stack Developer", "skills": ["SQL"], "goneAt": "x"},
    }
    stats = market_stats(index)
    assert stats["postings"] == 1 and stats["skills"] == [("Python", 1)]
    assert title_stem("Full-Stack Developer, Graz") == "full-stack developer"


# -- the workflow ---------------------------------------------------------------------------


class FakeLlm:
    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = outputs
        self.prompts: list[str] = []
        self.systems: list[str] = []

    def research(self, system, prompt, max_searches, effort):
        self.prompts.append(prompt)
        return "QA is in demand.\n\nSources: https://example.org", Usage(0.05, 2)

    def parse(self, system, prompt, schema, effort, images=None):
        self.prompts.append(prompt)
        self.systems.append(system)
        out = self.outputs.pop(0)
        assert isinstance(out, schema), (schema, out)
        return out, Usage(0.1, 0)


class FakeGh:
    def __init__(self, comments: list[str] | None = None) -> None:
        self.notes = comments or []

    def issue_comments(self, issue_id: str) -> list[dict[str, Any]]:
        return [{"id": str(i), "body": b, "createdAt": ""} for i, b in enumerate(self.notes)]


AGENT = """---
name: profile-designer
title: Profil-Designer
runner: workflow
workflow: profile-designer
board: tasks
sources: [ready/knowledge/Goals]
---

## Review checklist

1. Skills concrete?
"""


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    root = tmp_path / "vault"
    for rel, text in {
        MASTER_FILE: "positions: []\n",
        "ready/knowledge/Goals/Wishes.md": "More technical, closer to the product.\n",
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return Vault.open(root)


def context(vault: Vault, number: int = 1, gh: FakeGh | None = None) -> RunContext:
    from lifeos.agents.config import parse_agent

    agent: AgentDef = parse_agent(AGENT, "agents/profile-designer.md")
    ticket = Ticket("item", "issue", number, "Jobprofile erstellen", f"https://github.com/o/r/issues/{number}",
                    "OPEN", "Three profiles please.", "Todo")  # fmt: skip
    return RunContext(agent, None, ticket, vault, gh or FakeGh(), None, None)  # type: ignore[arg-type]


def output(*drafts: ProfileDraft, levels: list[LevelProposal] | None = None) -> DesignerOutput:
    return DesignerOutput(profiles=list(drafts), levels=levels or [],
                          notes=Notes(decisions=["QA first"], uncertain=[], please_check=[]),
                          feedback=[])  # fmt: skip


def passed() -> Review:
    return Review(checks=[Check(number=1, passed=True, reason="")], fixes=[])


def test_designer_creates_profiles_overview_and_curriculum(vault: Vault) -> None:
    llm = FakeLlm([
        output(draft("qa"), levels=[LevelProposal(skill="Python", level=2, reason="CV")]),
        passed(),
    ])  # fmt: skip
    run = make_profile_designer(llm_factory=lambda agent: llm)
    result = run(context(vault))
    profile = vault.resolve(f"{PROFILES_DIR}/qa/Profile.md").read_text(encoding="utf-8")
    assert "| Python | core | r |" in profile and "#1 · created" in profile
    package = vault.resolve(f"{PROFILES_DIR}/qa/review.yaml").read_text(encoding="utf-8")
    assert "ticket: o/r#1" in package
    overview = vault.resolve(OVERVIEW_FILE).read_text(encoding="utf-8")
    assert "`qa`" in overview and curriculum.BLOCK_START in overview
    assert vault.resolve(RESEARCH_FILE).is_file()
    assert "closer to the product" in llm.systems[0]  # sources: from the agent file
    assert result.paths[0] == PROFILES_DIR and "Test Automation Engineer" in result.summary
    # the new profile is a draft until the owner activates it: the curriculum is empty
    assert CareerFiles(vault).overview().entries[0].status == "draft"


def test_designer_revision_keeps_history_and_review_fixes(vault: Vault) -> None:
    first = FakeLlm([output(draft("qa")), passed()])
    make_profile_designer(llm_factory=lambda a: first)(context(vault))
    sharper = draft("qa", "update", "Firma X sucht auch Docker")
    fixes = Review(checks=[Check(number=1, passed=False, reason="vague")], fixes=["be concrete"])
    second = FakeLlm([output(draft("qa", "keep", "")), fixes, output(sharper), passed()])
    result = make_profile_designer(llm_factory=lambda a: second)(context(vault, 2))
    profile = vault.resolve(f"{PROFILES_DIR}/qa/Profile.md").read_text(encoding="utf-8")
    assert "#1 · created" in profile and "#2 · Firma X sucht auch Docker" in profile
    assert "be concrete" in result.report


def test_service_lists_profiles_and_refreshes_the_block(vault: Vault) -> None:
    folder = vault.resolve(f"{PROFILES_DIR}/qa")
    folder.mkdir(parents=True)
    (folder / "Profile.md").write_text(PROFILE, encoding="utf-8")
    vault.resolve(OVERVIEW_FILE).write_text(OVERVIEW, encoding="utf-8")
    commits: list[tuple[list[str], str]] = []
    service = CareerService(vault, committer=lambda paths, msg: commits.append((paths, msg)))
    data = service.overview()
    assert data["items"][0]["name"] == "Playwright"
    assert service.refresh() == {"written": True}
    assert commits and curriculum.BLOCK_START in vault.resolve(OVERVIEW_FILE).read_text("utf-8")
