"""Project CRUD, scoped to the authenticated user."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from ..auth import CurrentUser
from ..config import get_settings
from ..deps import Repo, Storage
from ..errors import NotFoundError
from ..schemas import DownloadUrlResponse, ProjectCreate, ProjectOut, ProjectUpdate

router = APIRouter(prefix="/v1/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
async def list_projects(user: CurrentUser, repo: Repo) -> list[ProjectOut]:
    rows = await repo.list_projects(user.id)
    return [ProjectOut.model_validate(r) for r in rows]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate, user: CurrentUser, repo: Repo
) -> ProjectOut:
    row = await repo.create_project(
        user.id,
        title=body.title,
        source_lang=body.source_lang,
        target_lang=body.target_lang,
        subtitle_mode=body.subtitle_mode,
        tone_style=body.tone_style,
        diarization_enabled=body.diarization_enabled,
    )
    return ProjectOut.model_validate(row)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: UUID, user: CurrentUser, repo: Repo) -> ProjectOut:
    row = await repo.get_project(user.id, project_id)
    if row is None:
        raise NotFoundError("Project not found")
    return ProjectOut.model_validate(row)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: UUID, body: ProjectUpdate, user: CurrentUser, repo: Repo
) -> ProjectOut:
    row = await repo.update_project(
        user.id, project_id, body.model_dump(exclude_unset=True)
    )
    if row is None:
        raise NotFoundError("Project not found")
    return ProjectOut.model_validate(row)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID, user: CurrentUser, repo: Repo, storage: Storage
) -> None:
    deleted = await repo.delete_project(user.id, project_id)
    if not deleted:
        raise NotFoundError("Project not found")
    # Storage cleanup is best-effort; orphans are cheap and can be swept later.
    try:
        await storage.delete_prefix(f"users/{user.id}/projects/{project_id}/")
    except Exception:  # noqa: BLE001 - deletion must not fail the request
        pass


@router.get("/{project_id}/source-url", response_model=DownloadUrlResponse)
async def get_source_url(
    project_id: UUID, user: CurrentUser, repo: Repo, storage: Storage
) -> DownloadUrlResponse:
    """Presigned GET for the uploaded source video (Before preview)."""
    row = await repo.get_project(user.id, project_id)
    if row is None:
        raise NotFoundError("Project not found")
    source_key = row.get("source_key")
    if not source_key:
        raise NotFoundError("Source not uploaded yet")
    expires_in = get_settings().download_expires_seconds
    url = await storage.presign_get(source_key, expires_in=expires_in)
    return DownloadUrlResponse(url=url, expires_in=expires_in)


@router.get("/{project_id}/output-url", response_model=DownloadUrlResponse)
async def get_output_url(
    project_id: UUID, user: CurrentUser, repo: Repo, storage: Storage
) -> DownloadUrlResponse:
    row = await repo.get_project(user.id, project_id)
    if row is None:
        raise NotFoundError("Project not found")
    if row.get("status") != "completed":
        raise NotFoundError("Output not available yet")
    output_key = row.get("lipsync_output_key") or row.get("output_key")
    if not output_key:
        raise NotFoundError("Output not available yet")
    expires_in = get_settings().download_expires_seconds
    url = await storage.presign_get(
        output_key,
        expires_in=expires_in,
        download_filename=f"{row.get('title') or 'dubby-output'}-dubbed.mp4",
    )
    return DownloadUrlResponse(url=url, expires_in=expires_in)
