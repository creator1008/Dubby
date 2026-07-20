"""Segment timing math: fitting TTS clips into their transcript slots.

Pure functions, unit-tested without ffmpeg. A dubbed clip that runs longer
than its segment slot is sped up (never slowed down) up to a configurable
cap; whatever still does not fit is allowed to spill over and simply
overlaps the following silence/segment in the mix.
"""

from __future__ import annotations

from dataclasses import dataclass

# ffmpeg's atempo filter only accepts factors in [0.5, 2.0]; larger factors
# must be decomposed into a chain.
_ATEMPO_MIN = 0.5
_ATEMPO_MAX = 2.0


def fit_speedup(clip_seconds: float, slot_seconds: float, max_speedup: float) -> float:
    """Tempo factor (>= 1.0) that fits ``clip_seconds`` into ``slot_seconds``.

    Returns 1.0 when the clip already fits (we never slow speech down), and
    never more than ``max_speedup``.
    """
    if clip_seconds <= 0 or slot_seconds <= 0:
        return 1.0
    factor = clip_seconds / slot_seconds
    if factor <= 1.0:
        return 1.0
    return min(factor, max_speedup)


def atempo_chain(factor: float) -> list[str]:
    """Decompose a tempo factor into valid ``atempo=X`` filter steps."""
    if factor <= 0:
        raise ValueError("tempo factor must be positive")
    steps: list[str] = []
    remaining = factor
    while remaining > _ATEMPO_MAX:
        steps.append(f"atempo={_ATEMPO_MAX}")
        remaining /= _ATEMPO_MAX
    while remaining < _ATEMPO_MIN:
        steps.append(f"atempo={_ATEMPO_MIN}")
        remaining /= _ATEMPO_MIN
    steps.append(f"atempo={remaining:.6f}".rstrip("0").rstrip("."))
    return steps


def slot_seconds(start_ms: int, end_ms: int) -> float:
    return max(0.0, (end_ms - start_ms) / 1000.0)


@dataclass(frozen=True)
class FitDecision:
    tempo: float
    backend: str
    output_seconds: float
    warning: str | None = None


def choose_fit_policy(
    clip_seconds: float,
    slot_seconds_value: float,
    *,
    min_tempo: float,
    atempo_max: float,
    max_speedup: float,
    rubberband_available: bool,
) -> FitDecision:
    """Choose a bounded tempo and always cap output at the non-overlap slot."""
    if clip_seconds <= 0 or slot_seconds_value <= 0:
        return FitDecision(1.0, "atempo", max(0.0, slot_seconds_value), "invalid_duration")
    requested = clip_seconds / slot_seconds_value
    tempo = min(max(requested, min_tempo), max_speedup)
    backend = "atempo"
    warning = None
    if tempo > atempo_max:
        if rubberband_available:
            backend = "rubberband"
        else:
            tempo = atempo_max
            warning = "rubberband_unavailable"
    fitted = clip_seconds / tempo
    if fitted > slot_seconds_value + 0.02:
        warning = "speech_truncated_to_prevent_overlap"
    elif requested < min_tempo:
        warning = "speech_not_extended_beyond_quality_limit"
    return FitDecision(tempo, backend, slot_seconds_value, warning)


def safe_slot_seconds(start_ms: int, end_ms: int, next_start_ms: int | None) -> float:
    """Never let a clip extend into the next transcript segment."""
    safe_end = min(end_ms, next_start_ms) if next_start_ms is not None else end_ms
    return slot_seconds(start_ms, safe_end)
