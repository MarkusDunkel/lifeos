"""Recognise postings that describe the same job.

Two postings are the same job when the company and the title match after
normalisation and they share a city (or one of them has no city). Typical cases:
a company publishes the same position twice, or re-publishes it under a new id
after the old posting went offline.
"""

from __future__ import annotations

import re
import unicodedata

from lifeos.modules.jobsearch.model import Posting

# "(m/w/d)", "(w/m/x)", "(all genders)", "(*all genders)", "(m/f/d)", "(gn)" …
_GENDER_PARENS = re.compile(
    r"\(\s*(?:\*?\s*all genders?|alle geschlechter|gn|[mwfdx](?:\s*[/|,]\s*[mwfdx]){1,3})\s*\)",
    re.I,
)
_GENDER_BARE = re.compile(r"\b(?:[mwfdx]\s*/\s*[mwfdx]\s*/\s*[mwfdx]|all genders?)\b", re.I)
# Gendered word endings: Entwickler:in, Entwickler*innen, Entwickler_in, Entwickler/-in
_GENDER_SUFFIX = re.compile(r"(?<=\w)(?::|\*|_|/-?)in(?:nen)?\b", re.I)
_LEGAL_FORMS = {
    "gmbh", "gesmbh", "mbh", "ges", "gesellschaft", "ag", "kg", "og", "co", "se", "eu",
    "ek", "kgaa", "nfg", "inc", "ltd", "llc", "bv", "sa", "srl", "und",
}  # fmt: skip


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[^\w]+", " ", text).strip()


def normalize_title(title: str) -> str:
    title = _GENDER_PARENS.sub(" ", title)
    title = _GENDER_BARE.sub(" ", title)
    title = _GENDER_SUFFIX.sub("", title)
    return " ".join(_plain(title).split())


def normalize_company(company: str) -> str:
    text = unicodedata.normalize("NFKC", company).lower().replace("&", " ")
    text = text.replace(".", "")  # "ges.m.b.h." → "gesmbh", "e.u." → "eu"
    words = [w for w in _plain(text).split() if w not in _LEGAL_FORMS]
    return " ".join(words)


def match_key(posting: Posting) -> str:
    return f"{normalize_company(posting.company)}|{normalize_title(posting.title)}"


def cities(posting: Posting) -> list[str]:
    return sorted({_plain(loc.city) for loc in posting.locations if loc.city})


def same_place(a: list[str], b: list[str]) -> bool:
    return not a or not b or bool(set(a) & set(b))
