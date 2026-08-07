"""Project CRUD, scoped to the authenticated user."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from uuid import UUID
from urllib.parse import quote

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse

from ..auth import CurrentUser
from ..config import get_settings
from ..deps import Repo, Storage
from ..errors import BadRequestError, NotFoundError
from ..remote_media import RemoteMediaError, ingest_remote_media
from ..schemas import (
    DownloadUrlResponse,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    SourceFromUrlRequest,
)
from ..storage.r2 import sanitize_filename

router = APIRouter(prefix="/v1/projects", tags=["projects"])
logger = logging.getLogger("dubby.projects")

_DUB_VOICE_META = "dub_voice_ids.json"


async def _resolve_project(user: CurrentUser, repo: Repo, project_id: UUID):
    """Owner-scoped project, or any project when the caller is an admin."""
    row = await repo.get_project(user.id, project_id)
    if row is None and user.is_admin:
        row = await repo.get_project_for_worker(project_id)
    return row


async def _save_dub_voice_ids(
    storage: Storage, owner_id: UUID, project_id: UUID, voice_ids: list[str]
) -> None:
    key = storage.project_meta_key(owner_id, project_id, _DUB_VOICE_META)
    payload = json.dumps(
        {"voice_ids": [str(v).strip() for v in voice_ids if str(v).strip()][:8]}
    ).encode("utf-8")
    await storage.upload_bytes(payload, key, content_type="application/json")


async def _load_dub_voice_ids(
    storage: Storage, owner_id: UUID, project_id: UUID
) -> list[str]:
    key = storage.project_meta_key(owner_id, project_id, _DUB_VOICE_META)
    raw = await storage.download_bytes(key)
    if not raw:
        return []
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    voices = data.get("voice_ids") if isinstance(data, dict) else data
    if not isinstance(voices, list):
        return []
    return [str(v).strip() for v in voices if str(v).strip()][:8]


async def _with_dub_voices(
    storage: Storage, owner_id: UUID, row: dict
) -> dict:
    if row.get("dub_voice_ids"):
        return row
    try:
        voices = await _load_dub_voice_ids(storage, owner_id, UUID(str(row["id"])))
    except Exception:  # noqa: BLE001 - project reads must not fail on meta miss
        logger.warning("could not load dub_voice_ids for project %s", row.get("id"))
        voices = []
    return {**row, "dub_voice_ids": voices}


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    user: CurrentUser, repo: Repo, storage: Storage
) -> list[ProjectOut]:
    rows = await repo.list_projects(user.id)
    enriched = [await _with_dub_voices(storage, user.id, r) for r in rows]
    return [ProjectOut.model_validate(r) for r in enriched]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate, user: CurrentUser, repo: Repo, storage: Storage
) -> ProjectOut:
    row = await repo.create_project(
        user.id,
        title=body.title,
        source_lang=body.source_lang,
        target_lang=body.target_lang,
        subtitle_mode=body.subtitle_mode,
        tone_style=body.tone_style,
        diarization_enabled=body.diarization_enabled,
        dub_voice_ids=body.dub_voice_ids,
    )
    voices = [
        str(v).strip() for v in (body.dub_voice_ids or []) if str(v).strip()
    ][:8]
    if voices:
        try:
            await _save_dub_voice_ids(storage, user.id, UUID(str(row["id"])), voices)
        except Exception:  # noqa: BLE001 - project create should still succeed
            logger.exception("failed to persist dub_voice_ids sidecar")
    row = {**row, "dub_voice_ids": voices}
    return ProjectOut.model_validate(row)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: UUID, user: CurrentUser, repo: Repo, storage: Storage
) -> ProjectOut:
    row = await _resolve_project(user, repo, project_id)
    if row is None:
        raise NotFoundError("Project not found")
    owner_id = UUID(str(row.get("owner_id") or user.id))
    row = await _with_dub_voices(storage, owner_id, row)
    return ProjectOut.model_validate(row)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: UUID,
    body: ProjectUpdate,
    user: CurrentUser,
    repo: Repo,
    storage: Storage,
) -> ProjectOut:
    fields = body.model_dump(exclude_unset=True)
    row = await repo.update_project(user.id, project_id, fields)
    if row is None:
        raise NotFoundError("Project not found")
    if "dub_voice_ids" in fields:
        voices = [
            str(v).strip()
            for v in (fields.get("dub_voice_ids") or [])
            if str(v).strip()
        ][:8]
        try:
            await _save_dub_voice_ids(storage, user.id, project_id, voices)
        except Exception:  # noqa: BLE001
            logger.exception("failed to update dub_voice_ids sidecar")
        row = {**row, "dub_voice_ids": voices}
    else:
        row = await _with_dub_voices(storage, user.id, row)
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


@router.post("/{project_id}/source-from-url", response_model=ProjectOut)
async def source_from_url(
    project_id: UUID,
    body: SourceFromUrlRequest,
    user: CurrentUser,
    repo: Repo,
    storage: Storage,
) -> ProjectOut:
    """Download a remote video (direct MP4/WebM or YouTube/Facebook/TikTok) into R2."""
    project = await repo.get_project(user.id, project_id)
    if project is None:
        raise NotFoundError("Project not found")

    settings = get_settings()
    with tempfile.TemporaryDirectory(prefix="dubby-url-") as tmp:
        dest_dir = Path(tmp)
        try:
            source = await ingest_remote_media(
                body.url.strip(),
                dest_dir,
                max_bytes=settings.max_source_bytes,
            )
        except RemoteMediaError as exc:
            raise BadRequestError(str(exc)) from exc

        content_type = {
            ".mp4": "video/mp4",
            ".webm": "video/webm",
            ".mov": "video/quicktime",
            ".mkv": "video/x-matroska",
            ".m4a": "audio/mp4",
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
        }.get(source.suffix.lower(), "application/octet-stream")
        key = storage.source_key(user.id, project_id, source.name)
        await storage.upload_file(str(source), key, content_type=content_type)

    row = await repo.update_project(
        user.id,
        project_id,
        {"source_key": key, "status": "uploaded"},
    )
    if row is None:
        raise NotFoundError("Project not found")
    return ProjectOut.model_validate(row)


@router.get("/{project_id}/source-url", response_model=DownloadUrlResponse)
async def get_source_url(
    project_id: UUID, user: CurrentUser, repo: Repo, storage: Storage
) -> DownloadUrlResponse:
    """Presigned GET for the uploaded source video (Before preview)."""
    row = await _resolve_project(user, repo, project_id)
    if row is None:
        raise NotFoundError("Project not found")
    source_key = row.get("source_key")
    if not source_key:
        raise NotFoundError("Source not uploaded yet")
    expires_in = get_settings().download_expires_seconds
    url = await storage.presign_get(source_key, expires_in=expires_in)
    return DownloadUrlResponse(url=url, expires_in=expires_in)


@router.get("/{project_id}/voice-removed-url", response_model=DownloadUrlResponse)
async def get_voice_removed_url(
    project_id: UUID, user: CurrentUser, repo: Repo, storage: Storage
) -> DownloadUrlResponse:
    """Presigned GET for the speech-scrubbed preview built during extract."""
    row = await _resolve_project(user, repo, project_id)
    if row is None:
        raise NotFoundError("Project not found")
    source_key = row.get("source_key")
    if not source_key:
        raise NotFoundError("Source not uploaded yet")
    key = storage.meta_key_for_source(str(source_key), "voice_removed.mp4")
    if await storage.head_object(key) is None:
        raise NotFoundError("Voice-removed preview not available yet")
    expires_in = get_settings().download_expires_seconds
    url = await storage.presign_get(key, expires_in=expires_in)
    return DownloadUrlResponse(url=url, expires_in=expires_in)


@router.get("/{project_id}/output-url", response_model=DownloadUrlResponse)
async def get_output_url(
    project_id: UUID, user: CurrentUser, repo: Repo, storage: Storage
) -> DownloadUrlResponse:
    row = await _resolve_project(user, repo, project_id)
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


@router.get("/{project_id}/output")
async def download_output_file(
    project_id: UUID, user: CurrentUser, repo: Repo, storage: Storage
) -> StreamingResponse:
    """Stream the dubbed file through the API so the SPA never leaves the page.

    Mobile browsers often open signed R2 URLs in an external browser / blank the
    PWA when using iframe or target=_blank. Authenticated same-origin fetch →
    blob download keeps the editor screen.
    """
    row = await _resolve_project(user, repo, project_id)
    if row is None:
        raise NotFoundError("Project not found")
    if row.get("status") != "completed":
        raise NotFoundError("Output not available yet")
    output_key = row.get("lipsync_output_key") or row.get("output_key")
    if not output_key:
        raise NotFoundError("Output not available yet")

    display_name = f"{row.get('title') or 'dubby-output'}-dubbed.mp4"
    ascii_name = sanitize_filename(display_name)
    try:
        body = await storage.open_object_stream(str(output_key))
    except Exception as exc:  # noqa: BLE001
        raise NotFoundError("Output not available yet") from exc

    def iter_chunks():
        try:
            while True:
                chunk = body.read(256 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                body.close()
            except Exception:  # noqa: BLE001
                pass

    disposition = (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(display_name)}"
    )
    return StreamingResponse(
        iter_chunks(),
        media_type="video/mp4",
        headers={
            "Content-Disposition": disposition,
            "Cache-Control": "no-store",
        },
    )
