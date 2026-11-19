from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pytest
from pypdf import PdfReader

from lifeos.core.vault import Vault
from lifeos.modules.applications.model import (
    ChosenBullet,
    ChosenEducation,
    ChosenPosition,
    CvSelection,
    Draft,
    DraftError,
    Letter,
    german_date,
    resolve_cv,
)
from lifeos.modules.applications.profile import (
    MASTER_FILE,
    PERSONAL_FILE,
    ProfileError,
    load_profile,
)

PERSONAL = """\
name: Erika Muster
street: Musterweg 1
city: 1010 Wien
phone: +43 1 234567
phone_link: "+43-1-234567"
email: erika@example.org
"""

MASTER = """\
headline_examples: [Data Engineering]
profile_examples: [Ingenieurin mit Erfahrung in Daten.]
positions:
  - id: acme
    title: Data Engineer
    title_variants: [Dateningenieurin]
    company: ACME GmbH
    place: Wien
    period: seit 2024
    bullets:
      - {id: acme-pipelines, text: Aufbau von Datenpipelines}
      - {id: acme-sql, text: SQL-Modellierung}
education:
  - id: tu
    institution: TU Wien
    degree: MSc Informatik
    period: 2018 – 2020
    bullets:
      - {id: tu-thesis, text: "Masterarbeit: Graphen", url: "https://example.org/thesis"}
skills:
  technical: Python, SQL
  languages: Deutsch, Englisch
"""


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    root = tmp_path / "vault"
    cv_dir = root / Path(PERSONAL_FILE).parent
    cv_dir.mkdir(parents=True)
    (root / PERSONAL_FILE).write_text(PERSONAL, encoding="utf-8")
    (root / MASTER_FILE).write_text(MASTER, encoding="utf-8")
    return Vault.open(root)


def draft(paragraphs: int = 3, title: str = "Data Engineer", bullet: str = "acme-sql") -> Draft:
    return Draft(
        letter=Letter(
            recipient=["Beispiel AG", "z. Hd. Herrn Max Beispiel"],
            subject="Bewerbung als Data Engineer",
            salutation="Sehr geehrter Herr Beispiel,",
            paragraphs=[f"Absatz {i} über Daten und Pipelines." for i in range(paragraphs)],
        ),
        cv=CvSelection(
            headline="Data Engineering & Analytics",
            profile="Ingenieurin mit Erfahrung in Daten.",
            positions=[
                ChosenPosition(
                    id="acme",
                    title=title,
                    bullets=[ChosenBullet(base=bullet, text="SQL-Modellierung für Analysen")],
                )
            ],
            education=[
                ChosenEducation(
                    id="tu", bullets=[ChosenBullet(base="tu-thesis", text="Masterarbeit")]
                )
            ],
        ),
    )


def test_profile_loads_and_checks_ids(vault: Vault) -> None:
    profile = load_profile(vault)
    assert profile.personal.name == "Erika Muster" and profile.photo is None
    assert [p.id for p in profile.master.positions] == ["acme"]

    path = vault.resolve(MASTER_FILE)
    path.write_text(MASTER.replace("acme-sql", "acme-pipelines"), encoding="utf-8")
    with pytest.raises(ProfileError, match="duplicate bullet ids"):
        load_profile(vault)


def test_missing_profile_is_reported(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(ProfileError, match="missing"):
        load_profile(Vault.open(tmp_path / "empty"))


def test_resolve_takes_facts_from_the_master(vault: Vault) -> None:
    cv = resolve_cv(draft(title="Dateningenieurin").cv, load_profile(vault).master)
    position = cv["positions"][0]
    assert (position["company"], position["period"], position["title"]) == (
        "ACME GmbH",
        "seit 2024",
        "Dateningenieurin",
    )
    assert position["bullets"] == [{"text": "SQL-Modellierung für Analysen", "url": None}]
    assert cv["education"][0]["bullets"][0]["url"] == "https://example.org/thesis"
    assert cv["skills"]["technical"] == "Python, SQL"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"title": "Chief Data Officer"}, "not allowed"),
        ({"bullet": "acme-invented"}, "unknown bullet"),
    ],
)
def test_resolve_rejects_what_the_master_does_not_back(vault: Vault, kwargs, message) -> None:
    with pytest.raises(DraftError, match=message):
        resolve_cv(draft(**kwargs).cv, load_profile(vault).master)


def test_resolve_rejects_unknown_position(vault: Vault) -> None:
    d = draft()
    d.cv.positions[0].id = "other"
    with pytest.raises(DraftError, match="unknown position"):
        resolve_cv(d.cv, load_profile(vault).master)


def test_german_date() -> None:
    assert german_date(date(2026, 1, 5)) == "5. Jänner 2026"
    assert german_date(date(2026, 11, 17)) == "17. November 2026"


typst = pytest.importorskip("typst")


def test_render_makes_one_page_letter_and_cv(vault: Vault) -> None:
    from lifeos.modules.applications.render import render

    result = render(draft(), load_profile(vault), on=date(2026, 11, 17))
    assert result.pages == {"letter": 1, "cv": 1}
    letter = PdfReader(io.BytesIO(result.letter)).pages[0].extract_text()
    assert "Sehr geehrter Herr Beispiel" in letter and "17. November 2026" in letter
    cv = PdfReader(io.BytesIO(result.cv)).pages[0].extract_text()
    assert "ACME GmbH" in cv and "SQL-Modellierung" in cv


def test_render_refuses_a_letter_longer_than_a_page(vault: Vault) -> None:
    from lifeos.modules.applications.render import RenderError, render

    long = draft()
    long.letter.paragraphs = ["Sehr langer Absatz. " * 60 for _ in range(6)]
    with pytest.raises(RenderError, match="cover letter has 2 pages"):
        render(long, load_profile(vault))


def test_render_fits_a_typical_letter_with_wide_paragraph_gaps_on_one_page(vault: Vault) -> None:
    # The guide asks for five paragraphs and about 350 to 400 words; the wider gap between
    # paragraphs (lifeos-board#33) must still leave such a letter on one page.
    from lifeos.modules.applications.render import render

    typical = draft()
    # 12 words, about 7.5 characters per word like a real German letter (spaces included).
    # Repeating one sentence fills lines less evenly than real text, so stay at 360 words.
    sentence = (
        "Ich habe Datenmodelle in SQL entwickelt und Anwendungen mit den Fachbereichen verbessert. "
    )
    typical.letter.paragraphs = [sentence * 6 for _ in range(5)]
    assert sum(len(p.split()) for p in typical.letter.paragraphs) == 360
    assert render(typical, load_profile(vault)).pages["letter"] == 1


def test_skill_groups_are_one_line_each_in_pdf_and_markdown(vault: Vault) -> None:
    from lifeos.modules.applications.model import SkillGroup
    from lifeos.modules.applications.render import render
    from lifeos.modules.applications.review import cv_markdown

    d = draft()
    d.cv.skill_groups = [SkillGroup(label="Daten", items="SQL, dbt"),
                         SkillGroup(label="Betrieb", items="Linux, Docker")]  # fmt: skip
    profile = load_profile(vault)
    cv = PdfReader(io.BytesIO(render(d, profile).cv)).pages[0].extract_text()
    assert "Technische Kenntnisse:" in cv and "Daten: SQL, dbt" in cv
    assert "Betrieb: Linux, Docker" in cv and "Python, SQL" not in cv  # replaces the line
    md = cv_markdown(resolve_cv(d.cv, profile.master))
    assert "- **technical:**\n  - Daten: SQL, dbt\n  - Betrieb: Linux, Docker" in md
    assert "Python, SQL" not in md and "- **languages:** Deutsch, Englisch" in md
