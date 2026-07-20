"""Segment listing and translated-text editing."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from ..auth import CurrentUser
from ..deps import Repo
from ..errors import NotFoundError
from ..schemas import SegmentOut, SegmentsBulkUpdate

router = APIRouter(prefix="/v1/projects/{project_id}/segments", tags=["segments"])


@router.get("", response_model=list[SegmentOut])
async def list_segments(
    project_id: UUID, user: CurrentUser, repo: Repo
) -> list[SegmentOut]:
    if await repo.get_project(user.id, project_id) is None:
        raise NotFoundError("Project not found")
    rows = await repo.list_segments(user.id, project_id)
    return [SegmentOut.model_validate(r) for r in rows]


@router.put("", response_model=list[SegmentOut])
async def update_segments(
    project_id: UUID, body: SegmentsBulkUpdate, user: CurrentUser, repo: Repo
) -> list[SegmentOut]:
    """Bulk-update segment texts, then return the full ordered segment list
    so the editor can re-render from truth."""
    if await repo.get_project(user.id, project_id) is None:
        raise NotFoundError("Project not found")
    await repo.update_segment_texts(
        user.id,
        project_id,
        [(seg.id, seg.target_text, seg.source_text) for seg in body.segments],
    )
    rows = await repo.list_segments(user.id, project_id)
    return [SegmentOut.model_validate(r) for r in rows]
