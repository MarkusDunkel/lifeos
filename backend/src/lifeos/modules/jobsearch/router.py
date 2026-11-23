"""Job search API.

``/api/jobsearch/import/*`` is used by the importer on the user's PC (bearer token
``LIFEOS_JOBSEARCH_IMPORT_TOKEN``, checked in the auth middleware); the other routes
serve the web app.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from lifeos.modules.jobsearch.model import ID_PATTERN, SOURCE_PATTERN, Posting
from lifeos.modules.jobsearch.store import JobStore

MAX_IDS = 50_000
RANK = {"apply": 0, "maybe": 1, None: 2, "no": 3}  # order of sort=match
MAX_POSTINGS = 200


class DiffRequest(BaseModel):
    source: str
    ids: list[str] = Field(max_length=MAX_IDS)


class PostingsRequest(BaseModel):
    postings: list[Posting] = Field(max_length=MAX_POSTINGS)


class FinishRequest(BaseModel):
    source: str
    started: str | None = None
    listed: int = 0
    new: int = 0
    duplicates: int = 0
    reposts: int = 0
    failed: int = 0
    complete: bool = True  # False when the run was interrupted or limited
    errors: list[str] = Field(default_factory=list, max_length=20)


def build_router(
    store: JobStore,
    timezone: str,
    fit: Any | None = None,
    matcher: Any | None = None,
    commute: Any | None = None,
) -> APIRouter:
    """``fit`` (a :class:`lifeos.modules.career.prefilter.FitIndex`) adds the profile fit
    and home labels to the rows and allows sorting and filtering by them; ``matcher`` (a
    :class:`lifeos.modules.career.matcher.Matcher`) adds the AI assessments; ``commute``
    (a :class:`lifeos.modules.career.commute.Commutes`) the commute times, which weigh the
    sorting by fit and by recommendation softly."""
    router = APIRouter(prefix="/api/jobsearch", tags=["jobsearch"])
    tz = ZoneInfo(timezone)

    def _source(value: str) -> str:
        if not SOURCE_PATTERN.match(value):
            raise HTTPException(400, "invalid source")
        return value

    def _last_import() -> dict[str, Any] | None:
        runs = store.runs()
        return runs[0] if runs else None

    def _today_done() -> bool:
        today = datetime.now(tz).date()
        for run in store.runs():
            at = datetime.fromisoformat(run["at"]).astimezone(tz).date()
            if at == today and run.get("complete"):
                return True
        return False

    # -- importer (PC) ------------------------------------------------------------

    @router.get("/import/status")
    def import_status() -> dict:
        return {"lastImport": _last_import(), "doneToday": _today_done()}

    @router.post("/import/diff")
    def import_diff(body: DiffRequest) -> dict:
        source = _source(body.source)
        if any(not ID_PATTERN.match(i) for i in body.ids):
            raise HTTPException(400, "invalid posting id")
        return store.diff(source, body.ids)

    @router.post("/import/postings")
    def import_postings(body: PostingsRequest) -> dict:
        return store.add(body.postings)

    @router.post("/import/finish")
    def import_finish(body: FinishRequest) -> dict:
        _source(body.source)
        record = store.finish(body.model_dump())
        if fit is not None:
            fit.warm()
        return {"run": record, "doneToday": _today_done()}

    # -- web app ------------------------------------------------------------------

    @router.get("/status")
    def status() -> dict:
        return {
            "lastImport": _last_import(),
            "doneToday": _today_done(),
            "counts": store.counts(),
            "runs": store.runs()[:10],
        }

    @router.get("/postings")
    def postings(
        q: str = "",
        duplicates: bool = False,
        gone: bool = False,
        applied: bool = False,
        days: int | None = Query(default=None, ge=1),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
        sort: str = Query(default="new", pattern="^(new|fit|match)$"),
        profile: str | None = None,
        home: str | None = None,
        reach: bool = False,
        passed: bool = False,
        recommended: bool = False,
        assessed: bool = False,
    ) -> dict:
        """Imported postings, newest first (``sort=fit``: best profile fit first). ``q``
        matches title, company, place and skills (all words must match). ``profile`` sorts
        by the fit to that profile; ``home`` keeps postings near that home, ``reach`` those
        near any home or remote, ``passed`` those that pass the prefilter. ``sort=match``:
        the AI recommendation first (apply, maybe, not assessed, no), then the fit;
        ``recommended`` keeps those the AI recommends to apply for, ``assessed`` those with
        an assessment."""
        words = q.lower().split()
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat() if days else None
        index = store.index()
        dupes: dict[str, int] = {}
        for entry in index.values():
            if entry.get("duplicateOf") and not entry.get("goneAt"):
                dupes[entry["duplicateOf"]] = dupes.get(entry["duplicateOf"], 0) + 1
        rows = []
        for key, entry in index.items():
            if entry.get("goneAt") and not gone:
                continue
            if entry.get("duplicateOf") and not duplicates:
                continue
            if since and entry.get("firstSeen", "") < since:
                continue
            if applied and not entry.get("application"):
                continue
            if words:
                text = " ".join(
                    [entry.get("title", ""), entry.get("company", "")]
                    + entry.get("cities", [])
                    + entry.get("skills", [])
                ).lower()
                if not all(w in text for w in words):
                    continue
            rows.append((key, entry))
        rows.sort(key=lambda kv: (kv[1].get("firstSeen", ""), kv[0]), reverse=True)
        verdicts: dict[str, Any] = {}
        if matcher is not None and (sort == "match" or recommended or assessed):
            verdicts = matcher.summaries([k for k, _ in rows])
            if recommended:
                rows = [
                    kv for kv in rows if verdicts.get(kv[0], {}).get("recommendation") == "apply"
                ]
            if assessed:
                rows = [kv for kv in rows if verdicts.get(kv[0], {}).get("recommendation")]
        fits: dict[str, Any] = {}
        if fit is not None and (sort in ("fit", "match") or profile or home or reach or passed):
            fits = fit.all([k for k, _ in rows])

            def keep(key: str) -> bool:
                f = fits.get(key)
                if f is None:
                    return False
                if home and all(n.label != home for n in f.homes):
                    return False
                if reach and not (f.homes or f.remote):
                    return False
                return not passed or f.passed

            rows = [kv for kv in rows if keep(kv[0])]
            weights: dict[str, float] = {}
            if commute is not None and (sort in ("fit", "match") or profile):
                cache, homes_by_label = commute.read(), commute.homes()
                weights = {k: commute.annotate(fits[k], cache, homes_by_label)["factor"]
                           for k, _ in rows}  # fmt: skip
            if sort == "match":

                def rank(key: str) -> tuple[int, float]:
                    v = verdicts.get(key) or {}
                    score = v.get("fit") if v.get("fit") is not None else fits[key].fit
                    return (RANK.get(v.get("recommendation"), 2), -score * weights.get(key, 1.0))

                rows.sort(key=lambda kv: rank(kv[0]))
            elif sort == "fit" or profile:

                def value(key: str) -> float:
                    f = fits[key]
                    if profile:
                        score = next((p.fit for p in f.profiles if p.slug == profile), 0)
                    else:
                        score = f.fit
                    return score * weights.get(key, 1.0)

                rows.sort(key=lambda kv: value(kv[0]), reverse=True)  # stable: newest first
        page = rows[offset : offset + limit]
        if fit is not None and not fits:
            fits = fit.all([k for k, _ in page])
        if matcher is not None and not verdicts:
            verdicts = matcher.summaries([k for k, _ in page])
        cache = commute.read() if commute is not None else {}
        homes_by_label = commute.homes() if commute is not None else {}
        items = []
        for key, entry in page:
            posting = store.posting(key)
            if posting is None:
                continue
            row = _row(key, entry, posting) | {"duplicateCount": dupes.get(key, 0)}
            if key in fits:
                row["fit"] = asdict(fits[key])
                if commute is not None:
                    row["commute"] = commute.annotate(fits[key], cache, homes_by_label)
            if key in verdicts:
                row["assessment"] = verdicts[key]
            items.append(row)
        return {"total": len(rows), "offset": offset, "items": items}

    dashboard_cache: dict[str, Any] = {}

    @router.get("/dashboard")
    def dashboard(refresh: bool = False) -> dict:
        """Funnel, demand per profile, profile × place, AI quality and gaps (cached 5 min)."""
        if fit is None:
            raise HTTPException(503, "the job profiles are not set up")
        from lifeos.modules.career.dashboard import build_dashboard

        now = datetime.now(UTC)
        cached = dashboard_cache.get("value")
        if refresh or not cached or now - dashboard_cache["at"] > timedelta(minutes=5):
            cached = build_dashboard(store, fit, matcher, commute, now=now)
            dashboard_cache.update(value=cached, at=now)
        return cached

    @router.get("/matcher")
    def matcher_status() -> dict:
        """Today's assessments against the daily limits, and the queue."""
        if matcher is None:
            return {"configured": False}
        return matcher.status()

    @router.post("/postings/{source}/{posting_id}/assess")
    def assess(source: str, posting_id: str) -> dict:
        """Queue a posting for the AI matcher (the worker assesses it within minutes)."""
        key = f"{_source(source)}:{posting_id}"
        if matcher is None:
            raise HTTPException(503, "the AI matcher is not set up")
        if not ID_PATTERN.match(posting_id) or key not in store.index():
            raise HTTPException(404, "posting not found")
        return matcher.enqueue(key)

    @router.get("/fit/options")
    def fit_options() -> dict:
        """The active profiles and the homes, for the filters of the job list."""
        if fit is None:
            return {"profiles": [], "homes": []}
        return {"profiles": fit.profiles(), "homes": [asdict(h) for h in fit.homes()]}

    @router.get("/postings/{source}/{posting_id}")
    def posting(source: str, posting_id: str) -> dict:
        key = f"{_source(source)}:{posting_id}"
        entry = store.index().get(key)
        found = store.posting(key) if entry and ID_PATTERN.match(posting_id) else None
        if found is None:
            raise HTTPException(404, "posting not found")
        others = [
            _row(k, e, p)
            for k, e in store.index().items()
            if (e.get("duplicateOf") == key or key == entry.get("duplicateOf"))
            and k != key
            and (p := store.posting(k)) is not None
        ]
        extra = {}
        if fit is not None:
            found_fit = fit.all([key]).get(key)
            extra = {"fit": asdict(found_fit)} if found_fit else {}
            if found_fit and commute is not None:
                extra["commute"] = commute.annotate(found_fit)
        if matcher is not None:
            record = matcher.load([key]).get(key)
            if record:
                summary = matcher.summaries([key]).get(key, {})
                extra["assessment"] = summary | {
                    "detail": record.get("assessment"),
                    "research": record.get("research", ""),
                    "error": record.get("error"),
                    "usage": record.get("usage"),
                }
            extra["queued"] = key in matcher.queue()
        return (
            _row(key, entry, found)
            | extra
            | {
                "description": found.description,
                "benefits": found.benefits,
                "related": others,
            }
        )

    return router


def _row(key: str, entry: dict[str, Any], posting: Posting) -> dict[str, Any]:
    data = posting.model_dump(mode="json", exclude={"description", "benefits"})
    return data | {
        "key": key,
        "place": posting.place(),
        "firstSeen": entry.get("firstSeen"),
        "lastSeen": entry.get("lastSeen"),
        "goneAt": entry.get("goneAt"),
        "duplicateOf": entry.get("duplicateOf"),
        "repostOf": entry.get("repostOf"),
        "application": entry.get("application"),
    }
