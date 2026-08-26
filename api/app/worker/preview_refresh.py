"""Regenerate per-segment dubbed preview clips after subtitle edits."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from ..config import Settings
from ..storage.r2 import R2Storage
from .dub_quality import matched_loudness_gain
from .dub_voice_assets import (
    dub_clip_key,
    load_dub_voice_manifest,
    upsert_manifest_segment,
)
from .elevenlabs_client import ElevenLabsClient
from .emotion import normalize_emotion_tone
from .media import measure_audio_loudness_db

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
    """TTS + loudness-match preview clips for the given (or all stale) segments.

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
        audio_key = str(meta.get("audio_key") or "").strip()
        # Refresh when missing, invalidated, or text drifted.
        if audio_key and meta_text == text and segment_ids is None:
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
    project_tone = normalize_emotion_tone(str(project.get("tone_style") or "calm"))
    refreshed: list[int] = []

    with tempfile.TemporaryDirectory(prefix="dubby-preview-") as tmp:
        scratch = Path(tmp)
        for row in targets:
            idx = int(row["idx"])
            text = str(row["target_text"]).strip()
            speaker = str(row.get("speaker_id") or "").strip() or "speaker_1"
            voice_id = voices.get(speaker) or next(iter(voices.values()))
            meta = by_idx_meta.get(idx) or {}
            emotion = str(
                row.get("emotion_tone") or meta.get("emotion_tone") or project_tone
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
            duration_ms = max(
                1,
                int(
                    (
                        await _probe_duration_ms(settings, str(raw_path))
                    )
                ),
            )
            tts_level = measure_audio_loudness_db(
                str(raw_path),
                0,
                duration_ms,
                ffmpeg_path=settings.ffmpeg_path,
            )
            try:
                source_level = float(meta["source_level_db"])
            except (KeyError, TypeError, ValueError):
                source_level = tts_level
            gain_db = matched_loudness_gain(source_level, tts_level)
            preview_path = scratch / f"seg_{idx}_preview.wav"
            await _apply_gain_wav(settings, str(raw_path), str(preview_path), gain_db)

            clip_key = dub_clip_key(storage, source_key, idx, "wav")
            await storage.upload_file(str(preview_path), clip_key, "audio/wav")
            meta_row: dict[str, Any] = {
                "idx": idx,
                "speak_speed": speak_speed,
                "clip_speak_speed": speak_speed,
                "audio_key": clip_key,
                "target_text": text,
                "gain_db": gain_db,
                "source_level_db": source_level,
                "tts_level_db": tts_level,
                "emotion_tone": emotion,
            }
            if meta.get("source_end_ms"):
                meta_row["source_end_ms"] = meta["source_end_ms"]
            await upsert_manifest_segment(
                storage, source_key=source_key, meta_row=meta_row
            )
            by_idx_meta[idx] = meta_row
            refreshed.append(idx)

    return refreshed


async def _probe_duration_ms(settings: Settings, path: str) -> float:
    import asyncio
    import json
    import subprocess

    def _run() -> float:
        result = subprocess.run(
            [
                settings.ffprobe_path,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            data = json.loads(result.stdout or "{}")
            return float(data.get("format", {}).get("duration") or 0) * 1000
        except (TypeError, ValueError, json.JSONDecodeError):
            return 1000.0

    return await asyncio.to_thread(_run)


async def _apply_gain_wav(
    settings: Settings, src: str, dst: str, gain_db: float
) -> None:
    import asyncio
    import subprocess

    def _run() -> None:
        cmd = [
            settings.ffmpeg_path,
            "-nostdin",
            "-y",
            "-i",
            src,
            "-af",
            f"volume={gain_db:.2f}dB",
            "-c:a",
            "pcm_s16le",
            "-ar",
            "44100",
            "-ac",
            "1",
            dst,
        ]
        subprocess.run(cmd, check=True, capture_output=True)

    await asyncio.to_thread(_run)


def parse_segment_id_set(raw_ids: list[UUID] | None) -> set[str] | None:
    if raw_ids is None:
        return None
    return {str(i) for i in raw_ids}
