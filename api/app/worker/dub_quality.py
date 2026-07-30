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
    """Match generated speech to its source slot while avoiding clipping/noise."""
    return round(max(-8.0, min(6.0, source_level_db - tts_level_db)), 2)


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
) -> list[tuple[int, int]]:
    """Resolve selective voice-removal ranges from word and segment timestamps."""
    if not saved_ranges:
        covered = merge_speech_ranges(segment_bounds)
    else:
        covered = cover_recognized_phrase_boundaries(saved_ranges, segment_bounds)
    return harden_voice_removal_ranges(covered)


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
