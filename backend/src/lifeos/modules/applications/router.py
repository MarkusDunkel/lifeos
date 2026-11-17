"""Applications API: start an application from a posting (or a pasted one), list them."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from lifeos.modules.applications.service import Applications, ManualPosting


class StartRequest(BaseModel):
    posting: str | None = None  # key of an imported posting, e.g. "devjobs:<id>"
    manual: ManualPosting | None = None


def build_router(service: Applications) -> APIRouter:
    router = APIRouter(prefix="/api/applications", tags=["applications"])

    @router.get("")
    def overview() -> dict:
        return {"missing": service.missing(), "items": service.list()}

    @router.post("")
    def start(body: StartRequest) -> dict:
        return service.start(body.posting, body.manual)

    return router
