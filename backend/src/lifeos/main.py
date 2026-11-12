"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from lifeos import __version__
from lifeos.core import auth
from lifeos.core.config import Settings, get_settings
from lifeos.core.vault import RevisionConflict, Vault, VaultPathError
from lifeos.modules.knowledge.index import KnowledgeIndex
from lifeos.modules.knowledge.router import build_router as knowledge_router
from lifeos.modules.knowledge.service import KnowledgeService
from lifeos.modules.notes.router import build_router as notes_router
from lifeos.modules.notes.store import NotesStore
from lifeos.modules.workspaces.router import build_router as workspaces_router
from lifeos.modules.workspaces.service import FeedbackError, WorkspaceService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    vault = Vault.open(settings.vault_root)

    app = FastAPI(title="LifeOS", version=__version__)
    app.add_middleware(auth.AuthMiddleware, settings=settings)

    @app.exception_handler(VaultPathError)
    async def _bad_path(_: Request, exc: VaultPathError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(FileNotFoundError)
    async def _not_found(_: Request, exc: FileNotFoundError) -> JSONResponse:
        return JSONResponse({"detail": f"not found: {exc}"}, status_code=404)

    @app.exception_handler(RevisionConflict)
    async def _conflict(_: Request, exc: RevisionConflict) -> JSONResponse:
        return JSONResponse(
            {"detail": "file changed on disk", "currentRevision": exc.current}, status_code=409
        )

    @app.exception_handler(FeedbackError)
    async def _feedback(_: Request, exc: FeedbackError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    app.include_router(auth.build_router(settings))

    knowledge = KnowledgeService(vault)
    app.include_router(knowledge_router(knowledge, KnowledgeIndex(knowledge.root)))
    app.include_router(notes_router(NotesStore(vault)))
    app.include_router(workspaces_router(WorkspaceService(vault)))

    if settings.static_dir:
        _mount_spa(app, settings.static_dir)
    return app


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    """Serve the built frontend; unknown non-API paths fall back to index.html
    so client-side routes survive a page reload."""
    index_html = static_dir / "index.html"
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise_404 = JSONResponse({"detail": "not found"}, status_code=404)
            return raise_404  # type: ignore[return-value]
        candidate = (static_dir / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(static_dir.resolve()):
            return FileResponse(candidate)
        return FileResponse(index_html)
