"""Direct-to-R2 multipart upload presigning.

Flow (client-side):
 1. POST /v1/uploads/multipart            -> upload_id, key, part sizing
 2. POST /v1/uploads/multipart/parts      -> presigned URL per part (repeat)
 3. PUT each part body to its URL, collect ETag response headers
 4. POST /v1/uploads/multipart/complete   -> R2 assembles the object
    (or /abort to discard)

Keys are always derived server-side under ``users/{user_id}/...`` and every
key-accepting endpoint re-validates the prefix, so users cannot sign URLs
for other users' objects.
"""

from __future__ import annotations

from uuid import UUID

from botocore.exceptions import ClientError
from fastapi import APIRouter

from ..auth import CurrentUser
from ..config import get_settings
from ..deps import Repo, Storage
from ..errors import BadRequestError, NotFoundError
from ..schemas import (
    MultipartAbortRequest,
    MultipartCompleteRequest,
    MultipartCompleteResponse,
    MultipartCreateRequest,
    MultipartCreateResponse,
    MultipartSignPartRequest,
    MultipartSignPartResponse,
)

router = APIRouter(prefix="/v1/uploads/multipart", tags=["uploads"])


def _require_owned_key(storage, user_id, key: str) -> None:
    if not storage.key_belongs_to_user(key, user_id):
        raise BadRequestError("Key does not belong to the authenticated user")


@router.post("", response_model=MultipartCreateResponse)
async def create_multipart(
    body: MultipartCreateRequest, user: CurrentUser, repo: Repo, storage: Storage
) -> MultipartCreateResponse:
    settings = get_settings()
    if body.size_bytes > settings.max_upload_bytes:
        raise BadRequestError(
            f"File exceeds the {settings.max_upload_bytes} byte upload limit"
        )
    project = await repo.get_project(user.id, body.project_id)
    if project is None:
        raise NotFoundError("Project not found")

    key = storage.source_key(user.id, body.project_id, body.filename)
    upload_id = await storage.create_multipart_upload(key, body.content_type)

    await repo.update_project(
        user.id, body.project_id, {"source_key": key, "status": "uploading"}
    )
    return MultipartCreateResponse(
        upload_id=upload_id,
        key=key,
        part_size_bytes=settings.multipart_part_size_bytes,
        part_count=storage.part_count_for(body.size_bytes),
    )


@router.post("/{upload_id}/parts", response_model=MultipartSignPartResponse)
async def sign_part(
    upload_id: str, body: MultipartSignPartRequest, user: CurrentUser, storage: Storage
) -> MultipartSignPartResponse:
    _require_owned_key(storage, user.id, body.key)
    url = await storage.presign_upload_part(body.key, upload_id, body.part_number)
    return MultipartSignPartResponse(
        url=url,
        part_number=body.part_number,
        expires_in=storage.presign_expires_seconds,
    )


@router.post("/{upload_id}/complete", response_model=MultipartCompleteResponse)
async def complete_multipart(
    upload_id: str,
    body: MultipartCompleteRequest,
    user: CurrentUser,
    repo: Repo,
    storage: Storage,
) -> MultipartCompleteResponse:
    _require_owned_key(storage, user.id, body.key)
    parts = sorted(
        ({"PartNumber": p.part_number, "ETag": p.etag} for p in body.parts),
        key=lambda p: p["PartNumber"],
    )
    try:
        location = await storage.complete_multipart_upload(body.key, upload_id, parts)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "UploadError")
        raise BadRequestError(f"Could not complete upload: {code}")

    # Key layout: users/{uid}/projects/{pid}/source/{file}
    key_parts = body.key.split("/")
    if len(key_parts) >= 4 and key_parts[2] == "projects":
        try:
            project_id = UUID(key_parts[3])
        except ValueError:
            project_id = None
        if project_id is not None:
            await repo.update_project(user.id, project_id, {"status": "uploaded"})
    return MultipartCompleteResponse(key=body.key, location=location)


@router.post("/{upload_id}/abort", status_code=204)
async def abort_multipart(
    upload_id: str, body: MultipartAbortRequest, user: CurrentUser, storage: Storage
) -> None:
    _require_owned_key(storage, user.id, body.key)
    try:
        await storage.abort_multipart_upload(body.key, upload_id)
    except ClientError:
        # Aborting an already-aborted/expired upload is not an error.
        pass
