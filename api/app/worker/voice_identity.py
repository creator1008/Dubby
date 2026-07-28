"""Speaker gender heuristics and premade ElevenLabs voice fallbacks.

When Instant Voice Clone cannot be created (monthly add/edit quota), each
speaker still needs a distinct, gender-appropriate voice. Reusing another
speaker's clone collapses multi-speaker dubs into one timbre — often the wrong
gender.
"""

from __future__ import annotations

import array
import math
import wave
from pathlib import Path

# Public premade voices (distinct timbres). Order = preference within gender.
MALE_FALLBACK_VOICE_IDS: tuple[str, ...] = (
    "pNInz6obpgDQGcFmaJgB",  # Adam
    "N2lVS1w4EtoT3dr4eOWO",  # Callum
    "TX3LPaxmHKxFdv7VOQHJ",  # Liam
    "onwK4e9ZLuTAKqWW03F9",  # Daniel
    "JBFqnCBsd6RMkjVDRZzb",  # George
    "CwhRBWXzGAHq8TQ4Fs17",  # Roger
)

FEMALE_FALLBACK_VOICE_IDS: tuple[str, ...] = (
    "EXAVITQu4vr4xnSDxMaL",  # Sarah
    "21m00Tcm4TlvDq8ikWAM",  # Rachel
    "XrExE9yKIg1WjnnlVkGX",  # Matilda
    "cgSgspJ2msm6clMCkdW9",  # Jessica
    "FGY2WhTYpPnrIDTdsKH5",  # Laura
    "XB0fDUnXU5powFXDhCwa",  # Charlotte
)


def estimate_f0_hz(pcm_s16le_mono: bytes, sample_rate: int = 16000) -> float | None:
    """Estimate fundamental frequency via autocorrelation (speech band)."""
    if sample_rate <= 0 or len(pcm_s16le_mono) < sample_rate:
        return None
    samples = array.array("h")
    samples.frombytes(pcm_s16le_mono)
    if not samples:
        return None

    # Use a mid window to skip fades / silence at the edges.
    window = min(len(samples), sample_rate * 3)
    start = max(0, (len(samples) - window) // 2)
    chunk = samples[start : start + window]
    # Energy gate: skip near-silent clips.
    energy = sum(int(s) * int(s) for s in chunk) / max(1, len(chunk))
    if energy < 500.0:
        return None

    mean = sum(chunk) / len(chunk)
    centered = [float(s) - mean for s in chunk]
    min_lag = max(1, int(sample_rate / 350))  # ~350 Hz
    max_lag = min(len(centered) // 2, int(sample_rate / 70))  # ~70 Hz
    if max_lag <= min_lag + 2:
        return None

    best_lag = min_lag
    best_corr = float("-inf")
    for lag in range(min_lag, max_lag + 1):
        corr = 0.0
        # Stride to keep this cheap on CPU.
        for i in range(0, len(centered) - lag, 3):
            corr += centered[i] * centered[i + lag]
        if corr > best_corr:
            best_corr = corr
            best_lag = lag
    if best_corr <= 0:
        return None
    return sample_rate / best_lag


def gender_from_f0(f0_hz: float | None) -> str:
    """Map F0 to male / female / neutral."""
    if f0_hz is None or not math.isfinite(f0_hz):
        return "neutral"
    # Adult speech: male typically ~85–155 Hz, female ~180–255 Hz.
    if f0_hz < 155:
        return "male"
    if f0_hz > 180:
        return "female"
    return "neutral"


def pick_fallback_voice_id(
    gender: str,
    used_voice_ids: set[str],
    *,
    speaker_key: str = "",
) -> str:
    """Pick a premade voice matching ``gender``, never reusing ``used_voice_ids``."""
    male = list(MALE_FALLBACK_VOICE_IDS)
    female = list(FEMALE_FALLBACK_VOICE_IDS)
    normalized = (gender or "neutral").strip().lower()
    if normalized == "male":
        pools = [male, female]
    elif normalized == "female":
        pools = [female, male]
    else:
        # Stable per-speaker preference so A/B don't both land on the same pool.
        prefer_male = (sum(ord(ch) for ch in speaker_key) % 2) == 0
        pools = [male, female] if prefer_male else [female, male]

    for pool in pools:
        for voice_id in pool:
            if voice_id not in used_voice_ids:
                return voice_id
    # Exhausted unique pool — still return a gender-preferred default.
    return pools[0][0]
