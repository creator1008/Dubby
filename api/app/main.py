"""FastAPI application factory.

Run locally:
    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__
from .auth import JwtVerifier
from .config import get_settings
from .db import create_repository
from .routers import admin, billing, credits, health, jobs, projects, segments, uploads, voices
from .storage import R2Storage

logger = logging.getLogger(__name__)


class AccessLogMiddleware:
    """Record authenticated /v1/* hits without BaseHTTPMiddleware.

    Starlette's BaseHTTPMiddleware can strip CORS headers from 500 responses,
    which browsers surface as net::ERR_FAILED / missing ACAO.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or ""
        method = scope.get("method") or "GET"
        status_code = 500

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status") or 500)
            await send(message)

        await self.app(scope, receive, send_wrapper)

        if not path.startswith("/v1/") or path == "/v1/health":
            return

        app = scope.get("app")
        while app is not None and not hasattr(getattr(app, "state", None), "repository"):
            app = getattr(app, "app", None)
        if app is None:
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in (scope.get("headers") or [])
        }
        authorization = headers.get("authorization", "")
        user_id = None
        if authorization.lower().startswith("bearer "):
            try:
                user_id = app.state.jwt_verifier.verify(
                    authorization.split(" ", 1)[1]
                ).id
            except Exception:
                user_id = None
        if user_id is None:
            return

        try:
            forwarded = headers.get("x-forwarded-for")
            client = scope.get("client")
            ip_address = (
                forwarded.split(",", 1)[0].strip()
                if forwarded
                else (client[0] if client else None)
            )
            await app.state.repository.record_access_log(
                user_id,
                method=method,
                path=path,
                status_code=status_code,
                ip_address=ip_address,
                user_agent=headers.get("user-agent"),
            )
        except Exception:
            logger.exception("Could not record access log")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())

    repository = create_repository(settings)
    await repository.startup()

    app.state.repository = repository
    app.state.storage = R2Storage(settings)
    app.state.jwt_verifier = JwtVerifier(settings)
    try:
        yield
    finally:
        await repository.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Dubby API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url=None,
    )
    # Last add_middleware is outermost. CORS must wrap AccessLog so 500s keep ACAO.
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        # "*" is invalid on credentialed preflights; list headers browsers send.
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Accept",
            "Origin",
            "X-Requested-With",
            "Bypass-Tunnel-Reminder",
        ],
        max_age=86400,
    )

    app.include_router(health.router)
    app.include_router(projects.router)
    app.include_router(segments.router)
    app.include_router(jobs.router)
    app.include_router(credits.router)
    app.include_router(voices.router)
    app.include_router(billing.router)
    app.include_router(uploads.router)
    app.include_router(admin.router)
    return app


app = create_app()
