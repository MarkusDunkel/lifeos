"""Job search API.

``/api/jobsearch/import/*`` is used by the importer on the user's PC (bearer token
``LIFEOS_JOBSEARCH_IMPORT_TOKEN``, checked in the auth middleware); the other routes
serve the web app.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from lifeos.modules.jobsearch.model import ID_PATTERN, SOURCE_PATTERN, Posting
from lifeos.modules.jobsearch.store import STATES, JobStore

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
        return {"stored": store.add(body.postings)}

    @router.post("/import/finish")
    def import_finish(body: FinishRequest) -> dict:
        _source(body.source)
        record = store.finish(body.model_dump())
        return {"run": record, "doneToday": _today_done()}

    # -- web app ------------------------------------------------------------------

    @router.get("/status")
    def status() -> dict:
        return {"lastImport": _last_import(), "doneToday": _today_done(), "counts": store.counts()}

    @router.get("/postings")
    def postings(
        state: str | None = None,
        min_score: int | None = None,
        days: int | None = None,
        limit: int = 200,
    ) -> list[dict]:
        if state and state not in STATES:
            raise HTTPException(400, f"state must be one of {', '.join(STATES)}")
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat() if days else None
        rows = []
        for key, entry in store.index().items():
            if entry.get("goneAt"):
                continue
            if state and entry.get("state") != state:
                continue
            if min_score is not None and (entry.get("score") or 0) < min_score:
                continue
            if since and entry.get("firstSeen", "") < since:
                continue
            rows.append((key, entry))
        rows.sort(key=lambda kv: (-(kv[1].get("score") or 0), kv[1].get("firstSeen", "")))
        out = []
        for key, entry in rows[: max(1, min(limit, 1000))]:
            posting = store.posting(key)
            if posting is None:
                continue
            summary = posting.model_dump(exclude={"description", "benefits"})
            out.append(summary | {"key": key, **entry})
        return out

    return router
