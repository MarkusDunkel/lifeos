"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from lifeos import __version__
from lifeos.agents.admin import AgentAdmin
from lifeos.agents.router import build_router as agents_router
from lifeos.connectors.github.client import GitHubClient, GitHubError
from lifeos.connectors.github.sync import GitHubConnector, SyncError
from lifeos.connectors.moodle.client import MoodleError
from lifeos.connectors.moodle.setup import build_moodle
from lifeos.connectors.onedrive.auth import MicrosoftAuth, OneDriveError
from lifeos.connectors.onedrive.client import GraphClient
from lifeos.connectors.onedrive.sync import PresentationsSync
from lifeos.core import auth
from lifeos.core.config import Settings, get_settings
from lifeos.core.git_sync import background_committer, git_sync_for
from lifeos.core.vault import RevisionConflict, Vault, VaultPathError
from lifeos.mcp_server.http import mount_mcp
from lifeos.mcp_server.server import build_mcp
from lifeos.modules.applications.router import build_router as applications_router
from lifeos.modules.applications.service import ApplicationError, Applications
from lifeos.modules.career.matcher import Matcher
from lifeos.modules.career.prefilter import FitIndex
from lifeos.modules.career.router import CareerService
from lifeos.modules.career.router import build_router as career_router
from lifeos.modules.jobsearch.router import build_router as jobsearch_router
from lifeos.modules.jobsearch.store import JobStore
from lifeos.modules.knowledge.index import KnowledgeIndex
from lifeos.modules.knowledge.router import build_router as knowledge_router
from lifeos.modules.knowledge.service import KnowledgeService
from lifeos.modules.moodle.router import build_router as moodle_router
from lifeos.modules.notes.router import build_router as notes_router
from lifeos.modules.notes.store import NotesStore
from lifeos.modules.presentations.router import build_router as presentations_router
from lifeos.modules.reviews.router import build_router as reviews_router
from lifeos.modules.reviews.service import ReviewService
from lifeos.modules.study.router import build_router as study_router
from lifeos.modules.study.service import StudyService
from lifeos.modules.tickets.router import build_router as tickets_router
from lifeos.modules.tickets.service import TicketService
from lifeos.modules.vault_sync.router import build_router as vault_sync_router
from lifeos.modules.workspaces.router import build_router as workspaces_router
from lifeos.modules.workspaces.service import FeedbackError, WorkspaceService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    vault = Vault.open(settings.vault_root)
    git = git_sync_for(settings)
    study = StudyService(vault, settings.timezone, committer=background_committer(git))
    mcp = build_mcp(study) if settings.mcp_path_token else None

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        # Study sessions live in this process: end idle ones (with their commit) regularly.
        closer = asyncio.create_task(_close_idle_sessions(study))
        try:
            if mcp is None:
                yield
            else:
                async with mcp.session_manager.run():
                    yield
        finally:
            closer.cancel()

    app = FastAPI(title="LifeOS", version=__version__, lifespan=lifespan)
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

    @app.exception_handler(SyncError)
    async def _sync(_: Request, exc: SyncError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(GitHubError)
    async def _github(_: Request, exc: GitHubError) -> JSONResponse:
        return JSONResponse({"detail": f"GitHub: {exc}"}, status_code=502)

    @app.exception_handler(OneDriveError)
    async def _onedrive(_: Request, exc: OneDriveError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(ApplicationError)
    async def _application(_: Request, exc: ApplicationError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(MoodleError)
    async def _moodle(_: Request, exc: MoodleError) -> JSONResponse:
        return JSONResponse({"detail": f"Moodle: {exc}"}, status_code=exc.status)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    app.include_router(auth.build_router(settings))

    knowledge = KnowledgeService(vault)
    app.include_router(knowledge_router(knowledge, KnowledgeIndex(knowledge.root)))
    app.include_router(notes_router(NotesStore(vault)))
    app.include_router(workspaces_router(WorkspaceService(vault)))
    token = settings.github_token
    app.include_router(reviews_router(ReviewService(
        vault,
        github=(lambda: GitHubClient(token)) if token else None,
        committer=background_committer(git),
        public_url=settings.public_url,
    )))  # fmt: skip

    github = GitHubConnector(
        vault,
        (lambda: GitHubClient(token)) if token else None,
        settings.github_repo_list,
        settings.github_project_list,
    )
    app.include_router(tickets_router(TicketService(github)))
    app.include_router(_presentations(settings, vault))
    app.include_router(_moodle_router(settings, vault))
    jobs_file = settings.state_dir / "jobs.json" if settings.state_dir else None
    app.include_router(vault_sync_router(git, settings.git_sync_interval, jobs_file))
    app.include_router(study_router(study))
    jobs = JobStore(vault)
    fit = FitIndex(vault, jobs)
    fit.warm()
    matcher = Matcher(vault, jobs, fit, settings.timezone)
    app.include_router(jobsearch_router(jobs, settings.timezone, fit, matcher))
    applications = Applications(
        vault,
        jobs,
        (lambda: GitHubClient(token)) if token else None,
        settings.applications_repo or next(iter(settings.github_repo_list), None),
        settings.applications_project,
        committer=background_committer(git),
        timezone=settings.timezone,
    )
    app.include_router(applications_router(applications))
    app.include_router(career_router(CareerService(vault, committer=background_committer(git))))
    app.include_router(agents_router(AgentAdmin(vault, committer=background_committer(git))))

    if mcp is not None and settings.mcp_path_token:
        mount_mcp(app, mcp, settings.mcp_path_token)

    if settings.static_dir:
        _mount_spa(app, settings.static_dir)
    return app


async def _close_idle_sessions(study: StudyService, every: float = 60) -> None:
    while True:
        await asyncio.sleep(every)
        try:
            await asyncio.to_thread(study.close_idle)
        except Exception:  # keep the loop alive; the next round retries
            logging.getLogger("lifeos.study").exception("closing idle sessions failed")


def _presentations(settings: Settings, vault: Vault):
    missing = [
        name
        for name, value in (
            ("LIFEOS_MS_CLIENT_ID", settings.ms_client_id),
            ("LIFEOS_STATE_DIR", settings.state_dir),
            ("LIFEOS_ONEDRIVE_FOLDER", settings.onedrive_folder),
        )
        if not value
    ]
    auth = None
    if settings.ms_client_id and settings.state_dir:
        auth = MicrosoftAuth(settings.ms_client_id, settings.state_dir)
    factory = (lambda: GraphClient(auth.access_token)) if auth and not missing else None
    sync = PresentationsSync(vault, factory, settings.onedrive_folder, settings.presentations_dir)
    return presentations_router(sync, auth, missing)


def _moodle_router(settings: Settings, vault: Vault):
    sync, missing = build_moodle(settings, vault)
    return moodle_router(sync, missing)


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
