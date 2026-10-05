"""Full-document translation re-attached by stable segment_id.

The 3.0.12 scramble happened when a whole-document translation was sliced
into N caption rows by paragraph/order. This module forbids that:

1. Source units keep ``segment_id`` (same as STT idx) + speaker + timestamps.
2. The model may write a full-document translation for cohesion.
3. Re-split is ONLY by matching ``segment_id`` / ``idx`` on each output row.
4. Extra rows without a known id are dropped. Missing ids are retried one-by-one.
5. ``emotion_tag`` is never taken from the translator — it stays on the source unit.
"""

from __future__ import annotations

from typing import Any

from . import errors
from .errors import PipelineError
from .translation_align import idxs_needing_retranslate


def build_source_units(
    items: list[tuple[int, str, float]],
    *,
    speakers: list[str] | None = None,
    emotion_tags: list[dict[str, Any] | None] | None = None,
    starts_ms: list[int] | None = None,
    ends_ms: list[int] | None = None,
) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for i, (idx, text, seconds) in enumerate(items):
        units.append(
            {
                "segment_id": int(idx),
                "idx": int(idx),
                "speaker_id": (speakers[i] if speakers and i < len(speakers) else None),
                "start_ms": starts_ms[i] if starts_ms and i < len(starts_ms) else None,
                "end_ms": ends_ms[i] if ends_ms and i < len(ends_ms) else None,
                "source_text": text,
                "target_seconds": round(max(0.35, float(seconds)), 2),
                "emotion_tag": emotion_tags[i] if emotion_tags and i < len(emotion_tags) else None,
            }
        )
    return units


def document_context_from_units(units: list[dict[str, Any]]) -> str:
    """Readable full transcript with stable ids — context only, never sliced."""
    lines: list[str] = []
    for unit in units:
        sid = unit.get("segment_id", unit.get("idx"))
        speaker = unit.get("speaker_id") or "speaker"
        start = unit.get("start_ms")
        end = unit.get("end_ms")
        timing = ""
        if isinstance(start, int) and isinstance(end, int):
            timing = f" t={start}-{end}"
        lines.append(
            f"[segment_id={sid} speaker={speaker}{timing}] {unit.get('source_text') or ''}"
        )
    return "\n".join(lines)


def _row_segment_id(item: dict[str, Any]) -> int | None:
    for key in ("segment_id", "idx", "id"):
        if key not in item:
            continue
        try:
            return int(item[key])
        except (TypeError, ValueError):
            continue
    return None


def parse_translations_by_segment_id(
    payload: object,
    expected_ids: list[int],
    *,
    require_all: bool = False,
) -> dict[int, str]:
    """Map model JSON → {segment_id: text}. Never assign by array position."""
    expected = {int(i) for i in expected_ids}
    rows: list[Any]
    if isinstance(payload, dict):
        raw = payload.get("translations", payload.get("segments"))
        if isinstance(raw, list):
            rows = raw
        else:
            raise PipelineError(
                errors.TRANSLATION_FAILED,
                "translation response was not the expected JSON shape",
                retryable=True,
            )
    elif isinstance(payload, list):
        rows = payload
    else:
        raise PipelineError(
            errors.TRANSLATION_FAILED,
            "translation response was not the expected JSON shape",
            retryable=True,
        )

    result: dict[int, str] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        sid = _row_segment_id(item)
        if sid is None or sid not in expected:
            # Drop leftovers / invented ids — do not zip onto remaining slots.
            continue
        text = str(item.get("text") or item.get("target_text") or "").strip()
        if not text:
            continue
        result[sid] = text

    if require_all:
        missing = [i for i in expected_ids if i not in result]
        if missing:
            raise PipelineError(
                errors.TRANSLATION_FAILED,
                f"translation missing segments {missing[:10]}",
                retryable=True,
            )
    return result


def missing_segment_ids(expected_ids: list[int], translated: dict[int, str]) -> list[int]:
    return [i for i in expected_ids if i not in translated or not str(translated.get(i) or "").strip()]


def attach_translations(
    units: list[dict[str, Any]],
    translated: dict[int, str],
) -> list[dict[str, Any]]:
    """Copy target text onto units; emotion_tag / timestamps stay from STT."""
    attached: list[dict[str, Any]] = []
    for unit in units:
        sid = int(unit["segment_id"])
        row = dict(unit)
        row["target_text"] = str(translated.get(sid) or "").strip()
        attached.append(row)
    return attached


def ids_needing_retranslate(
    items: list[tuple[int, str, float]],
    translated: dict[int, str],
    source_lang: str,
    target_lang: str,
) -> list[int]:
    """Missing ids plus heuristic mis-assignments (never positional repair)."""
    missing = missing_segment_ids([idx for idx, _, _ in items], translated)
    bad = idxs_needing_retranslate(items, translated, source_lang, target_lang)
    return list(dict.fromkeys([*missing, *bad]))
