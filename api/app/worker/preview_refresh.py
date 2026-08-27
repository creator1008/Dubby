"""Regenerate per-segment dubbed preview clips after subtitle edits.

Runs inside the API container (no ffmpeg/Demucs). Uploads raw ElevenLabs MP3
clips so editor preview works without the worker image.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from ..config import Settings
from ..storage.r2 import R2Storage
from .dub_voice_assets import (
    dub_clip_key,
    load_dub_voice_manifest,
    upsert_manifest_segment,
)
from .elevenlabs_client import ElevenLabsClient
from .emotion import resolve_segment_emotion

logger = logging.getLogger(__name__)


def _speaker_voice_map(
    rows: list[dict[str, Any]],
    voice_ids: list[str],
    fallback: str | None,
) -> dict[str, str]:
    speakers: list[str] = []
    seen: set[str] = set()
    for row in sorted(rows, key=lambda r: int(r.get("idx", 0))):
        sid = str(row.get("speaker_id") or "").strip() or "speaker_1"
        if sid not in seen:
            seen.add(sid)
            speakers.append(sid)
    selected = [v for v in voice_ids if v]
    if not selected and fallback:
        selected = [fallback]
    if not selected:
        return {}
    default = selected[0]
    return {
        speaker: selected[i] if i < len(selected) else default
        for i, speaker in enumerate(speakers)
    }


async def refresh_preview_clips(
    *,
    storage: R2Storage,
    settings: Settings,
    project: dict[str, Any],
    rows: list[dict[str, Any]],
    segment_ids: set[str] | None = None,
    voice_ids: list[str] | None = None,
) -> list[int]:
    """TTS preview clips for the given (or all stale) segments.

    Returns idxs that were refreshed.
    """
    source_key = str(project.get("source_key") or "").strip()
    if not source_key:
        return []
    if not settings.elevenlabs_api_key:
        raise RuntimeError("ElevenLabs API key is not configured")

    by_idx_meta = await load_dub_voice_manifest(storage, source_key)
    targets: list[dict[str, Any]] = []
    for row in rows:
        sid = str(row.get("id") or "")
        if segment_ids is not None and sid not in segment_ids:
            continue
        text = str(row.get("target_text") or "").strip()
        if not text:
            continue
        try:
            idx = int(row["idx"])
        except (KeyError, TypeError, ValueError):
            continue
        meta = by_idx_meta.get(idx) or {}
        meta_text = str(meta.get("target_text") or "").strip()
        meta_tone = str(meta.get("emotion_tone") or "").strip()
        row_tone = str(row.get("emotion_tone") or "").strip()
        audio_key = str(meta.get("audio_key") or "").strip()
        # Refresh when missing, invalidated, text/tone drifted, or explicitly asked.
        if (
            segment_ids is None
            and audio_key
            and meta_text == text
            and (not row_tone or not meta_tone or meta_tone == row_tone)
        ):
            continue
        targets.append(row)

    if not targets:
        return []

    voices = _speaker_voice_map(
        rows,
        list(voice_ids or []),
        str(settings.elevenlabs_voice_id or "").strip() or None,
    )
    if not voices:
        raise RuntimeError(
            "No dubbing voice available. Select My Voice Box voices first."
        )

    client = ElevenLabsClient(settings)
    target_lang = str(project.get("target_lang") or "")
    project_tone = resolve_segment_emotion(
        None, project_tone=str(project.get("tone_style") or "calm")
    )
    refreshed: list[int] = []

    with tempfile.TemporaryDirectory(prefix="dubby-preview-") as tmp:
        scratch = Path(tmp)
        for row in targets:
            idx = int(row["idx"])
            text = str(row["target_text"]).strip()
            speaker = str(row.get("speaker_id") or "").strip() or "speaker_1"
            voice_id = voices.get(speaker) or next(iter(voices.values()))
            meta = by_idx_meta.get(idx) or {}
            emotion = resolve_segment_emotion(
                str(meta.get("emotion_tone") or row.get("emotion_tone") or ""),
                project_tone=project_tone,
                user_set=bool(meta.get("emotion_user_set")),
            )
            try:
                speak_speed = float(
                    row.get("speak_speed") or meta.get("speak_speed") or 1.0
                )
            except (TypeError, ValueError):
                speak_speed = 1.0
            speak_speed = max(0.7, min(1.2, speak_speed if speak_speed > 0 else 1.0))

            raw_path = scratch / f"seg_{idx}.mp3"
            await client.tts_to_file(
                text,
                voice_id,
                str(raw_path),
                emotion,
                target_lang,
                speed=speak_speed,
            )

            # API image has no ffmpeg — upload raw MP3 for editor preview.
            clip_key = dub_clip_key(storage, source_key, idx, "mp3")
            await storage.upload_file(str(raw_path), clip_key, "audio/mpeg")
            meta_row: dict[str, Any] = {
                "idx": idx,
                "speak_speed": speak_speed,
                "clip_speak_speed": speak_speed,
                "audio_key": clip_key,
                "target_text": text,
            }
            if meta.get("emotion_user_set"):
                meta_row["emotion_tone"] = emotion
                meta_row["emotion_user_set"] = True
            for key in ("source_end_ms", "gain_db", "source_level_db", "tts_level_db"):
                if meta.get(key) is not None:
                    meta_row[key] = meta[key]
            await upsert_manifest_segment(
                storage, source_key=source_key, meta_row=meta_row
            )
            by_idx_meta[idx] = meta_row
            refreshed.append(idx)

    return refreshed


def parse_segment_id_set(raw_ids: list[UUID] | None) -> set[str] | None:
    if raw_ids is None:
        return None
    return {str(i) for i in raw_ids}
