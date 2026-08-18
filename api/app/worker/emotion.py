"""Per-segment emotion tone from source vocals → ElevenLabs voice settings.

Taxonomy (product):
  sad, angry, whisper, excited, energetic, calm, cheerful

Detection uses lightweight PCM features (loudness, dynamics, ZCR, F0) so the
worker needs no extra ML dependency. Project ``tone_style`` is the fallback
when a clip is too short/silent to classify.
"""

from __future__ import annotations

import array
import math
import wave
from typing import Any, Literal

from .voice_identity import estimate_f0_hz

EmotionTone = Literal[
    "sad",
    "angry",
    "whisper",
    "excited",
    "energetic",
    "calm",
    "cheerful",
]

EMOTION_TONES: tuple[EmotionTone, ...] = (
    "sad",
    "angry",
    "whisper",
    "excited",
    "energetic",
    "calm",
    "cheerful",
)

# Legacy project presets → current taxonomy.
_LEGACY_TONE_MAP: dict[str, EmotionTone] = {
    "neutral": "calm",
    "warm": "cheerful",
    "energetic": "energetic",
    "serious": "calm",
}

# ElevenLabs multilingual-v2 / flash voice_settings tuned per emotion.
EMOTION_VOICE_SETTINGS: dict[EmotionTone, dict[str, float]] = {
    "sad": {"stability": 0.72, "similarity_boost": 0.78, "style": 0.22},
    "angry": {"stability": 0.28, "similarity_boost": 0.70, "style": 0.72},
    "whisper": {"stability": 0.88, "similarity_boost": 0.72, "style": 0.05},
    "excited": {"stability": 0.22, "similarity_boost": 0.68, "style": 0.78},
    "energetic": {"stability": 0.32, "similarity_boost": 0.72, "style": 0.65},
    "calm": {"stability": 0.70, "similarity_boost": 0.80, "style": 0.10},
    "cheerful": {"stability": 0.40, "similarity_boost": 0.75, "style": 0.55},
}


def normalize_emotion_tone(value: str | None, *, fallback: EmotionTone = "calm") -> EmotionTone:
    """Map arbitrary / legacy labels onto the product taxonomy."""
    raw = (value or "").strip().lower()
    if raw in EMOTION_VOICE_SETTINGS:
        return raw  # type: ignore[return-value]
    mapped = _LEGACY_TONE_MAP.get(raw)
    if mapped:
        return mapped
    return fallback


def voice_settings_for_emotion(tone: str | None) -> dict[str, float]:
    """ElevenLabs voice_settings (without speed / speaker boost)."""
    key = normalize_emotion_tone(tone)
    return dict(EMOTION_VOICE_SETTINGS[key])


def _read_pcm16_mono(
    path: str,
    start_ms: int,
    end_ms: int,
    *,
    target_rate: int = 16000,
) -> tuple[bytes, int]:
    """Load a segment as mono PCM16, optionally downmixing and resampling lightly."""
    with wave.open(path, "rb") as source:
        if source.getsampwidth() != 2:
            raise ValueError("emotion analysis requires PCM16 WAV")
        rate = source.getframerate()
        channels = source.getnchannels()
        start_frame = max(0, round(start_ms * rate / 1000))
        frame_count = max(1, round((end_ms - start_ms) * rate / 1000))
        source.setpos(min(start_frame, source.getnframes()))
        raw = source.readframes(frame_count)
    samples = array.array("h")
    samples.frombytes(raw)
    if channels > 1:
        mono = array.array("h")
        for i in range(0, len(samples) - channels + 1, channels):
            mono.append(int(sum(samples[i : i + channels]) / channels))
        samples = mono
    if rate != target_rate and samples and rate > 0:
        # Cheap linear resample for F0 / ZCR (quality is secondary to speed).
        ratio = target_rate / rate
        out_len = max(1, int(len(samples) * ratio))
        resampled = array.array("h")
        for i in range(out_len):
            src = i / ratio
            left = int(src)
            right = min(left + 1, len(samples) - 1)
            frac = src - left
            value = samples[left] * (1 - frac) + samples[right] * frac
            resampled.append(int(value))
        samples = resampled
        rate = target_rate
    return samples.tobytes(), rate


def extract_emotion_features(
    path: str,
    start_ms: int,
    end_ms: int,
) -> dict[str, float]:
    """Acoustic features used by :func:`classify_emotion_tone`."""
    duration_ms = max(1, end_ms - start_ms)
    try:
        pcm, rate = _read_pcm16_mono(path, start_ms, end_ms)
    except (OSError, wave.Error, ValueError):
        return {
            "loudness_db": -60.0,
            "dynamics": 0.0,
            "zcr": 0.0,
            "f0_hz": 0.0,
            "f0_std": 0.0,
            "duration_ms": float(duration_ms),
        }
    samples = array.array("h")
    samples.frombytes(pcm)
    if not samples:
        return {
            "loudness_db": -60.0,
            "dynamics": 0.0,
            "zcr": 0.0,
            "f0_hz": 0.0,
            "f0_std": 0.0,
            "duration_ms": float(duration_ms),
        }

    abs_vals = [abs(int(s)) for s in samples]
    mean_square = sum(s * s for s in samples) / len(samples)
    loudness_db = (
        max(-60.0, 20 * math.log10(math.sqrt(mean_square) / 32768))
        if mean_square > 0
        else -60.0
    )
    mean_abs = sum(abs_vals) / len(abs_vals)
    # Peak-to-mean ratio as a crude dynamics / shoutiness proxy.
    peak = max(abs_vals) if abs_vals else 0
    dynamics = (peak / mean_abs) if mean_abs > 1 else 0.0

    zero_crossings = 0
    for i in range(1, len(samples)):
        if (samples[i - 1] >= 0) != (samples[i] >= 0):
            zero_crossings += 1
    zcr = zero_crossings / max(1, len(samples) - 1)

    # Windowed F0 for mean + variance (skip near-silent frames).
    window = max(rate // 2, 1)
    hop = max(window // 2, 1)
    f0s: list[float] = []
    for start in range(0, max(1, len(samples) - window), hop):
        chunk = samples[start : start + window].tobytes()
        f0 = estimate_f0_hz(chunk, rate)
        if f0 is not None and 70 <= f0 <= 400:
            f0s.append(f0)
    if f0s:
        f0_mean = sum(f0s) / len(f0s)
        f0_var = sum((f - f0_mean) ** 2 for f in f0s) / len(f0s)
        f0_std = math.sqrt(f0_var)
    else:
        f0_mean = 0.0
        f0_std = 0.0

    return {
        "loudness_db": round(loudness_db, 2),
        "dynamics": round(dynamics, 3),
        "zcr": round(zcr, 4),
        "f0_hz": round(f0_mean, 2),
        "f0_std": round(f0_std, 2),
        "duration_ms": float(duration_ms),
    }


def classify_emotion_tone(
    features: dict[str, float],
    *,
    source_text: str = "",
    fallback: EmotionTone = "calm",
) -> EmotionTone:
    """Map acoustic features (+ light text cues) to an emotion tone."""
    loud = float(features.get("loudness_db") or -60.0)
    dynamics = float(features.get("dynamics") or 0.0)
    zcr = float(features.get("zcr") or 0.0)
    f0 = float(features.get("f0_hz") or 0.0)
    f0_std = float(features.get("f0_std") or 0.0)
    duration_ms = float(features.get("duration_ms") or 0.0)

    if duration_ms < 180 or loud <= -55:
        return fallback

    text = source_text or ""
    text_excited = "!" in text or "！" in text
    text_question = "?" in text or "？" in text

    # Whisper: quiet + relatively airy (higher ZCR) delivery.
    if loud <= -38 and zcr >= 0.08:
        return "whisper"
    if loud <= -42 and dynamics < 3.5:
        return "whisper"

    # Angry: loud, punchy dynamics, unstable pitch.
    if loud >= -18 and dynamics >= 4.2 and f0_std >= 18:
        return "angry"
    if loud >= -16 and dynamics >= 3.8:
        return "angry"

    # Excited: loud + lively pitch motion (often with !).
    if loud >= -20 and f0_std >= 22 and (f0 >= 160 or text_excited):
        return "excited"
    if loud >= -18 and f0_std >= 28:
        return "excited"

    # Energetic: strong level, moderate pitch motion.
    if loud >= -22 and f0_std >= 14 and dynamics >= 3.2:
        return "energetic"
    if loud >= -20 and text_excited:
        return "energetic"

    # Cheerful: brighter pitch, moderate energy.
    if f0 >= 175 and loud >= -28 and f0_std >= 10:
        return "cheerful"
    if text_excited and loud >= -30 and f0 >= 150:
        return "cheerful"

    # Sad: quieter, lower pitch, flat contour.
    if loud <= -30 and f0 > 0 and f0 <= 145 and f0_std <= 12:
        return "sad"
    if loud <= -32 and f0_std <= 8 and not text_question:
        return "sad"

    # Calm: default steady mid delivery.
    if loud <= -24 and f0_std <= 14:
        return "calm"

    return fallback


def detect_segment_emotion(
    vocals_path: str,
    *,
    start_ms: int,
    end_ms: int,
    source_text: str = "",
    fallback: EmotionTone = "calm",
) -> EmotionTone:
    """Classify one segment from the vocals stem."""
    analysis_end = max(start_ms + 120, end_ms)
    features = extract_emotion_features(vocals_path, start_ms, analysis_end)
    return classify_emotion_tone(
        features, source_text=source_text, fallback=fallback
    )


def detect_emotions_for_segments(
    vocals_path: str,
    segments: list[dict[str, Any]],
    *,
    fallback: str = "calm",
) -> dict[int, EmotionTone]:
    """Return ``idx -> emotion_tone`` for each segment (uses original ASR span)."""
    default = normalize_emotion_tone(fallback)
    out: dict[int, EmotionTone] = {}
    for row in segments:
        try:
            idx = int(row["idx"])
            start_ms = int(row["start_ms"])
        except (KeyError, TypeError, ValueError):
            continue
        source_end = row.get("source_end_ms")
        try:
            end_ms = int(source_end) if source_end is not None else int(row["end_ms"])
        except (TypeError, ValueError):
            try:
                end_ms = int(row["end_ms"])
            except (KeyError, TypeError, ValueError):
                continue
        if end_ms <= start_ms:
            out[idx] = default
            continue
        out[idx] = detect_segment_emotion(
            vocals_path,
            start_ms=start_ms,
            end_ms=end_ms,
            source_text=str(row.get("source_text") or ""),
            fallback=default,
        )
    return out
