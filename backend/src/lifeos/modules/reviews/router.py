"""API of the review page: packages, their documents, the rendered view and feedback."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from lifeos.modules.reviews.package import PackageError
from lifeos.modules.reviews.service import ReviewService, ViewError
from lifeos.modules.workspaces.feedback import Scope


class PackageRef(BaseModel):
    path: str


class DocRef(PackageRef):
    doc: int


class RoundRef(DocRef):
    round: str


class AddItem(RoundRef):
    start_line: int
    end_line: int
    scope: Scope
    feedback: str
    quote: str | None = None


class UpdateItem(RoundRef):
    id: str
    feedback: str


class DeleteItem(RoundRef):
    id: str


def build_router(service: ReviewService) -> APIRouter:
    router = APIRouter(prefix="/api/reviews", tags=["reviews"])

    def guarded(call):
        try:
            return call()
        except PackageError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("")
    def packages() -> list[dict]:
        return service.list()

    @router.get("/package")
    def package(path: str = Query(...)) -> dict:
        return guarded(lambda: service.package(path))

    @router.get("/view")
    def view(path: str = Query(...), doc: int = Query(...)) -> Response:
        try:
            pdf = guarded(lambda: service.view(path, doc))
        except ViewError as exc:
            raise HTTPException(422, str(exc)) from exc
        headers = {"Content-Disposition": "inline", "Cache-Control": "no-store"}
        return Response(pdf, media_type="application/pdf", headers=headers)

    @router.get("/round")
    def get_round(path: str = Query(...), doc: int = Query(...), round: str = Query(...)) -> dict:
        return guarded(lambda: service.round(path, doc, round))

    @router.post("/rounds")
    def start(body: DocRef) -> dict:
        return guarded(lambda: service.start(body.path, body.doc))

    @router.post("/rounds/items")
    def add_item(body: AddItem) -> dict:
        return guarded(lambda: service.add_item(
            body.path, body.doc, body.round, start_line=body.start_line,
            end_line=body.end_line, scope=body.scope, feedback=body.feedback, quote=body.quote,
        ))  # fmt: skip

    @router.put("/rounds/items")
    def update_item(body: UpdateItem) -> dict:
        return guarded(lambda: service.update_item(body.path, body.doc, body.round, body.id,
                                                   body.feedback))  # fmt: skip

    @router.post("/rounds/items/delete")
    def delete_item(body: DeleteItem) -> dict:
        return guarded(lambda: service.delete_item(body.path, body.doc, body.round, body.id))

    @router.post("/rounds/seal")
    def seal(body: RoundRef) -> dict:
        return guarded(lambda: service.seal(body.path, body.doc, body.round))

    @router.post("/send")
    def send(body: PackageRef) -> dict:
        return guarded(lambda: service.send(body.path))

    @router.post("/rounds/discard")
    def discard(body: RoundRef) -> dict:
        guarded(lambda: service.discard(body.path, body.doc, body.round))
        return {"ok": True}

    return router
