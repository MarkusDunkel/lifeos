"""Job search API.

``/api/jobsearch/import/*`` is used by the importer on the user's PC (bearer token
``LIFEOS_JOBSEARCH_IMPORT_TOKEN``, checked in the auth middleware); the other routes
serve the web app.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from lifeos.modules.jobsearch.model import ID_PATTERN, SOURCE_PATTERN, Posting
from lifeos.modules.jobsearch.store import JobStore

MAX_IDS = 50_000
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


def build_router(store: JobStore, timezone: str) -> APIRouter:
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
    ) -> dict:
        """Imported postings, newest first. ``q`` matches title, company, place and
        skills (all words must match)."""
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
        items = []
        for key, entry in rows[offset : offset + limit]:
            posting = store.posting(key)
            if posting is None:
                continue
            items.append(_row(key, entry, posting) | {"duplicateCount": dupes.get(key, 0)})
        return {"total": len(rows), "offset": offset, "items": items}

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
        return _row(key, entry, found) | {
            "description": found.description,
            "benefits": found.benefits,
            "related": others,
        }

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
