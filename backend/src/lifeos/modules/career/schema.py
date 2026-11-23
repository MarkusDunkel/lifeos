"""Structured output of the profile designer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from lifeos.modules.applications.writer import FeedbackItem, Notes


class SkillLine(BaseModel):
    skill: str = Field(description="the skill as it is usually named in postings, e.g. 'Python'")
    share: Literal["core", "secondary", "edge"] = Field(
        description="core = needed a lot in this role, secondary = needed regularly, "
        "edge = nice to have"
    )
    reason: str = Field(description="why, with evidence (market numbers, postings), one sentence")


class ProfileDraft(BaseModel):
    slug: str = Field(
        description="folder name, kebab-case ASCII, stable: reuse the slug of an existing profile"
    )
    action: Literal["create", "update", "keep"] = Field(
        description="create a new profile, update (sharpen) an existing one, or keep it unchanged"
    )
    title: str = Field(description="name of the target role, e.g. 'Test Automation Engineer'")
    summary: str = Field(description="two or three sentences: the role in short")
    direction: str = Field(description="where this profile leads; what kind of companies/products")
    fit: list[str] = Field(description="why it fits the owner, from the CV and the owner's wishes")
    titles: list[str] = Field(description="typical job titles to search for")
    exclusions: list[str] = Field(description="what this profile is not (to tell it apart)")
    skills: list[SkillLine] = Field(description="8–20 skills, the most important first")
    market: str = Field(
        description="market evidence in Markdown: numbers from <market>, findings of the "
        "research with source URLs"
    )
    gaps: list[str] = Field(description="what the owner lacks for this role today")
    change: str = Field(
        description="one line for the profile's history: what changed and why (occasion, e.g. "
        "the owner's comment or a finding); for a new profile 'created' plus the reason; "
        "empty for keep"
    )
    weight: float = Field(
        description="proposed weight in percent for a NEW profile (the owner decides); 0 for "
        "existing ones"
    )


class LevelProposal(BaseModel):
    skill: str
    level: int = Field(ge=0, le=3, description="0 none, 1 basics, 2 used in projects, 3 strong")
    reason: str = Field(description="evidence from the master CV, a few words")


class DesignerOutput(BaseModel):
    profiles: list[ProfileDraft] = Field(
        description="every existing profile (keep/update) and any new one (create)"
    )
    levels: list[LevelProposal] = Field(
        description="own level for each skill of the profiles that has no level in the "
        "overview yet; empty when all have one"
    )
    notes: Notes
    feedback: list[FeedbackItem] = Field(
        description="one item per comment in <owner_feedback>, in order; empty without feedback"
    )
