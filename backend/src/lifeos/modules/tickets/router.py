from __future__ import annotations

from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from lifeos.modules.tickets.service import TicketService


class SaveRequest(BaseModel):
    path: str
    fields: dict[str, Any]
    revision: str


class CreateRequest(BaseModel):
    repo: str
    title: str


class PathRevision(BaseModel):
    path: str
    revision: str


class ResolveRequest(PathRevision):
    field: str
    choice: Literal["local", "remote"]


def build_router(service: TicketService) -> APIRouter:
    router = APIRouter(prefix="/api/tickets", tags=["tickets"])
    gh = service.gh

    @router.get("")
    def list_tickets() -> dict:
        return service.list()

    @router.get("/file")
    def read(path: str = Query(...)) -> dict:
        return service.read(path)

    @router.put("/file")
    def save(body: SaveRequest) -> dict:
        return service.save(body.path, body.fields, body.revision)

    @router.post("/new")
    def create(body: CreateRequest) -> dict:
        return service.create(body.repo, body.title)

    @router.post("/delete")
    def delete(body: PathRevision) -> dict:
        service.delete(body.path, body.revision)
        return {"deleted": body.path}

    @router.post("/revert")
    def revert(body: PathRevision) -> dict:
        return service.revert(body.path, body.revision)

    @router.post("/resolve")
    def resolve(body: ResolveRequest) -> dict:
        return service.resolve(body.path, body.field, body.choice, body.revision)

    @router.post("/sync")
    def sync() -> dict:
        return asdict(gh.sync())

    @router.post("/plans")
    def create_plan() -> dict:
        return gh.create_plan()

    @router.get("/plans/{plan_id}")
    def read_plan(plan_id: str) -> dict:
        return gh.read_plan(plan_id)

    @router.post("/plans/{plan_id}/apply")
    def apply(plan_id: str) -> dict:
        return gh.apply(plan_id)

    @router.post("/plans/{plan_id}/discard")
    def discard(plan_id: str) -> dict:
        gh.discard_plan(plan_id)
        return {"discarded": plan_id}

    return router
