"""Data of a job application: personal data, the master CV and one draft.

The master CV (vault, YAML) holds every position, education entry and bullet with a
stable id. A draft only *selects* from it: positions and bullets by id, titles from the
allowed variants. Companies, places and periods always come from the master, so the
writer cannot invent them. Facts the owner gives in review comments reach the master
first (``master.py``), so the draft can use them in the same run.

``check: true`` marks an entry or bullet that still has to be confirmed by the owner;
``origin`` says where a fact came from (e.g. the review comment that added it).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

MONTHS_DE = (
    "Jänner", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)  # fmt: skip


def german_date(day: date) -> str:
    """Austrian long date: "17. November 2026"."""
    return f"{day.day}. {MONTHS_DE[day.month - 1]} {day.year}"


class DraftError(ValueError):
    """A draft refers to something the master CV does not contain."""


# -- vault data ----------------------------------------------------------------------


class Personal(BaseModel):
    name: str
    street: str = ""
    city: str = ""
    phone: str = ""
    phone_link: str = ""
    email: str = ""
    github: str = ""
    linkedin: str = ""
    photo: str | None = None


class Bullet(BaseModel):
    id: str
    text: str
    url: str | None = None
    check: bool = False
    origin: str | None = None


class Position(BaseModel):
    id: str
    title: str
    title_variants: list[str] = Field(default_factory=list)
    company: str
    place: str = ""
    period: str
    bullets: list[Bullet] = Field(default_factory=list)
    check: bool = False
    origin: str | None = None


class Education(BaseModel):
    id: str
    institution: str
    degree: str
    detail: str = ""
    period: str
    inline: bool = False
    bullets: list[Bullet] = Field(default_factory=list)
    check: bool = False
    origin: str | None = None


class SkillGroup(BaseModel):
    label: str = Field(description="e.g. 'Softwareentwicklung'")
    items: str = Field(description="comma-separated, e.g. 'Python, SQL, Git'")


class Skills(BaseModel):
    technical: str = ""
    groups: list[SkillGroup] = Field(default_factory=list)  # shown instead of `technical`
    interests: str = ""
    languages: str = ""
    origin: str | None = None


class MasterCv(BaseModel):
    headline_examples: list[str] = Field(default_factory=list)
    profile_examples: list[str] = Field(default_factory=list)
    positions: list[Position]
    education: list[Education] = Field(default_factory=list)
    skills: Skills = Field(default_factory=Skills)


# -- a draft (written by the AI, reviewed by the user) -------------------------------------


class ChosenBullet(BaseModel):
    base: str = Field(description="id of the master bullet this point is based on")
    text: str = Field(description="the point as it appears in this CV (may be reworded)")


class ChosenPosition(BaseModel):
    id: str = Field(description="id of a master position")
    title: str = Field(description="the master title or one of its title_variants")
    bullets: list[ChosenBullet]


class ChosenEducation(BaseModel):
    id: str = Field(description="id of a master education entry")
    bullets: list[ChosenBullet] = Field(default_factory=list)


class CvSelection(BaseModel):
    headline: str = Field(description="line under the name, tailored to the position")
    profile: str = Field(description="one or two sentences between the header rules")
    positions: list[ChosenPosition]
    education: list[ChosenEducation]
    technical_skills: str = Field(default="", description="empty = master value")
    skill_groups: list[SkillGroup] = Field(
        default_factory=list,
        description="technical skills in groups, one line each; empty = master groups",
    )


class Letter(BaseModel):
    recipient: list[str] = Field(description="address lines: company, 'z. Hd. …', street, city")
    subject: str
    salutation: str = Field(description="e.g. 'Sehr geehrte Frau Kreuzer,'")
    paragraphs: list[str]
    closing: str = "Mit freundlichen Grüßen"


class Draft(BaseModel):
    letter: Letter
    cv: CvSelection


# -- resolving a draft against the master --------------------------------------------------


def _bullets(chosen: list[ChosenBullet], master: list[Bullet], where: str) -> list[dict]:
    known = {b.id: b for b in master}
    out = []
    for item in chosen:
        base = known.get(item.base)
        if base is None:
            raise DraftError(f"{where}: unknown bullet id {item.base!r}")
        out.append({"text": item.text, "url": base.url})
    return out


def resolve_cv(cv: CvSelection, master: MasterCv) -> dict:
    """The CV as the template needs it; fails on anything not backed by the master."""
    positions = {p.id: p for p in master.positions}
    education = {e.id: e for e in master.education}
    out_positions = []
    for chosen in cv.positions:
        pos = positions.get(chosen.id)
        if pos is None:
            raise DraftError(f"unknown position id {chosen.id!r}")
        if chosen.title not in (pos.title, *pos.title_variants):
            raise DraftError(f"title {chosen.title!r} is not allowed for position {pos.id!r}")
        out_positions.append(
            {
                "title": chosen.title,
                "company": pos.company,
                "place": pos.place,
                "period": pos.period,
                "bullets": _bullets(chosen.bullets, pos.bullets, pos.id),
            }
        )
    out_education = []
    for chosen in cv.education:
        edu = education.get(chosen.id)
        if edu is None:
            raise DraftError(f"unknown education id {chosen.id!r}")
        out_education.append(
            edu.model_dump(exclude={"bullets", "id", "check", "origin"})
            | {"bullets": _bullets(chosen.bullets, edu.bullets, edu.id)}
        )
    skills = master.skills.model_dump(exclude={"origin"})
    if cv.technical_skills.strip():
        skills["technical"] = cv.technical_skills.strip()
    if cv.skill_groups:
        skills["groups"] = [g.model_dump() for g in cv.skill_groups]
    return {
        "headline": cv.headline,
        "profile": cv.profile,
        "positions": out_positions,
        "education": out_education,
        "skills": skills,
    }
