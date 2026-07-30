"""Segment listing, translated-text editing, and re-translation."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from ..auth import CurrentUser
from ..config import get_settings
from ..deps import Repo
from ..errors import BadRequestError, NotFoundError
from ..schemas import (
    SegmentOut,
    SegmentsBulkUpdate,
    SegmentsRetranslateRequest,
)
from ..worker.openai_client import OpenAIClient

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


@router.post("/retranslate", response_model=list[SegmentOut])
async def retranslate_segments(
    project_id: UUID,
    body: SegmentsRetranslateRequest,
    user: CurrentUser,
    repo: Repo,
) -> list[SegmentOut]:
    """Re-translate edited source lines and persist target_text updates."""
    project = await repo.get_project(user.id, project_id)
    if project is None:
        raise NotFoundError("Project not found")
    settings = get_settings()
    if not settings.openai_api_key:
        raise BadRequestError("OpenAI API key is not configured on the server")

    existing = {
        str(row["id"]): row for row in await repo.list_segments(user.id, project_id)
    }
    items: list[tuple[int, str, float]] = []
    ordered_ids: list[UUID] = []
    source_by_id: dict[str, str] = {}
    for seg in body.segments:
        row = existing.get(str(seg.id))
        if row is None:
            raise NotFoundError(f"Segment not found: {seg.id}")
        start_ms = int(row["start_ms"])
        end_ms = int(row["end_ms"])
        slot = max(0.0, (end_ms - start_ms) / 1000.0)
        items.append((int(row["idx"]), seg.source_text.strip(), slot))
        ordered_ids.append(seg.id)
        source_by_id[str(seg.id)] = seg.source_text.strip()

    client = OpenAIClient(settings)
    translations = await client.translate_batch(
        items,
        str(project["source_lang"]),
        str(project["target_lang"]),
    )
    updates: list[tuple[UUID, str, str | None]] = []
    for seg_id in ordered_ids:
        row = existing[str(seg_id)]
        target = (translations.get(int(row["idx"])) or "").strip()
        if not target:
            raise BadRequestError(f"Translation missing for segment {row['idx']}")
        updates.append((seg_id, target, source_by_id[str(seg_id)]))

    await repo.update_segment_texts(user.id, project_id, updates)
    rows = await repo.list_segments(user.id, project_id)
    return [SegmentOut.model_validate(r) for r in rows]
