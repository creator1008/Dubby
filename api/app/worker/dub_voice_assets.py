"""Per-segment dubbed-voice clips + speak speeds for the subtitle editor.

Stored under the project's R2 meta prefix so the segments API can attach
presigned preview URLs without a DB schema change.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..storage.r2 import R2Storage

logger = logging.getLogger(__name__)

MANIFEST_NAME = "dub_voice_manifest.json"


def dub_clip_filename(idx: int, extension: str) -> str:
    ext = (extension or "mp3").lstrip(".")
    return f"dub_seg_{int(idx)}.{ext}"


def dub_manifest_key(storage: R2Storage, source_key: str) -> str:
    return storage.meta_key_for_source(source_key, MANIFEST_NAME)


def dub_clip_key(storage: R2Storage, source_key: str, idx: int, extension: str) -> str:
    return storage.meta_key_for_source(
        source_key, dub_clip_filename(idx, extension)
    )


async def persist_dub_voice_assets(
    storage: R2Storage,
    *,
    source_key: str,
    items: list[dict[str, Any]],
) -> None:
    """Upload raw TTS clips + a speak-speed manifest for editor preview."""
    if not source_key or not items:
        return
    segments_meta: list[dict[str, Any]] = []
    for item in items:
        seg = item.get("seg") or {}
        try:
            idx = int(seg.get("idx", item.get("idx", -1)))
        except (TypeError, ValueError):
            continue
        if idx < 0:
            continue
        raw = item.get("raw")
        raw_path = Path(str(raw)) if raw is not None else None
        if raw_path is None or not raw_path.is_file():
            continue
        extension = raw_path.suffix.lstrip(".") or "mp3"
        clip_key = dub_clip_key(storage, source_key, idx, extension)
        try:
            await storage.upload_file(
                str(raw_path), clip_key, f"audio/{extension}"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("dub clip upload failed idx=%s: %s", idx, exc)
            continue
        speak_speed = 1.0
        try:
            speed = float(item.get("speak_speed") or 1.0)
            if speed > 0:
                speak_speed = speed
        except (TypeError, ValueError):
            pass
        clip_speak_speed = speak_speed
        try:
            clip_speed = float(item.get("tts_speak_speed") or speak_speed)
            if clip_speed > 0:
                clip_speak_speed = clip_speed
        except (TypeError, ValueError):
            pass
        source_end_ms = None
        for candidate in (seg.get("source_end_ms"), item.get("source_end_ms")):
            try:
                value = int(candidate) if candidate is not None else None
            except (TypeError, ValueError):
                value = None
            if value is not None and value > 0:
                source_end_ms = value
                break
        if source_end_ms is None:
            # Never treat a rate-shortened translation end as the original span.
            try:
                start_i = int(seg.get("start_ms") or 0)
                end_i = int(item.get("end_ms") or seg.get("end_ms") or 0)
                speed_i = float(item.get("speak_speed") or 1.0)
            except (TypeError, ValueError):
                start_i, end_i, speed_i = 0, 0, 1.0
            if end_i > start_i:
                translation_ms = end_i - start_i
                if abs(speed_i - 1.0) >= 0.001:
                    source_end_ms = start_i + int(
                        round(translation_ms * max(0.5, speed_i))
                    )
                else:
                    source_end_ms = end_i
        meta_row: dict[str, Any] = {
            "idx": idx,
            # Editor-facing rate (must survive re-dub refresh).
            "speak_speed": speak_speed,
            # Rate used to synthesize the uploaded clip (preview playback).
            "clip_speak_speed": clip_speak_speed,
            "audio_key": clip_key,
        }
        if source_end_ms is not None:
            meta_row["source_end_ms"] = source_end_ms
        emotion = item.get("emotion_tone") or seg.get("emotion_tone")
        if emotion:
            meta_row["emotion_tone"] = str(emotion)
        segments_meta.append(meta_row)
    if not segments_meta:
        return
    manifest = {"segments": segments_meta}
    manifest_key = dub_manifest_key(storage, source_key)
    try:
        await storage.upload_bytes(
            json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
            manifest_key,
            "application/json",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("dub voice manifest upload failed: %s", exc)


async def load_dub_voice_manifest(
    storage: R2Storage, source_key: str | None
) -> dict[int, dict[str, Any]]:
    """Return ``idx -> {speak_speed, audio_key}`` from R2, or empty."""
    if not source_key:
        return {}
    key = dub_manifest_key(storage, source_key)
    try:
        raw = await storage.download_bytes(key)
    except Exception:  # noqa: BLE001
        return {}
    if not raw:
        return {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    out: dict[int, dict[str, Any]] = {}
    for row in payload.get("segments") or []:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row["idx"])
        except (KeyError, TypeError, ValueError):
            continue
        out[idx] = row
    return out


async def update_manifest_speak_speeds(
    storage: R2Storage,
    *,
    source_key: str | None,
    speeds_by_idx: dict[int, float],
    source_end_by_idx: dict[int, int] | None = None,
) -> None:
    """Upsert speak_speed / source_end_ms into the dub-voice manifest."""
    if not source_key or (not speeds_by_idx and not source_end_by_idx):
        return
    by_idx = await load_dub_voice_manifest(storage, source_key)
    changed = False
    for idx, speed in speeds_by_idx.items():
        try:
            speed_f = float(speed)
        except (TypeError, ValueError):
            continue
        if speed_f <= 0:
            continue
        row = by_idx.get(idx)
        if not row:
            by_idx[idx] = {"idx": idx, "speak_speed": speed_f, "audio_key": ""}
            changed = True
            continue
        prev = row.get("speak_speed")
        try:
            prev_f = float(prev) if prev is not None else None
        except (TypeError, ValueError):
            prev_f = None
        if prev_f is None or abs(prev_f - speed_f) >= 0.001:
            row["speak_speed"] = speed_f
            changed = True
    if source_end_by_idx:
        for idx, end_ms in source_end_by_idx.items():
            try:
                end_i = int(end_ms)
            except (TypeError, ValueError):
                continue
            if end_i <= 0:
                continue
            row = by_idx.get(idx)
            if not row:
                by_idx[idx] = {
                    "idx": idx,
                    "speak_speed": 1.0,
                    "source_end_ms": end_i,
                    "audio_key": "",
                }
                changed = True
                continue
            if int(row.get("source_end_ms") or 0) != end_i:
                row["source_end_ms"] = end_i
                changed = True
    if not changed:
        return
    manifest = {
        "segments": [by_idx[idx] for idx in sorted(by_idx.keys())],
    }
    try:
        await storage.upload_bytes(
            json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
            dub_manifest_key(storage, source_key),
            "application/json",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("dub voice manifest speed update failed: %s", exc)


async def enrich_segments_with_dub_voice(
    storage: R2Storage,
    rows: list[dict[str, Any]],
    *,
    source_key: str | None,
    expires_in: int,
) -> list[dict[str, Any]]:
    """Attach dubbed_audio_url / speak_speed fields for the editor UI."""
    by_idx = await load_dub_voice_manifest(storage, source_key)
    enriched: list[dict[str, Any]] = []
    for row in rows:
        copied = dict(row)
        try:
            idx = int(copied.get("idx", -1))
        except (TypeError, ValueError):
            enriched.append(copied)
            continue
        meta = by_idx.get(idx) or {}
        speed_f: float | None = None
        for candidate in (copied.get("speak_speed"), meta.get("speak_speed")):
            try:
                value = float(candidate) if candidate is not None else None
            except (TypeError, ValueError):
                value = None
            if value is not None and value > 0:
                speed_f = value
                break
        if speed_f is not None:
            copied["speak_speed"] = speed_f
        else:
            copied["speak_speed"] = 1.0
        # Natural delivery is always 1.0×; reset returns here.
        copied["baseline_speak_speed"] = 1.0
        clip_speed = meta.get("clip_speak_speed", meta.get("speak_speed"))
        try:
            clip_f = float(clip_speed) if clip_speed is not None else None
        except (TypeError, ValueError):
            clip_f = None
        if clip_f is not None and clip_f > 0:
            copied["clip_speak_speed"] = clip_f
        source_end = meta.get("source_end_ms", copied.get("source_end_ms"))
        try:
            source_end_i = int(source_end) if source_end is not None else None
        except (TypeError, ValueError):
            source_end_i = None
        try:
            start_i = int(copied.get("start_ms") or 0)
            end_i = int(copied.get("end_ms") or 0)
        except (TypeError, ValueError):
            start_i, end_i = 0, 0
        if source_end_i is None or source_end_i <= start_i:
            # Recover original span from translation_end × speak_speed when needed.
            speed = float(speed_f or 1.0)
            translation_ms = max(120, end_i - start_i)
            if abs(speed - 1.0) >= 0.001 and translation_ms > 0:
                source_end_i = start_i + int(round(translation_ms * max(0.5, speed)))
            else:
                source_end_i = end_i if end_i > start_i else None
        if source_end_i is not None and source_end_i > 0:
            copied["source_end_ms"] = source_end_i
        emotion = meta.get("emotion_tone", copied.get("emotion_tone"))
        if emotion:
            copied["emotion_tone"] = str(emotion)
        audio_key = str(meta.get("audio_key") or "").strip()
        if audio_key:
            try:
                copied["dubbed_audio_url"] = await storage.presign_get(
                    audio_key, expires_in=expires_in
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("presign dub clip failed idx=%s: %s", idx, exc)
        enriched.append(copied)
    return enriched
