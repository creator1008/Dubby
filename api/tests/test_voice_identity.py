"""Tests for speaker gender heuristics and distinct voice fallbacks."""

from __future__ import annotations

import math
import struct

from app.worker.voice_identity import (
    FEMALE_FALLBACK_VOICE_IDS,
    MALE_FALLBACK_VOICE_IDS,
    estimate_f0_hz,
    gender_from_f0,
    pick_fallback_voice_id,
)


def _tone_pcm(freq_hz: float, seconds: float = 1.5, rate: int = 16000) -> bytes:
    frames = int(rate * seconds)
    return b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq_hz * i / rate)))
        for i in range(frames)
    )


def test_estimate_f0_roughly_tracks_tone() -> None:
    maleish = estimate_f0_hz(_tone_pcm(120.0))
    femaleish = estimate_f0_hz(_tone_pcm(220.0))
    assert maleish is not None and 90 < maleish < 150
    assert femaleish is not None and 190 < femaleish < 250


def test_gender_from_f0_thresholds() -> None:
    assert gender_from_f0(120.0) == "male"
    assert gender_from_f0(210.0) == "female"
    assert gender_from_f0(165.0) == "neutral"
    assert gender_from_f0(None) == "neutral"


def test_pick_fallback_voice_never_reuses_used_ids() -> None:
    used = {MALE_FALLBACK_VOICE_IDS[0], FEMALE_FALLBACK_VOICE_IDS[0]}
    male = pick_fallback_voice_id("male", used, speaker_key="A")
    female = pick_fallback_voice_id("female", used, speaker_key="B")
    assert male not in used
    assert female not in used
    assert male in MALE_FALLBACK_VOICE_IDS
    assert female in FEMALE_FALLBACK_VOICE_IDS
    assert male != female
