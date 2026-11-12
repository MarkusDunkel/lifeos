from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Path
from pydantic import BaseModel

from lifeos.core.vault import NOTES
from lifeos.modules.notes.schema import SCHEMA_FILE, build_schema, load_overrides
from lifeos.modules.notes.store import NotesStore

NOTE_ID = Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class CreateRequest(BaseModel):
    cells: dict[str, Any]


class UpdateRequest(BaseModel):
    cells: dict[str, Any]
    revision: str


class DeleteRequest(BaseModel):
    revision: str


def build_router(store: NotesStore) -> APIRouter:
    router = APIRouter(prefix="/api/notes", tags=["notes"])

    @router.get("")
    def list_notes() -> dict:
        notes = store.list()
        overrides = load_overrides(store.vault.resolve(SCHEMA_FILE, zone=NOTES))
        return {
            "fields": [asdict(f) for f in build_schema(notes, overrides)],
            "notes": [asdict(n) for n in notes],
        }

    @router.post("")
    def create(body: CreateRequest) -> dict:
        return asdict(store.create(body.cells))

    @router.put("/{note_id}")
    def update(body: UpdateRequest, note_id: str = NOTE_ID) -> dict:
        return asdict(store.update(note_id, body.cells, body.revision))

    @router.post("/{note_id}/delete")
    def delete(body: DeleteRequest, note_id: str = NOTE_ID) -> dict:
        store.delete(note_id, body.revision)
        return {"deleted": note_id}

    return router
