"""The job search dashboard: funnel, demand per profile, profile × place, AI quality, gaps.

Everything is derived from what already exists – the posting index, the prefilter cache,
the AI assessments, the commute cache and the career overview – so building it is cheap.
"Good fit" means a prefilter fit of at least :data:`GOOD_FIT` to the best profile.
"""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from lifeos.modules.career.profiles import CareerFiles, skill_key
from lifeos.modules.jobsearch.store import JobStore, is_primary

GOOD_FIT = 50
TOP_CITIES = 5
TOP_SKILLS = 15
REMOTE = "Remote"


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 1) if values else None


def build_dashboard(
    store: JobStore,
    fit: Any,
    matcher: Any | None = None,
    commutes: Any | None = None,
    files: CareerFiles | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    files = files or CareerFiles(store.vault)
    index = store.index()
    keys = [k for k, e in index.items() if is_primary(e)]
    fits = fit.all(keys)
    overview = files.overview()
    weights = {e.slug: e.weight for e in overview.entries if e.status == "active"}
    total_weight = sum(weights.values()) or 1
    titles = {p.slug: p.title for p in files.profiles() if p.slug in weights}
    homes = [h.label for h in fit.homes()]

    reach = [k for k in keys if k in fits and (fits[k].homes or fits[k].remote)]
    passed = [k for k in keys if k in fits and fits[k].passed]
    good = [k for k in keys if k in fits and fits[k].fit >= GOOD_FIT and fits[k].profile]
    postings = {k: p for k in good if (p := store.posting(k)) is not None}

    def published(key: str) -> str:
        """When the posting was published (the first import saw everything at once)."""
        posting = postings.get(key)
        return (posting.date_posted if posting and posting.date_posted
                else index[key].get("firstSeen", ""))  # fmt: skip

    def city_names(key: str) -> set[str]:
        posting = postings.get(key)
        return {loc.city for loc in posting.locations if loc.city} if posting else set()

    # -- AI assessments --------------------------------------------------------------------
    records = matcher.load(keys) if matcher is not None else {}
    signature = matcher.signature() if matcher is not None and records else None
    assessed = {k: r for k, r in records.items() if r.get("assessment")}
    failed = [k for k, r in records.items() if r.get("error") and not r.get("assessment")]
    verdicts = Counter(r["assessment"]["recommendation"] for r in assessed.values())
    open_candidates = [k for k in passed if k not in records and not index[k].get("application")]
    status = matcher.status() if matcher is not None else {}
    daily = status.get("dailyLimit") or 0
    funnel = {
        "active": len(keys),
        "reach": len(reach),
        "passed": len(passed),
        "assessed": len(assessed),
        "verdicts": {v: verdicts.get(v, 0) for v in ("apply", "maybe", "no")},
        "applied": sum(1 for k in keys if index[k].get("application")),
        "open": len(open_candidates),
        "daysLeft": -(-len(open_candidates) // daily) if daily else None,
        "matcher": status,
    }

    # -- demand per profile -----------------------------------------------------------------
    week = (now - timedelta(days=7)).isoformat()
    fortnight = (now - timedelta(days=14)).isoformat()
    demand = []
    for slug, title in titles.items():
        mine = [k for k in good if fits[k].profile == slug]
        near = [k for k in mine if fits[k].homes or fits[k].remote]
        new = sum(1 for k in mine if published(k) >= week)
        before = sum(1 for k in mine if fortnight <= published(k) < week)
        demand.append({
            "slug": slug, "title": title, "good": len(mine), "near": len(near),
            "share": round(len(mine) / len(good), 3) if good else 0.0,
            "weight": round(weights[slug] / total_weight, 3),
            "newWeek": new, "newWeekBefore": before,
        })  # fmt: skip
    demand.sort(key=lambda d: -d["good"])

    # -- profile × place ----------------------------------------------------------------------
    cities: Counter[str] = Counter()
    for k in good:
        if not fits[k].homes and not fits[k].remote:
            for city in city_names(k):
                cities[city] += 1
    columns = homes + [REMOTE] + [c for c, _ in cities.most_common(TOP_CITIES)]
    matrix: dict[str, dict[str, int]] = {slug: dict.fromkeys(columns, 0) for slug in titles}
    for k in good:
        f = fits[k]
        row = matrix.get(f.profile)
        if row is None:
            continue
        labels = {n.label for n in f.homes}
        for label in labels:
            row[label] += 1
        if f.remote:
            row[REMOTE] += 1
        if not labels and not f.remote:
            for city in city_names(k):
                if city in row:
                    row[city] += 1
    commute_median: dict[str, float | None] = {}
    if commutes is not None:
        cache, by_label = commutes.read(), commutes.homes()
        for label in homes:
            times = []
            for k in good:
                if any(n.label == label for n in fits[k].homes):
                    c = commutes.annotate(fits[k], cache, by_label)["homes"].get(label) or {}
                    values = [t for t in (c.get("car"), c.get("bike")) if t is not None]
                    if values:
                        times.append(min(values))
            commute_median[label] = _median(times)
    places = {"columns": columns, "homes": homes, "rows": [
        {"slug": s, "title": titles[s], "counts": matrix[s]} for s in titles
    ], "commuteMedian": commute_median}  # fmt: skip

    # -- AI quality ---------------------------------------------------------------------------
    quality: dict[str, Any] | None = None
    if assessed:
        points = []
        same_profile = 0
        by_profile: dict[str, Counter[str]] = {}
        tokens, list_eur = [], []
        for k, r in assessed.items():
            a = r["assessment"]
            pre = fits.get(k)
            if pre is not None:
                points.append({"key": k, "title": index[k].get("title", ""),
                               "prefilter": pre.fit, "ai": a.get("fit"),
                               "recommendation": a.get("recommendation")})  # fmt: skip
                same_profile += pre.profile == a.get("best_profile")
            by_profile.setdefault(a.get("best_profile") or "?", Counter())[a["recommendation"]] += 1
            usage = r.get("usage") or {}
            if usage.get("tokens"):
                tokens.append(usage["tokens"])
            if usage.get("listEur") is not None:
                list_eur.append(usage["listEur"])
        ranked = sorted(by_profile.items(), key=lambda kv: -sum(kv[1].values()))
        diffs = [p["prefilter"] - p["ai"] for p in points if p["ai"] is not None]
        quality = {
            "points": points,
            "prefilterMean": _mean([p["prefilter"] for p in points]),
            "aiMean": _mean([p["ai"] for p in points if p["ai"] is not None]),
            "offset": _mean(diffs),
            "sameProfile": round(same_profile / len(points), 3) if points else None,
            "hitRate": round((verdicts["apply"] + verdicts["maybe"]) / len(assessed), 3),
            "byProfile": [{"slug": s, "title": titles.get(s, s),
                           "counts": {v: c.get(v, 0) for v in ("apply", "maybe", "no")}}
                          for s, c in ranked],
            "stale": sum(1 for r in assessed.values() if r.get("signature") != signature),
            "failed": len(failed),
            "tokensMean": _mean(tokens),
            "listEurMean": _mean(list_eur),
            "listEurTotal": round(sum(list_eur), 2),
        }  # fmt: skip

    # -- gaps -----------------------------------------------------------------------------------
    missing: Counter[str] = Counter()
    names: dict[str, str] = {}
    for r in assessed.values():
        for skill in r["assessment"].get("missing_skills", []):
            missing[skill_key(skill)] += 1
            names.setdefault(skill_key(skill), skill)
    levels = overview.level_of()
    aliases = overview.aliases()
    wanted: Counter[str] = Counter()
    shown: dict[str, str] = {}
    for k in good:
        if not (fits[k].homes or fits[k].remote):
            continue
        for skill in {skill_key(s) for s in index[k].get("skills") or [] if s}:
            canonical = aliases.get(skill, skill)
            wanted[canonical] += 1
            shown.setdefault(
                canonical, next(s for s in index[k]["skills"] if skill_key(s) == skill)
            )
    gaps = {
        "missing": [{"skill": names[k], "count": n} for k, n in missing.most_common(TOP_SKILLS)],
        "wanted": [{"skill": levels[k].name if k in levels else shown[k], "count": n,
                    "level": levels[k].level if k in levels else None}
                   for k, n in wanted.most_common(TOP_SKILLS)],
        "basis": sum(1 for k in good if fits[k].homes or fits[k].remote),
    }  # fmt: skip

    return {
        "generatedAt": now.isoformat(timespec="seconds"),
        "goodFit": GOOD_FIT,
        "funnel": funnel,
        "demand": demand,
        "places": places,
        "quality": quality,
        "gaps": gaps,
    }
