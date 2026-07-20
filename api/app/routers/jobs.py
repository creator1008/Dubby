"""Job enqueueing and status polling.

A job is the unit of pipeline work (``transcribe`` or ``dub``). The API only
enqueues rows; the worker process claims and executes them. Dub jobs charge
credits up front based on the project's measured duration.
"""

from __future__ import annotations

import math
from uuid import UUID

from fastapi import APIRouter, status

from ..auth import CurrentUser
from ..config import get_settings
from ..db.base import ActiveJobExistsError, InsufficientCreditsError
from ..deps import Repo
from ..errors import (
    ConflictError,
    FeatureUnavailableError,
    NotFoundError,
    PaymentRequiredError,
)
from ..schemas import JobCreate, JobOut

router = APIRouter(prefix="/v1", tags=["jobs"])


@router.post(
    "/projects/{project_id}/jobs",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_job(
    project_id: UUID, body: JobCreate, user: CurrentUser, repo: Repo
) -> JobOut:
    project = await repo.get_project(user.id, project_id)
    if project is None:
        raise NotFoundError("Project not found")

    charge_minutes = 0.0
    if body.kind in ("dub", "lipsync"):
        duration = project.get("duration_seconds")
        if not duration:
            raise ConflictError("Project duration unknown; run transcribe first")
        if body.kind == "lipsync":
            settings = get_settings()
            if settings.lipsync_provider == "disabled":
                raise FeatureUnavailableError("feature_unavailable: lip sync is not configured")
            if not project.get("output_key"):
                raise ConflictError("Complete dubbing before premium lip sync")
            multiplier = settings.lipsync_cogs_minutes_multiplier
        else:
            multiplier = get_settings().dub_cogs_minutes_multiplier
        charge_minutes = math.ceil(
            float(duration) / 60 * multiplier
        )

    try:
        row = await repo.create_job(
            user.id, project_id, body.kind, charge_minutes=charge_minutes
        )
    except ActiveJobExistsError:
        raise ConflictError("A job is already queued or running for this project")
    except InsufficientCreditsError:
        balance = await repo.get_credit_balance(user.id)
        raise PaymentRequiredError(
            f"Need {charge_minutes} credit minutes, have {balance:g}"
        )
    except LookupError:
        raise NotFoundError("Project not found")

    return JobOut.model_validate(row)


@router.get("/projects/{project_id}/jobs", response_model=list[JobOut])
async def list_jobs(project_id: UUID, user: CurrentUser, repo: Repo) -> list[JobOut]:
    if await repo.get_project(user.id, project_id) is None:
        raise NotFoundError("Project not found")
    rows = await repo.list_jobs(user.id, project_id)
    return [JobOut.model_validate(r) for r in rows]


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: UUID, user: CurrentUser, repo: Repo) -> JobOut:
    row = await repo.get_job(user.id, job_id)
    if row is None:
        raise NotFoundError("Job not found")
    return JobOut.model_validate(row)


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: UUID, user: CurrentUser, repo: Repo) -> JobOut:
    row = await repo.get_job(user.id, job_id)
    if row is None:
        raise NotFoundError("Job not found")
    if row["status"] in ("queued", "running"):
        await repo.finish_job(job_id, status="cancelled", error="cancelled by user")
    updated = await repo.get_job(user.id, job_id)
    assert updated is not None
    return JobOut.model_validate(updated)
