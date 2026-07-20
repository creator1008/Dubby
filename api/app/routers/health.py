"""Health and readiness endpoints (unauthenticated)."""

from __future__ import annotations

from fastapi import APIRouter, Response

from .. import __version__
from ..config import get_settings
from ..deps import Repo
from ..schemas import HealthOut, ReadyOut

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthOut)
async def healthz() -> HealthOut:
    """Liveness: process is up. Never touches external services."""
    settings = get_settings()
    return HealthOut(status="ok", env=settings.app_env, version=__version__)


@router.get("/readyz", response_model=ReadyOut)
async def readyz(repo: Repo, response: Response) -> ReadyOut:
    """Readiness: database reachable."""
    db_ok = await repo.ping()
    if not db_ok:
        response.status_code = 503
    return ReadyOut(status="ready" if db_ok else "degraded", database=db_ok)
