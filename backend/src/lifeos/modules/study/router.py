from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lifeos.modules.study.service import StudyError, StudyService


class GradeBody(BaseModel):
    result: Literal["correct", "partial", "wrong", "known"]


def build_router(service: StudyService) -> APIRouter:
    router = APIRouter(prefix="/api/study", tags=["study"])

    @router.get("/subjects")
    def subjects() -> list[dict]:
        return service.overview()

    @router.get("/subjects/{subject}")
    def detail(subject: str) -> dict:
        try:
            return service.detail(subject)
        except StudyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get("/{subject}/progress")
    def progress(subject: str) -> dict:
        try:
            return service.progress(subject)
        except StudyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post("/{subject}/cards/{card_id}/grade")
    def grade(subject: str, card_id: str, body: GradeBody) -> dict:
        """Correct the latest grade of a card, or mark it as known."""
        try:
            target = service.subject(subject)
            if target.card(card_id) is None:
                raise HTTPException(404, f"unknown card '{card_id}' in {target.key}")
        except StudyError as exc:
            raise HTTPException(404, str(exc)) from exc
        try:
            return service.correct(card_id, body.result, subject=target.key)
        except StudyError as exc:
            raise HTTPException(400, str(exc)) from exc

    return router
