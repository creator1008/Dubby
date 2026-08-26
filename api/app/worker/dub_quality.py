"""Shared dubbing quality helpers used by the SaaS worker and local step12."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from typing import Any

from .media import merge_speech_ranges


def cover_recognized_phrase_boundaries(
    ranges_ms: list[tuple[int, int]],
    segments: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Cover words omitted at the start or end of an ASR phrase.

    Word-level aligners can omit short initial or final words. Broader
    transcript segment boundaries supply those missing edges without filling
    pauses in the middle of the phrase.
    """
    adjusted = list(merge_speech_ranges(ranges_ms))
    for segment_start, segment_end in sorted(segments):
        overlaps = [
            index
            for index, (start, end) in enumerate(adjusted)
            if end > segment_start and start < segment_end
        ]
        if not overlaps:
            if segment_end > segment_start:
                adjusted.append((max(0, segment_start), segment_end))
                adjusted = merge_speech_ranges(adjusted)
            continue
        first = overlaps[0]
        last = overlaps[-1]
        adjusted[first] = (
            min(adjusted[first][0], segment_start),
            adjusted[first][1],
        )
        adjusted[last] = (
            adjusted[last][0],
            max(adjusted[last][1], segment_end),
        )
    return merge_speech_ranges(adjusted)


def matched_loudness_gain(source_level_db: float, tts_level_db: float) -> float:
    """Match generated speech to its source slot while avoiding clipping/noise.

    Bounds are wide enough for quiet vs loud speakers in the same clip; tighter
    clamps left soft lines far below shouts after matching.
    """
    return round(max(-14.0, min(14.0, source_level_db - tts_level_db)), 2)


def source_loudness_levels(
    segments: list[dict[str, Any]],
    speech_ranges: list[tuple[int, int]],
    segment_indices: set[int],
    measure_fn: Callable[[int, int], float],
) -> dict[int, float]:
    """Weighted loudness per segment using only voiced sub-ranges."""
    levels: dict[int, float] = {}
    for segment in segments:
        idx = int(segment["idx"])
        if idx not in segment_indices:
            continue
        start_ms = int(segment["start_ms"])
        end_ms = int(segment["end_ms"])
        voiced_ranges = [
            (max(start_ms, start), min(end_ms, end))
            for start, end in speech_ranges
            if end > start_ms and start < end_ms
        ]
        if not voiced_ranges:
            voiced_ranges = [(start_ms, end_ms)]
        weighted_power = 0.0
        total_duration = 0
        for range_start, range_end in voiced_ranges:
            duration = max(1, range_end - range_start)
            level_db = measure_fn(range_start, range_end)
            weighted_power += (10 ** (level_db / 10)) * duration
            total_duration += duration
        levels[idx] = round(
            10 * math.log10(max(weighted_power / max(1, total_duration), 1e-12)),
            2,
        )
    return levels


async def source_loudness_levels_async(
    segments: list[dict[str, Any]],
    speech_ranges: list[tuple[int, int]],
    segment_indices: set[int],
    measure_fn: Callable[[int, int], Awaitable[float]],
) -> dict[int, float]:
    """Async variant of :func:`source_loudness_levels`."""
    levels: dict[int, float] = {}
    for segment in segments:
        idx = int(segment["idx"])
        if idx not in segment_indices:
            continue
        start_ms = int(segment["start_ms"])
        end_ms = int(segment["end_ms"])
        voiced_ranges = [
            (max(start_ms, start), min(end_ms, end))
            for start, end in speech_ranges
            if end > start_ms and start < end_ms
        ]
        if not voiced_ranges:
            voiced_ranges = [(start_ms, end_ms)]
        weighted_power = 0.0
        total_duration = 0
        for range_start, range_end in voiced_ranges:
            duration = max(1, range_end - range_start)
            level_db = await measure_fn(range_start, range_end)
            weighted_power += (10 ** (level_db / 10)) * duration
            total_duration += duration
        levels[idx] = round(
            10 * math.log10(max(weighted_power / max(1, total_duration), 1e-12)),
            2,
        )
    return levels


def voice_removal_ranges(
    saved_ranges: list[tuple[int, int]],
    segment_bounds: list[tuple[int, int]],
    *,
    fill_interiors: bool = False,
) -> list[tuple[int, int]]:
    """Resolve selective voice-removal ranges from word and segment timestamps.

    ``fill_interiors=True`` (final dub mix) uses solid segment spans so TTS is
    never stacked on leftover original speech inside mid-phrase pauses.
    ``fill_interiors=False`` (editor preview) preserves larger interior gaps so
    sobbing / non-lexical sounds can remain audible outside word hits.
    """
    if fill_interiors or not saved_ranges:
        covered = merge_speech_ranges(segment_bounds)
    else:
        covered = cover_recognized_phrase_boundaries(saved_ranges, segment_bounds)
    return harden_voice_removal_ranges(
        covered,
        # Wider coalesce for dub so short residual syllables disappear.
        merge_gap_ms=900 if fill_interiors else 420,
        lead_ms=320 if fill_interiors else 280,
        trail_ms=320 if fill_interiors else 240,
    )


def harden_voice_removal_ranges(
    ranges_ms: list[tuple[int, int]],
    *,
    lead_ms: int = 280,
    trail_ms: int = 240,
    merge_gap_ms: int = 420,
) -> list[tuple[int, int]]:
    """Widen and coalesce ranges so short leftover syllables are scrubbed."""
    expanded = [
        (max(0, start - lead_ms), end + trail_ms)
        for start, end in ranges_ms
        if end > start
    ]
    return merge_speech_ranges(expanded, max_gap_ms=merge_gap_ms)


def next_start_by_segment_idx(
    segments: list[dict[str, Any]],
) -> dict[int, int | None]:
    """Map each segment idx to the following segment's start on the full timeline.

    Speakable-only neighbors incorrectly extend slots across passthrough rows
    and leave original speech under the dub.
    """
    ordered: list[tuple[int, int]] = []
    for row in segments:
        try:
            idx = int(row["idx"])
            start_ms = int(row["start_ms"])
        except (KeyError, TypeError, ValueError):
            continue
        ordered.append((idx, start_ms))
    ordered.sort(key=lambda pair: (pair[1], pair[0]))
    out: dict[int, int | None] = {}
    for i, (idx, _) in enumerate(ordered):
        out[idx] = ordered[i + 1][1] if i + 1 < len(ordered) else None
    return out


def cap_segment_ends_to_neighbors(
    rows: list[dict[str, Any]],
    *,
    pad_ms: int = 40,
    min_duration_ms: int = 80,
) -> list[dict[str, Any]]:
    """Prevent subtitle/TTS ends from overlapping the next segment start."""
    ordered = sorted(
        rows,
        key=lambda row: (
            int(row.get("start_ms", 0)),
            int(row.get("idx", 0)),
        ),
    )
    for i, row in enumerate(ordered):
        if i + 1 >= len(ordered):
            continue
        try:
            start = int(row["start_ms"])
            end = int(row["end_ms"])
            next_start = int(ordered[i + 1]["start_ms"]) - pad_ms
        except (KeyError, TypeError, ValueError):
            continue
        if end > next_start:
            row["end_ms"] = max(start + min_duration_ms, next_start)
    return rows


def final_voice_removal_bounds(
    items: list[dict[str, Any]],
    next_start_by_idx: dict[int, int | None],
) -> list[tuple[int, int]]:
    """Solid scrub windows covering source speech and any extended TTS slot."""
    bounds: list[tuple[int, int]] = []
    for item in items:
        seg = item.get("seg") or item
        try:
            idx = int(seg["idx"])
            start = int(seg["start_ms"])
            source_end = int(
                item.get("source_end_ms")
                or seg.get("source_end_ms")
                or item.get("end_ms")
                or seg.get("end_ms")
            )
            end = max(source_end, int(item.get("end_ms") or source_end))
        except (KeyError, TypeError, ValueError):
            continue
        nxt = next_start_by_idx.get(idx)
        if nxt is not None:
            end = min(end, max(start + 120, nxt))
        if end > start:
            bounds.append((start, end))
    return bounds
