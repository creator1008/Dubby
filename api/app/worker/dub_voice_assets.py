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
        segments_meta.append(
            {
                "idx": idx,
                "speak_speed": speak_speed,
                "audio_key": clip_key,
            }
        )
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
) -> None:
    """Patch speak_speed values in the existing dub-voice manifest."""
    if not source_key or not speeds_by_idx:
        return
    by_idx = await load_dub_voice_manifest(storage, source_key)
    if not by_idx:
        return
    changed = False
    for idx, speed in speeds_by_idx.items():
        row = by_idx.get(idx)
        if not row:
            continue
        try:
            speed_f = float(speed)
        except (TypeError, ValueError):
            continue
        if speed_f <= 0:
            continue
        if abs(float(row.get("speak_speed") or 0) - speed_f) >= 0.001:
            row["speak_speed"] = speed_f
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
            copied["baseline_speak_speed"] = speed_f
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
