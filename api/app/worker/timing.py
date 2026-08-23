"""Segment timing math: fitting TTS clips into their transcript slots.

Pure functions, unit-tested without ffmpeg. Prefer synthesis-time speaking
rate (ElevenLabs ``speed``) and pitch-preserving rubberband over mechanical
``atempo`` chains, which thin the voice at high factors.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ffmpeg's atempo filter only accepts factors in [0.5, 2.0]; larger factors
# must be decomposed into a chain.
_ATEMPO_MIN = 0.5
_ATEMPO_MAX = 2.0

# ElevenLabs voice_settings.speed API bounds (HTTP TTS).
ELEVENLABS_SPEAK_SPEED_MIN = 0.7
ELEVENLABS_SPEAK_SPEED_MAX = 1.2

# Approx spoken characters/sec at speed=1.0 (used to pick initial speak_speed
# without a calibration TTS round-trip). Tuned for cost+latency, not perfection.
_CHARS_PER_SECOND: dict[str, float] = {
    "en": 14.5,
    "es": 14.0,
    "fr": 13.5,
    "pt": 13.5,
    "de": 13.0,
    "id": 13.0,
    "ms": 13.0,
    "tr": 12.5,
    "vi": 12.0,
    "ru": 11.5,
    "ar": 11.0,
    "ur": 11.0,
    "ko": 8.5,
    "ja": 7.5,
    "zh": 6.5,
    "ta": 10.0,
    "th": 11.5,
    "my": 10.5,
}


def estimate_tts_seconds(text: str, language: str = "") -> float:
    """Cheap pre-TTS duration estimate from character density."""
    compact = "".join(text.split())
    if not compact:
        return 0.0
    lang = language.strip().lower().split("-", 1)[0]
    cps = _CHARS_PER_SECOND.get(lang, 12.0)
    # Light pause budget for sentence punctuation.
    pauses = sum(compact.count(mark) for mark in ".!?。？！…")
    return max(0.35, len(compact) / cps + 0.12 * pauses)


def source_relative_pace(
    source_text: str,
    source_lang: str,
    slot_seconds_value: float,
) -> float:
    """How fast the original speaker was vs average TTS for that language.

    Values > 1 mean the source was delivered faster than a typical reading;
    values < 1 mean a slower, more deliberate delivery.
    """
    if slot_seconds_value <= 0:
        return 1.0
    estimated = estimate_tts_seconds(source_text, source_lang)
    if estimated <= 0:
        return 1.0
    return max(0.55, min(1.85, estimated / slot_seconds_value))


def speak_speed_matching_source(
    source_text: str,
    source_lang: str,
    target_text: str,
    target_lang: str,
    slot_seconds_value: float,
    *,
    min_speed: float = ELEVENLABS_SPEAK_SPEED_MIN,
    max_speed: float = ELEVENLABS_SPEAK_SPEED_MAX,
) -> float:
    """Match dub delivery pace to the original speaker, not a fixed rate.

    Uses the source line's spoken density in its timestamp slot as the pace
    reference, then asks TTS for a similar relative rate on the target line.
    Residual slot fit is handled separately (compress / extend / tempo).
    """
    pace = source_relative_pace(source_text, source_lang, slot_seconds_value)
    lo = max(min_speed, ELEVENLABS_SPEAK_SPEED_MIN)
    hi = min(max_speed, ELEVENLABS_SPEAK_SPEED_MAX)
    # Prefer speaker-matched pace; only nudge faster when the target line is
    # clearly longer than the slot even at that pace.
    target_est = estimate_tts_seconds(target_text, target_lang)
    paced_duration = target_est / pace if pace > 0 else target_est
    if paced_duration > slot_seconds_value * 1.08 and slot_seconds_value > 0:
        needed = paced_duration / slot_seconds_value
        return min(max(needed, lo), hi)
    return min(max(pace, lo), hi)


def initial_speak_speed(
    text: str,
    slot_seconds_value: float,
    language: str = "",
    *,
    min_speed: float = ELEVENLABS_SPEAK_SPEED_MIN,
    max_speed: float = ELEVENLABS_SPEAK_SPEED_MAX,
    tolerance: float = 0.03,
) -> float:
    """Speak-speed to request on the first (and usually only) TTS call."""
    return speak_speed_for_slot(
        estimate_tts_seconds(text, language),
        slot_seconds_value,
        min_speed=min_speed,
        max_speed=max_speed,
        tolerance=tolerance,
    )


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


def speak_speed_for_slot(
    clip_seconds: float,
    slot_seconds_value: float,
    *,
    min_speed: float = ELEVENLABS_SPEAK_SPEED_MIN,
    max_speed: float = ELEVENLABS_SPEAK_SPEED_MAX,
    tolerance: float = 0.03,
) -> float:
    """Natural TTS speaking-rate multiplier that preserves pitch/timbre.

    Returns 1.0 when the clip already fits within ``tolerance``. Values above
    1.0 ask the synthesizer to talk faster rather than post-process audio.
    Clamped to ElevenLabs ``speed`` bounds (0.7–1.2); residual fit uses
    rubberband after synthesis.
    """
    if clip_seconds <= 0 or slot_seconds_value <= 0:
        return 1.0
    if clip_seconds <= slot_seconds_value * (1.0 + tolerance):
        return 1.0
    requested = clip_seconds / slot_seconds_value
    lo = max(min_speed, ELEVENLABS_SPEAK_SPEED_MIN)
    hi = min(max_speed, ELEVENLABS_SPEAK_SPEED_MAX)
    return min(max(requested, lo), hi)


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


def tempo_filters(factor: float, *, rubberband_available: bool) -> list[str]:
    """Prefer rubberband (pitch-preserving) over atempo for residual fitting."""
    if abs(factor - 1.0) <= 0.001:
        return []
    if rubberband_available:
        return [f"rubberband=tempo={factor:.6f}"]
    return atempo_chain(factor)


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
    """Choose a bounded tempo and always cap output at the non-overlap slot.

    When rubberband is available it is preferred for any tempo change so the
    speaker's fundamental frequency stays intact.
    """
    if clip_seconds <= 0 or slot_seconds_value <= 0:
        return FitDecision(1.0, "atempo", max(0.0, slot_seconds_value), "invalid_duration")
    requested = clip_seconds / slot_seconds_value
    tempo = min(max(requested, min_tempo), max_speedup)
    warning = None
    if rubberband_available and abs(tempo - 1.0) > 0.001:
        backend = "rubberband"
    else:
        backend = "atempo"
        if tempo > atempo_max:
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


def extend_end_ms(
    start_ms: int,
    end_ms: int,
    next_start_ms: int | None,
    needed_seconds: float,
    *,
    pad_ms: int = 80,
) -> int:
    """Grow ``end_ms`` into trailing silence without overlapping the next voice.

    Used when compression + natural speak-speed still leave the dub clip longer
    than the original stamp — prefer a longer natural delivery over chipmunk
    tempo.
    """
    if needed_seconds <= 0:
        return end_ms
    need_ms = max(0, int(math.ceil(needed_seconds * 1000)))
    desired = end_ms + need_ms
    if next_start_ms is None:
        return max(end_ms, desired)
    limit = max(end_ms, next_start_ms - max(0, pad_ms))
    # Never move start; never cross into the next utterance.
    return min(max(end_ms, desired), limit)
