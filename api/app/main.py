"""FastAPI application factory.

Run locally:
    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .auth import JwtVerifier
from .config import get_settings
from .db import create_repository
from .routers import admin, billing, credits, health, jobs, projects, segments, uploads
from .storage import R2Storage


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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def record_api_access(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/v1/") and request.url.path != "/v1/health":
            user_id = None
            authorization = request.headers.get("authorization", "")
            if authorization.lower().startswith("bearer "):
                try:
                    user_id = request.app.state.jwt_verifier.verify(
                        authorization.split(" ", 1)[1]
                    ).id
                except Exception:
                    user_id = None
            if user_id is not None:
                try:
                    forwarded = request.headers.get("x-forwarded-for")
                    ip_address = (
                        forwarded.split(",", 1)[0].strip()
                        if forwarded
                        else request.client.host if request.client else None
                    )
                    await request.app.state.repository.record_access_log(
                        user_id,
                        method=request.method,
                        path=request.url.path,
                        status_code=response.status_code,
                        ip_address=ip_address,
                        user_agent=request.headers.get("user-agent"),
                    )
                except Exception:
                    logging.getLogger(__name__).exception(
                        "Could not record access log"
                    )
        return response

    app.include_router(health.router)
    app.include_router(projects.router)
    app.include_router(segments.router)
    app.include_router(jobs.router)
    app.include_router(credits.router)
    app.include_router(billing.router)
    app.include_router(uploads.router)
    app.include_router(admin.router)
    return app


app = create_app()
