"""Job postings as exchanged between the PC importer and the server.

A posting is source-neutral: ``source`` names the job board (e.g. ``devjobs``) and
``id`` is the board's own identifier, so further sources can be added later.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

SOURCE_PATTERN = re.compile(r"^[a-z0-9-]{1,32}$")
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class Location(BaseModel):
    city: str = ""
    region: str = ""
    country: str = ""
    lat: float | None = None
    lon: float | None = None


class Salary(BaseModel):
    """Minimum salary as published (devjobs.at shows "ab …")."""

    amount: float
    unit: str = "YEAR"  # YEAR | MONTH | HOUR
    currency: str = "EUR"

    def yearly(self) -> float:
        """Austrian salaries are paid 14 times a year; hourly values assume 40 h/week."""
        if self.unit == "MONTH":
            return self.amount * 14
        if self.unit == "HOUR":
            return self.amount * 40 * 52
        return self.amount


class Posting(BaseModel):
    source: str
    id: str
    url: str
    title: str
    company: str = ""
    locations: list[Location] = Field(default_factory=list)
    salary: Salary | None = None
    employment_type: list[str] = Field(default_factory=list)
    date_posted: str | None = None
    valid_through: str | None = None
    skills: list[str] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)
    industry: str = ""
    description: str = ""  # Markdown

    @field_validator("source")
    @classmethod
    def _source(cls, value: str) -> str:
        if not SOURCE_PATTERN.match(value):
            raise ValueError("invalid source")
        return value

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not ID_PATTERN.match(value):
            raise ValueError("invalid id")
        return value

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"

    def place(self) -> str:
        return ", ".join(dict.fromkeys(loc.city for loc in self.locations if loc.city))
