"""Pure parsing tests for Gemini Ver 3.0 STT / timestamps / char budgets."""

from __future__ import annotations

import pytest

from app.worker.elevenlabs_client import v3_tagged_text
from app.worker.gemini_client import (
    extract_gemini_text,
    normalize_transcript_segments,
    parse_clock_or_number_to_ms,
)
from app.worker.locale_rules import spoken_char_budget


def test_parse_seconds_under_duration() -> None:
    duration_ms = 37192
    assert parse_clock_or_number_to_ms(3.92, duration_ms=duration_ms) == 3920
    assert parse_clock_or_number_to_ms("00:03.920", duration_ms=duration_ms) == 3920
    assert parse_clock_or_number_to_ms("19.019", duration_ms=duration_ms) == 19019


def test_parse_milliseconds_when_larger_than_duration_seconds() -> None:
    duration_ms = 37192
    assert parse_clock_or_number_to_ms(19019, duration_ms=duration_ms) == 19019


def test_normalize_covers_full_transcript_and_speakers() -> None:
    payload = {
        "full_transcript": "Kính thưa quý hành khách. Cảng Liên Khương đã hoạt động trở lại.",
        "segments": [
            {
                "start_sec": 0.4,
                "end_sec": 3.9,
                "speaker": "A",
                "text": "Kính thưa quý hành khách.",
            },
            {
                "start_sec": 3.9,
                "end_sec": 8.4,
                "speaker": "A",
                "text": "Cảng Liên Khương đã hoạt động trở lại.",
            },
        ],
    }
    full, drafts = normalize_transcript_segments(payload, duration_ms=37192)
    assert "Liên Khương" in full
    assert len(drafts) == 2
    assert drafts[0].start_ms == 400
    assert drafts[0].speaker_id == "speaker_1"
    assert drafts[1].speaker_id == "speaker_1"


def test_normalize_maps_two_speakers_in_appearance_order() -> None:
    payload = {
        "full_transcript": "Hello. Hi.",
        "segments": [
            {"start_sec": 0, "end_sec": 1, "speaker": "B", "text": "Hello."},
            {"start_sec": 1, "end_sec": 2, "speaker": "A", "text": "Hi."},
        ],
    }
    _, drafts = normalize_transcript_segments(payload, duration_ms=5000)
    assert drafts[0].speaker_id == "speaker_1"
    assert drafts[1].speaker_id == "speaker_2"


def test_spoken_char_budget_korean_slot() -> None:
    assert spoken_char_budget("ko", 4.0) == 36
    assert spoken_char_budget("en", 4.0) == 56


def test_v3_emotion_tag_prefixes_once() -> None:
    assert v3_tagged_text("안녕하세요", "excited").startswith("[excited]")
    assert v3_tagged_text("[whispers] 안녕", "whisper") == "[whispers] 안녕"


def test_extract_gemini_text_strips_fence() -> None:
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": '```json\n{"full_transcript":"ok"}\n```'}]
                }
            }
        ]
    }
    assert extract_gemini_text(payload) == '{"full_transcript":"ok"}'


def test_extract_gemini_text_empty_is_retryable() -> None:
    from app.worker.errors import PipelineError

    with pytest.raises(PipelineError) as exc:
        extract_gemini_text({"candidates": [{"content": {"parts": [{"text": ""}]}}]})
    assert exc.value.retryable
