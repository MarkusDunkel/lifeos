"""Single-user authentication with a signed session cookie."""

from __future__ import annotations

import asyncio

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, TimestampSigner
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from lifeos.core.config import Settings

COOKIE_NAME = "lifeos_session"
SESSION_PAYLOAD = "owner"
PUBLIC_API_PREFIXES = ("/api/auth/", "/api/health")

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def _signer(settings: Settings) -> TimestampSigner:
    return TimestampSigner(settings.session_secret, salt="lifeos-session")


def is_authenticated(request: Request, settings: Settings) -> bool:
    if settings.auth_disabled:
        return True
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    try:
        value = _signer(settings).unsign(token, max_age=settings.session_max_age_days * 86400)
    except BadSignature:
        return False
    return value.decode() == SESSION_PAYLOAD


class AuthMiddleware(BaseHTTPMiddleware):
    """Rejects unauthenticated API calls. Static files and the SPA stay public
    (they contain no data); the SPA itself shows the login screen."""

    def __init__(self, app, settings: Settings):
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        protected = path.startswith("/api/") and not path.startswith(PUBLIC_API_PREFIXES)
        if protected and not is_authenticated(request, self.settings):
            return JSONResponse({"detail": "not authenticated"}, status_code=401)
        return await call_next(request)


class LoginRequest(BaseModel):
    password: str


class SessionInfo(BaseModel):
    authenticated: bool
    auth_disabled: bool


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.get("/session")
    def session(request: Request) -> SessionInfo:
        return SessionInfo(
            authenticated=is_authenticated(request, settings),
            auth_disabled=settings.auth_disabled,
        )

    @router.post("/login")
    async def login(body: LoginRequest, response: Response) -> SessionInfo:
        if settings.auth_disabled:
            return SessionInfo(authenticated=True, auth_disabled=True)
        ok = False
        if settings.password_hash:
            try:
                ok = _hasher.verify(settings.password_hash, body.password)
            except (VerificationError, InvalidHashError):
                ok = False
        if not ok:
            await asyncio.sleep(1.0)  # slow down guessing
            raise HTTPException(status_code=401, detail="invalid password")
        token = _signer(settings).sign(SESSION_PAYLOAD).decode()
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=settings.session_max_age_days * 86400,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
        )
        return SessionInfo(authenticated=True, auth_disabled=False)

    @router.post("/logout")
    def logout(response: Response) -> SessionInfo:
        response.delete_cookie(COOKIE_NAME)
        return SessionInfo(authenticated=False, auth_disabled=settings.auth_disabled)

    return router
