from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from lifeos.modules.knowledge.index import KnowledgeIndex
from lifeos.modules.knowledge.service import KnowledgeService


class SaveRequest(BaseModel):
    path: str
    content: str
    revision: str | None = None  # None = create new file


class DeleteRequest(BaseModel):
    path: str
    revision: str


def build_router(service: KnowledgeService, index: KnowledgeIndex) -> APIRouter:
    router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

    @router.get("/tree")
    def tree() -> dict:
        return asdict(service.tree())

    @router.get("/file")
    def read(path: str = Query(...)) -> dict:
        return asdict(service.read(path))

    @router.put("/file")
    def save(body: SaveRequest) -> dict:
        revision = service.write(body.path, body.content, body.revision)
        return {"path": body.path, "revision": revision}

    @router.post("/delete")
    def delete(body: DeleteRequest) -> dict:
        service.delete(body.path, body.revision)
        return {"deleted": body.path}

    @router.get("/asset")
    def asset(path: str = Query(...)) -> FileResponse:
        return FileResponse(service.asset(path))

    @router.get("/search")
    def search(q: str = Query(..., min_length=1)) -> list[dict]:
        return [asdict(hit) for hit in index.search(q)]

    @router.get("/backlinks")
    def backlinks(path: str = Query(...)) -> list[dict]:
        return [{"path": p, "title": t} for p, t in index.backlinks(path)]

    return router
