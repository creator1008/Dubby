"""Scribe grouping, segment_id translation attach, and emotion_tag schema."""

from __future__ import annotations

from app.worker.document_translate import (
    attach_translations,
    build_source_units,
    parse_translations_by_segment_id,
)
from app.worker.emotion_tag import (
    EMOTION_CLASSES,
    apply_user_emotion_input,
    audio_tags_from_emotion_tag,
    parse_emotion_tag,
    resolve_tts_emotion_tag,
)
from app.worker.scribe_client import transcribe_result_from_scribe


def test_scribe_groups_words_by_speaker_and_pause() -> None:
    payload = {
        "text": "Hello there thanks",
        "words": [
            {
                "text": "Hello",
                "type": "word",
                "start": 0.0,
                "end": 0.4,
                "speaker_id": "speaker_0",
            },
            {
                "text": "there",
                "type": "word",
                "start": 0.45,
                "end": 0.8,
                "speaker_id": "speaker_0",
            },
            {
                "text": " ",
                "type": "spacing",
                "start": 0.8,
                "end": 0.85,
                "speaker_id": "speaker_0",
            },
            {
                "text": "thanks",
                "type": "word",
                "start": 2.0,
                "end": 2.4,
                "speaker_id": "speaker_1",
            },
        ],
    }
    result = transcribe_result_from_scribe(payload)
    assert result.provider == "scribe"
    assert result.skip_proofread is True
    assert result.speakers_labeled is True
    assert len(result.drafts) == 2
    assert result.drafts[0].text == "Hello there"
    assert result.drafts[0].speaker_id == "speaker_1"
    assert result.drafts[1].text == "thanks"
    assert result.drafts[1].speaker_id == "speaker_2"
    assert result.drafts[0].end_ms == 800
    assert result.drafts[1].start_ms == 2000
    assert result.speech_ranges == [(0, 400), (450, 800), (2000, 2400)]


def test_document_translate_matches_segment_id_not_array_order() -> None:
    units = build_source_units(
        [(0, "short hello", 1.0), (1, "later scene goodbye", 2.0)],
        speakers=["speaker_1", "speaker_2"],
        emotion_tags=[
            {"class": "Happy", "intensity": "medium", "volume": "normal", "custom": None, "source": "auto"},
            {"class": "Sad", "intensity": "low", "volume": "soft", "custom": None, "source": "auto"},
        ],
        starts_ms=[0, 4000],
        ends_ms=[1000, 6000],
    )
    # Model returned rows out of order plus a leftover document dump.
    payload = {
        "full_translation": "IGNORE THIS DOCUMENT SLICE",
        "translations": [
            {"idx": 1, "segment_id": 1, "text": "나중에 작별"},
            {"text": "whole-document dump without id"},
            {"idx": 0, "segment_id": 0, "text": "짧은 안녕"},
            {"idx": 99, "text": "invented row"},
        ],
    }
    mapped = parse_translations_by_segment_id(payload, [0, 1])
    assert mapped == {0: "짧은 안녕", 1: "나중에 작별"}
    attached = attach_translations(units, mapped)
    assert attached[0]["target_text"] == "짧은 안녕"
    assert attached[1]["target_text"] == "나중에 작별"
    assert attached[0]["start_ms"] == 0
    assert attached[1]["speaker_id"] == "speaker_2"
    assert attached[0]["emotion_tag"]["class"] == "Happy"
    assert attached[1]["emotion_tag"]["class"] == "Sad"


def test_document_translate_drops_positional_document_dump() -> None:
    payload = {
        "translations": [
            {"text": "paragraph one of a whole-doc dump"},
            {"text": "paragraph two of a whole-doc dump"},
        ]
    }
    assert parse_translations_by_segment_id(payload, [0, 1], require_all=False) == {}


def test_emotion_tag_schema_and_v4_audio_tags() -> None:
    assert "Playful" in EMOTION_CLASSES
    tag = parse_emotion_tag(
        {"class": "Excited", "intensity": "high", "volume": "loud", "source": "auto"}
    )
    assert tag["class"] == "Excited"
    prefix = audio_tags_from_emotion_tag(tag)
    assert "[loudly]" in prefix
    assert "[excited]" in prefix
    user = apply_user_emotion_input("신나는", english_custom="excited")
    assert user["class"] == "Excited"
    assert user["source"] == "user"
    whisper = parse_emotion_tag("whisper")
    assert whisper["class"] == "Calm"
    assert whisper["volume"] == "whisper"


def test_tts_emotion_auto_ignores_user_override() -> None:
    user = apply_user_emotion_input("Angry")
    detected = parse_emotion_tag(
        {"class": "Sad", "intensity": "low", "volume": "soft", "source": "auto"}
    )
    tag = resolve_tts_emotion_tag(
        mode="auto",
        user_tag=user,
        user_tone="angry",
        detected_tag=detected,
        project_tone="calm",
    )
    assert tag["class"] == "Sad"
    assert tag["source"] == "auto"


def test_asr_mid_clip_holes_are_detected_at_800ms() -> None:
    from app.worker.media import asr_timeline_gaps

    gaps = asr_timeline_gaps(
        [(0, 2000), (3500, 5000)],
        8000,
        min_ms=800,
    )
    assert (2000, 3500) in gaps
    assert gaps[-1][0] == 5000


def test_tts_emotion_custom_uses_user_then_project() -> None:
    user = apply_user_emotion_input("Playful")
    detected = parse_emotion_tag({"class": "Sad", "source": "auto"})
    tag = resolve_tts_emotion_tag(
        mode="custom",
        user_tag=user,
        detected_tag=detected,
        project_tone="calm",
    )
    assert tag["class"] == "Playful"
    assert tag["source"] == "user"
    fallback = resolve_tts_emotion_tag(
        mode="custom",
        detected_tag=detected,
        project_tone="cheerful",
    )
    assert fallback["class"] == "Happy"
    assert fallback["source"] == "user"


def test_asr_timeline_gaps_include_mid_clip_holes() -> None:
    from app.worker.media import asr_timeline_gaps

    gaps = asr_timeline_gaps([(0, 1500), (14000, 16000)], 20000, min_ms=800)
    assert (1500, 14000) in gaps
    assert (16000, 20000) in gaps
