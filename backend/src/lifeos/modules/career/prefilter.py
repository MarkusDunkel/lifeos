"""Rule prefilter: how well a posting fits each active profile, and where it is.

Cheap and deterministic, for every posting; the AI job matcher only looks at postings that
pass. Per posting and active profile:

- **skills** – the profile's skills found in the posting (its skill list and title count
  fully, the description 0.7), weighted by share (core 3, secondary 2, edge 1) and capped at
  ``SKILL_CAP`` points, so four core skills make a full match,
- **title** – the best share of words of a typical title of the profile that the posting's
  title contains,
- ``fit = 60 % skills + 40 % title`` (0–100); the posting's fit is that of its best profile.

Where: the distance to each home (``homes:`` in ``Career/CV/personal.yaml``, with a label and
a radius) – a posting within the radius of a home gets that home's label; postings that say
they are fully remote get "remote". A posting passes the prefilter when it is near a home or
remote and its fit reaches ``PASS_FIT``. Salary is deliberately not part of it.

Results are cached in ``.lifeos/jobsearch/fit.json`` together with a signature of the
profiles, the overview and the homes; a change of any of them recalculates everything.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
from dataclasses import asdict, dataclass, field
from typing import Any

import yaml

from lifeos.core.vault import STATE, Vault, write_atomic
from lifeos.modules.applications.profile import PERSONAL_FILE
from lifeos.modules.career.profiles import (
    CareerFiles,
    Overview,
    Profile,
    skill_key,
)
from lifeos.modules.jobsearch.model import Posting

CACHE_FILE = f"{STATE}/jobsearch/fit.json"
SKILL_CAP = 12.0  # share points for a full skill match (four core skills)
SKILL_PART, TITLE_PART = 0.6, 0.4
DESCRIPTION_WEIGHT = 0.7
PASS_FIT = 35
VERSION = 2  # bump when the scoring changes

REMOTE = re.compile(
    r"\b(100\s?%\s?remote|full[ -]?remote|fully remote|remote[- ]first|komplett remote|"
    r"vollständig remote|ausschließlich remote|rein remote|remote only)\b",
    re.I,
)
STOP = {"m/w/d", "w/m/d", "m/f/d", "f/m/d", "mwd", "all", "genders", "und", "and", "the",
        "für", "for", "mit", "with", "später", "-", "–", "/"}  # fmt: skip


@dataclass
class Home:
    label: str
    lat: float
    lon: float
    radius_km: float = 40.0
    comfort_minutes: float = 30.0  # commute without penalty
    max_minutes: float = 75.0  # half the score at this commute; longer is flagged


@dataclass
class Near:
    label: str
    km: float
    lat: float | None = None  # the posting's location nearest to the home
    lon: float | None = None


@dataclass
class ProfileFit:
    slug: str
    title: str
    fit: int
    skills: int  # 0–100
    title_match: int  # 0–100
    matched: list[str] = field(default_factory=list)


@dataclass
class Fit:
    fit: int  # of the best profile
    profile: str | None
    profile_title: str | None
    profiles: list[ProfileFit] = field(default_factory=list)
    homes: list[Near] = field(default_factory=list)  # within the radius, nearest first
    nearest: Near | None = None  # nearest home, also when outside every radius
    remote: bool = False
    passed: bool = False


# -- reading the inputs ----------------------------------------------------------------------


def load_homes(vault: Vault) -> list[Home]:
    path = vault.resolve(PERSONAL_FILE)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    out = []
    for raw in data.get("homes") or []:
        try:
            out.append(Home(str(raw["label"]), float(raw["lat"]), float(raw["lon"]),
                            float(raw.get("radius_km", 40)),
                            float(raw.get("comfort_minutes", 30)),
                            float(raw.get("max_minutes", 75))))  # fmt: skip
        except (KeyError, TypeError, ValueError):
            continue
    return out


def terms(name: str, aliases: list[str]) -> list[str]:
    """Spellings to look for: 'Databricks / Spark' → Databricks, Spark; 'ERP-Integration
    (SAP/Odoo)' → ERP-Integration, SAP, Odoo; plus the overview's aliases."""
    out: list[str] = []
    inner = re.findall(r"\(([^)]*)\)", name)
    outer = re.sub(r"\([^)]*\)", " ", name)
    for part in re.split(r"\s/\s|,", outer):
        out.append(part.strip())
    for group in inner:
        out += [p.strip() for p in re.split(r"[/,]", group)]
    out += aliases
    seen, clean = set(), []
    for t in out:
        t = t.strip("`* ")
        if len(t) >= 2 and t.casefold() not in seen:
            seen.add(t.casefold())
            clean.append(t)
    return clean


def _pattern(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w])" + re.escape(term) + r"(?![\w])", re.I)


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _words(text: str) -> list[str]:
    text = re.sub(r"\(.*?\)", " ", text.casefold())
    return [w for w in re.split(r"[\s,;:|()]+", text) if w and w not in STOP]


# -- scoring --------------------------------------------------------------------------------


class Scorer:
    def __init__(self, profiles: list[Profile], overview: Overview, homes: list[Home]) -> None:
        active = {e.slug for e in overview.entries if e.status == "active"}
        self.profiles = [p for p in profiles if p.slug in active]
        self.homes = homes
        aliases: dict[str, list[str]] = {}
        for lvl in overview.levels:
            aliases[skill_key(lvl.name)] = lvl.aliases
        canonical = overview.aliases()
        self.patterns: dict[str, list[re.Pattern[str]]] = {}
        for p in self.profiles:
            for need in p.skills:
                key = canonical.get(skill_key(need.name), skill_key(need.name))
                if need.name not in self.patterns:
                    found = terms(need.name, aliases.get(key, []))
                    self.patterns[need.name] = [_pattern(t) for t in found]

    def _where(self, posting: Posting, fit: Fit) -> None:
        text = " ".join([posting.title, *(loc.city for loc in posting.locations),
                         posting.description[:4000]])  # fmt: skip
        fit.remote = bool(REMOTE.search(text)) or any(
            loc.city.casefold() in ("remote", "homeoffice", "home office")
            for loc in posting.locations
        )
        best: Near | None = None
        near: dict[str, Near] = {}
        for loc in posting.locations:
            if loc.lat is None or loc.lon is None:
                continue
            for home in self.homes:
                km = round(haversine(home.lat, home.lon, loc.lat, loc.lon), 1)
                if best is None or km < best.km:
                    best = Near(home.label, km, loc.lat, loc.lon)
                if km <= home.radius_km and (home.label not in near or km < near[home.label].km):
                    near[home.label] = Near(home.label, km, loc.lat, loc.lon)
        fit.nearest = best
        fit.homes = sorted(near.values(), key=lambda n: n.km)

    def score(self, posting: Posting) -> Fit:
        fit = Fit(0, None, None)
        self._where(posting, fit)
        listed = " · ".join(posting.skills)
        title = posting.title
        description = posting.description
        title_words = set(_words(title))
        for p in self.profiles:
            points, total, matched = 0.0, 0.0, []
            for need in p.skills:
                total += need.share
                pats = self.patterns.get(need.name, [])
                if any(x.search(listed) or x.search(title) for x in pats):
                    points += need.share
                    matched.append(need.name)
                elif any(x.search(description) for x in pats):
                    points += need.share * DESCRIPTION_WEIGHT
                    matched.append(need.name)
            skills = min(points / min(SKILL_CAP, total or 1), 1.0) if total else 0.0
            best_title = 0.0
            for t in [p.title, *p.titles]:
                for variant in re.split(r"\s/\s", t):
                    words = _words(variant)
                    if words:
                        share = sum(w in title_words for w in words) / len(words)
                        best_title = max(best_title, share)
            value = round(100 * (SKILL_PART * skills + TITLE_PART * best_title))
            fit.profiles.append(ProfileFit(p.slug, p.title, value, round(100 * skills),
                                           round(100 * best_title), matched))  # fmt: skip
        fit.profiles.sort(key=lambda f: -f.fit)
        if fit.profiles:
            top = fit.profiles[0]
            fit.fit, fit.profile, fit.profile_title = top.fit, top.slug, top.title
        fit.passed = fit.fit >= PASS_FIT and (bool(fit.homes) or fit.remote or not self.homes)
        return fit


# -- the cache ------------------------------------------------------------------------------


def fit_from(data: dict[str, Any]) -> Fit:
    fit = Fit(**{k: v for k, v in data.items() if k not in ("profiles", "homes", "nearest")})
    fit.profiles = [ProfileFit(**p) for p in data.get("profiles", [])]
    fit.homes = [Near(**n) for n in data.get("homes", [])]
    fit.nearest = Near(**data["nearest"]) if data.get("nearest") else None
    return fit


class FitIndex:
    """Fit of every posting, calculated on demand and cached until the inputs change."""

    def __init__(self, vault: Vault, store: Any) -> None:
        self.vault = vault
        self.store = store
        self.files = CareerFiles(vault)
        self._lock = threading.Lock()

    def _inputs(self) -> tuple[str, Scorer]:
        profiles = self.files.profiles()
        overview_text = self.files.overview_text()
        homes = load_homes(self.vault)
        raw = json.dumps({
            "v": VERSION,
            "profiles": [(p.slug, p.title, p.titles, [asdict(s) for s in p.skills])
                         for p in profiles],
            "overview": overview_text,
            "homes": [asdict(h) for h in homes],
        }, sort_keys=True, ensure_ascii=False)  # fmt: skip
        signature = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        overview = self.files.overview()
        return signature, Scorer(profiles, overview, homes)

    def homes(self) -> list[Home]:
        return load_homes(self.vault)

    def profiles(self) -> list[dict[str, str]]:
        _, scorer = self._inputs()
        return [{"slug": p.slug, "title": p.title} for p in scorer.profiles]

    def warm(self) -> None:
        """Calculate the fit of every active posting in a background thread (the first full
        calculation takes a while)."""

        def work() -> None:
            try:
                index = self.store.index()
                self.all([k for k, e in index.items() if not e.get("goneAt")])
            except Exception:  # a failed warm-up only costs time on the next request
                logging.getLogger("lifeos.career").exception("warming the fit cache failed")

        threading.Thread(target=work, name="fit-warm", daemon=True).start()

    def all(self, keys: list[str]) -> dict[str, Fit]:
        """The fit of ``keys`` (calculating what the cache lacks)."""
        with self._lock:
            signature, scorer = self._inputs()
            path = self.vault.resolve(CACHE_FILE)
            try:
                cache = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cache = {}
            if cache.get("signature") != signature:
                cache = {"signature": signature, "fits": {}}
            fits: dict[str, Any] = cache["fits"]
            changed = False
            for key in keys:
                if key in fits:
                    continue
                posting = self.store.posting(key)
                if posting is None:
                    continue
                fits[key] = asdict(scorer.score(posting))
                changed = True
            if changed:
                write_atomic(path, json.dumps(cache, ensure_ascii=False))
            return {k: fit_from(fits[k]) for k in keys if k in fits}
