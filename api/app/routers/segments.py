"""Segment listing, translated-text editing, and re-translation."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter

from ..auth import CurrentUser
from ..config import get_settings
from ..deps import Repo, Storage
from ..errors import BadRequestError, NotFoundError
from ..schemas import (
    SegmentOut,
    SegmentsBulkUpdate,
    SegmentsRetranslateRequest,
)
from ..worker.dub_voice_assets import enrich_segments_with_dub_voice
from ..worker.openai_client import OpenAIClient
from ..worker.utterance_pipeline import UtteranceChunk

router = APIRouter(prefix="/v1/projects/{project_id}/segments", tags=["segments"])


async def _segment_outs(
    rows: list[dict[str, Any]],
    *,
    storage: Storage,
    source_key: str | None,
) -> list[SegmentOut]:
    settings = get_settings()
    enriched = await enrich_segments_with_dub_voice(
        storage,
        rows,
        source_key=source_key,
        expires_in=settings.download_expires_seconds,
    )
    return [SegmentOut.model_validate(r) for r in enriched]


@router.get("", response_model=list[SegmentOut])
async def list_segments(
    project_id: UUID, user: CurrentUser, repo: Repo, storage: Storage
) -> list[SegmentOut]:
    owned = await repo.get_project(user.id, project_id)
    if owned is None:
        if not user.is_admin or await repo.get_project_for_worker(project_id) is None:
            raise NotFoundError("Project not found")
        rows = await repo.list_segments_for_worker(project_id)
        worker_project = await repo.get_project_for_worker(project_id)
        source_key = (
            str(worker_project.get("source_key") or "") if worker_project else None
        )
    else:
        rows = await repo.list_segments(user.id, project_id)
        source_key = str(owned.get("source_key") or "") or None
    return await _segment_outs(rows, storage=storage, source_key=source_key)


@router.put("", response_model=list[SegmentOut])
async def update_segments(
    project_id: UUID,
    body: SegmentsBulkUpdate,
    user: CurrentUser,
    repo: Repo,
    storage: Storage,
) -> list[SegmentOut]:
    """Bulk-update segment texts, then return the full ordered segment list
    so the editor can re-render from truth."""
    project = await repo.get_project(user.id, project_id)
    if project is None:
        raise NotFoundError("Project not found")
    await repo.update_segment_texts(
        user.id,
        project_id,
        [(seg.id, seg.target_text, seg.source_text) for seg in body.segments],
    )
    rows = await repo.list_segments(user.id, project_id)
    return await _segment_outs(
        rows,
        storage=storage,
        source_key=str(project.get("source_key") or "") or None,
    )


@router.post("/retranslate", response_model=list[SegmentOut])
async def retranslate_segments(
    project_id: UUID,
    body: SegmentsRetranslateRequest,
    user: CurrentUser,
    repo: Repo,
    storage: Storage,
) -> list[SegmentOut]:
    """Correct edited sources in context, full-document translate, re-align."""
    project = await repo.get_project(user.id, project_id)
    if project is None:
        raise NotFoundError("Project not found")
    settings = get_settings()
    if not settings.openai_api_key:
        raise BadRequestError("OpenAI API key is not configured on the server")

    rows = await repo.list_segments(user.id, project_id)
    if not rows:
        raise NotFoundError("No segments to retranslate")
    existing = {str(row["id"]): row for row in rows}
    edits = {str(seg.id): seg.source_text.strip() for seg in body.segments}
    for seg_id in edits:
        if seg_id not in existing:
            raise NotFoundError(f"Segment not found: {seg_id}")

    chunks: list[UtteranceChunk] = []
    ordered_ids: list[UUID] = []
    for row in sorted(rows, key=lambda r: int(r["idx"])):
        sid = str(row["id"])
        text = edits.get(sid, str(row.get("source_text") or "")).strip()
        if not text:
            continue
        chunks.append(
            UtteranceChunk(
                start_ms=int(row["start_ms"]),
                end_ms=int(row["end_ms"]),
                text=text,
                speaker_id=str(row.get("speaker_id") or "speaker_0"),
                words=(),
            )
        )
        ordered_ids.append(UUID(sid) if not isinstance(row["id"], UUID) else row["id"])

    if not chunks:
        raise BadRequestError("No source text to translate")

    client = OpenAIClient(settings)
    correction_items = [(i, c.text) for i, c in enumerate(chunks)]
    try:
        corrected = await client.correct_transcript(
            correction_items, str(project["source_lang"])
        )
        chunks = [
            UtteranceChunk(
                start_ms=c.start_ms,
                end_ms=c.end_ms,
                text=(corrected.get(i) or c.text).strip() or c.text,
                speaker_id=c.speaker_id,
                words=c.words,
            )
            for i, c in enumerate(chunks)
        ]
    except Exception:
        pass

    full_source = "\n".join(f"[{i}] {c.text}" for i, c in enumerate(chunks))
    translate_items = [
        (
            i,
            c.text,
            max(0.35, (c.end_ms - c.start_ms) / 1000.0),
        )
        for i, c in enumerate(chunks)
    ]
    aligned_map = await client.translate_batch(
        translate_items,
        str(project["source_lang"]),
        str(project["target_lang"]),
        document_context=full_source,
    )

    updates: list[tuple[UUID, str, str | None]] = []
    for index, (seg_id, chunk) in enumerate(zip(ordered_ids, chunks)):
        clean_target = (aligned_map.get(index) or "").strip()
        if not clean_target:
            raise BadRequestError(f"Translation missing for segment {seg_id}")
        updates.append((seg_id, clean_target, chunk.text))

    await repo.update_segment_texts(user.id, project_id, updates)
    out_rows = await repo.list_segments(user.id, project_id)
    return await _segment_outs(
        out_rows,
        storage=storage,
        source_key=str(project.get("source_key") or "") or None,
    )
