from __future__ import annotations

from fastapi import APIRouter, HTTPException

from lifeos.modules.study.service import StudyError, StudyService


def build_router(service: StudyService) -> APIRouter:
    router = APIRouter(prefix="/api/study", tags=["study"])

    @router.get("/subjects")
    def subjects() -> list[dict]:
        return service.overview()

    @router.get("/{subject}/progress")
    def progress(subject: str) -> dict:
        try:
            return service.progress(subject)
        except StudyError as exc:
            raise HTTPException(404, str(exc)) from exc

    return router
