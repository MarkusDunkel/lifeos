from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from lifeos.modules.workspaces.service import WorkspaceService


class RoundRef(BaseModel):
    name: str
    round: str


class AddItem(RoundRef):
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    scope: Literal["blocks", "rendered-text"]
    feedback: str
    quote: str | None = None  # rendered text; for "blocks" the server takes the source lines


class UpdateItem(RoundRef):
    id: str
    feedback: str


class DeleteItem(RoundRef):
    id: str


class StartRound(BaseModel):
    name: str


def build_router(service: WorkspaceService) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])

    @router.get("")
    def list_reports() -> dict:
        return service.list()

    @router.get("/file")
    def read(path: str = Query(...)) -> dict:
        return asdict(service.read(path))

    @router.get("/round")
    def get_round(name: str = Query(...), round: str = Query(...)) -> dict:
        return service.round(name, round)

    @router.post("/rounds")
    def start(body: StartRound) -> dict:
        return service.start(body.name)

    @router.post("/rounds/items")
    def add_item(body: AddItem) -> dict:
        return service.add_item(
            body.name,
            body.round,
            start_line=body.start_line,
            end_line=body.end_line,
            scope=body.scope,
            feedback=body.feedback,
            quote=body.quote,
        )

    @router.put("/rounds/items")
    def update_item(body: UpdateItem) -> dict:
        return service.update_item(body.name, body.round, body.id, body.feedback)

    @router.post("/rounds/items/delete")
    def delete_item(body: DeleteItem) -> dict:
        return service.delete_item(body.name, body.round, body.id)

    @router.post("/rounds/seal")
    def seal(body: RoundRef) -> dict:
        return service.seal(body.name, body.round)

    @router.post("/rounds/discard")
    def discard(body: RoundRef) -> dict:
        service.discard(body.name, body.round)
        return {"discarded": body.round}

    return router
