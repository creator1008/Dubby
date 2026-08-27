"""Unit tests for per-segment emotion tone classification."""

from __future__ import annotations

from app.worker.emotion import (
    classify_emotion_tone,
    normalize_emotion_tone,
    voice_settings_for_emotion,
)


def test_normalize_maps_legacy_presets() -> None:
    assert normalize_emotion_tone("neutral") == "calm"
    assert normalize_emotion_tone("warm") == "cheerful"
    assert normalize_emotion_tone("serious") == "calm"
    assert normalize_emotion_tone("excited") == "excited"


def test_resolve_segment_emotion_uses_project_tone_unless_editor_override() -> None:
    from app.worker.emotion import resolve_segment_emotion

    assert resolve_segment_emotion("sad", project_tone="calm", user_set=False) == "calm"
    assert resolve_segment_emotion("sad", project_tone="calm", user_set=True) == "sad"
    assert resolve_segment_emotion("", project_tone="cheerful", user_set=True) == "cheerful"


def test_voice_settings_cover_all_tones() -> None:
    for tone in (
        "sad",
        "angry",
        "whisper",
        "excited",
        "energetic",
        "calm",
        "cheerful",
    ):
        settings = voice_settings_for_emotion(tone)
        assert 0.0 <= settings["stability"] <= 1.0
        assert 0.0 <= settings["style"] <= 1.0


def test_classify_whisper_from_quiet_airy() -> None:
    assert (
        classify_emotion_tone(
            {
                "loudness_db": -44.0,
                "dynamics": 2.0,
                "zcr": 0.12,
                "f0_hz": 180.0,
                "f0_std": 8.0,
                "duration_ms": 800.0,
            }
        )
        == "whisper"
    )


def test_classify_angry_from_loud_dynamics() -> None:
    assert (
        classify_emotion_tone(
            {
                "loudness_db": -14.0,
                "dynamics": 5.0,
                "zcr": 0.05,
                "f0_hz": 160.0,
                "f0_std": 25.0,
                "duration_ms": 900.0,
            }
        )
        == "angry"
    )


def test_classify_sad_from_quiet_flat_low_pitch() -> None:
    assert (
        classify_emotion_tone(
            {
                "loudness_db": -34.0,
                "dynamics": 2.5,
                "zcr": 0.04,
                "f0_hz": 120.0,
                "f0_std": 6.0,
                "duration_ms": 1200.0,
            }
        )
        == "sad"
    )


def test_classify_falls_back_when_silent() -> None:
    assert (
        classify_emotion_tone(
            {
                "loudness_db": -60.0,
                "dynamics": 0.0,
                "zcr": 0.0,
                "f0_hz": 0.0,
                "f0_std": 0.0,
                "duration_ms": 50.0,
            },
            fallback="cheerful",
        )
        == "cheerful"
    )
