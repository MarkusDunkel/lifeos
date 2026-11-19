from __future__ import annotations

import yaml
from test_applications_render import MASTER

from lifeos.modules.applications.master import MasterUpdate, apply_updates, dump, merge
from lifeos.modules.applications.model import SkillGroup

ORIGINS = {1: "o/r#1 · CV F1 · 2026-01-02", 2: "o/r#1 · comment 2 · 2026-01-02"}


def data() -> dict:
    raw = yaml.safe_load(MASTER)
    raw["notes"] = "Kept as it is."
    raw["positions"][0]["bullets"][1]["check"] = True  # acme-sql still to be confirmed
    return raw


def test_owner_facts_reach_the_master_with_their_origin() -> None:
    updates = [
        MasterUpdate(comment=1, op="set_period", entry="acme", text="2024 – 2026"),
        MasterUpdate(comment=1, op="add_bullet", entry="acme", bullet="acme-kafka",
                     text="Streaming mit Kafka"),
        MasterUpdate(comment=2, op="confirm", bullet="acme-sql"),
        MasterUpdate(comment=2, op="set_detail", entry="tu", text="mit Auszeichnung"),
        MasterUpdate(comment=2, op="add_title_variant", entry="acme", text="Data Engineer (Lead)"),
        MasterUpdate(comment=2, op="set_skill_groups",
                     groups=[SkillGroup(label="Daten", items="SQL, Kafka")]),
    ]  # fmt: skip
    applied = apply_updates(data(), updates, ORIGINS)

    assert applied.rejected == [] and len(applied.done) == 6
    acme = applied.master.positions[0]
    assert acme.period == "2024 – 2026" and acme.origin == ORIGINS[2]  # the last change
    assert acme.title_variants[-1] == "Data Engineer (Lead)"
    new = acme.bullets[-1]
    assert (new.id, new.text, new.origin) == ("acme-kafka", "Streaming mit Kafka", ORIGINS[1])
    assert acme.bullets[1].check is False  # confirmed
    assert applied.master.education[0].detail == "mit Auszeichnung"
    assert applied.master.skills.groups == [SkillGroup(label="Daten", items="SQL, Kafka")]
    assert applied.done[0] == f"{ORIGINS[1]}: period of acme → 2024 – 2026"

    text = dump(applied.data)
    assert "notes: Kept as it is." in text  # unknown fields stay
    assert "check" not in text  # removed with the confirmation
    assert text.index("headline_examples") < text.index("positions")  # order kept


def test_updates_not_backed_by_an_owner_comment_or_invalid_are_rejected() -> None:
    raw = data()
    updates = [
        MasterUpdate(comment=7, op="set_period", entry="acme", text="seit 1990"),  # no comment 7
        MasterUpdate(comment=1, op="set_period", entry="nope", text="2020"),
        MasterUpdate(comment=1, op="edit_bullet", bullet="nope", text="x"),
        MasterUpdate(comment=1, op="add_position", entry="acme", text="X", company="Y",
                     period="2020"),  # the id exists
        MasterUpdate(comment=1, op="set_skills", skill_field="secret", text="x"),
        MasterUpdate(comment=1, op="remove_bullet", bullet="acme-pipelines"),
    ]  # fmt: skip
    applied = apply_updates(raw, updates, ORIGINS)
    assert len(applied.rejected) == 5 and len(applied.done) == 1
    assert "comment 7 is not from the owner" in applied.rejected[0]
    assert [b.id for b in applied.master.positions[0].bullets] == ["acme-sql"]
    assert raw["positions"][0]["period"] == "seit 2024"  # the input is not changed


def test_new_entries_and_a_later_update_wins() -> None:
    first = [MasterUpdate(comment=1, op="set_period", entry="acme", text="2024 – 2025")]
    later = [
        MasterUpdate(comment=1, op="set_period", entry="acme", text="2024 – 2026"),
        MasterUpdate(comment=2, op="add_education", entry="fh", text="MA Recht",
                     company="FH Beispiel", period="seit 2026"),
    ]  # fmt: skip
    applied = apply_updates(data(), merge(first, later), ORIGINS)
    assert applied.master.positions[0].period == "2024 – 2026"
    fh = applied.master.education[0]
    assert (fh.id, fh.institution, fh.degree, fh.origin) == ("fh", "FH Beispiel", "MA Recht",
                                                            ORIGINS[2])  # fmt: skip


def test_dump_is_readable_and_round_trips() -> None:
    raw = data()
    raw["notes"] = "Line one.\nLine two.\n"
    text = dump(raw)
    assert text.startswith("headline_examples:\n  - Data Engineering\n\nprofile_examples:")
    assert "notes: |\n  Line one.\n  Line two.\n" in text
    assert "positions:\n  - id: acme\n" in text  # lists indented, no blank line after the key
    assert "\n\nskills:\n" in text
    assert yaml.safe_load(text) == raw
