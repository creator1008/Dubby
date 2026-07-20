"""Unit tests: timing math, source validation, subtitles, response parsing."""

from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.worker import errors
from app.worker.errors import PipelineError
from app.worker.media import MediaInfo, parse_probe_output, validate_source
from app.worker.openai_client import (
    parse_translation_content,
    parse_whisper_segments,
)
from app.worker.subtitles import build_ass, escape_ass_text, format_ass_time
from app.worker.diarization import SpeakerTurn, assign_speakers, split_speaker_turns
from app.worker.timing import (
    atempo_chain,
    choose_fit_policy,
    fit_speedup,
    safe_slot_seconds,
    slot_seconds,
)


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def _info(**kw) -> MediaInfo:
    base = dict(
        duration_seconds=60.0,
        size_bytes=1_000_000,
        format_names=frozenset({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}),
        has_audio=True,
        has_video=True,
    )
    base.update(kw)
    return MediaInfo(**base)


# --- timing -----------------------------------------------------------------


def test_fit_speedup_no_speedup_when_clip_fits() -> None:
    assert fit_speedup(2.0, 3.0, 1.6) == 1.0
    assert fit_speedup(3.0, 3.0, 1.6) == 1.0


def test_fit_speedup_scales_and_caps() -> None:
    assert fit_speedup(4.5, 3.0, 1.6) == 1.5
    assert fit_speedup(9.0, 3.0, 1.6) == 1.6  # capped


def test_fit_speedup_degenerate_inputs() -> None:
    assert fit_speedup(0.0, 3.0, 1.6) == 1.0
    assert fit_speedup(3.0, 0.0, 1.6) == 1.0


def test_atempo_chain_simple_and_decomposed() -> None:
    assert atempo_chain(1.5) == ["atempo=1.5"]
    assert atempo_chain(2.0) == ["atempo=2"]
    assert atempo_chain(3.0) == ["atempo=2.0", "atempo=1.5"]
    with pytest.raises(ValueError):
        atempo_chain(0)


def test_slot_seconds() -> None:
    assert slot_seconds(1000, 3500) == 2.5
    assert slot_seconds(3500, 1000) == 0.0


def test_fit_policy_warns_and_caps_to_prevent_overlap() -> None:
    decision = choose_fit_policy(
        6.0,
        2.0,
        min_tempo=0.85,
        atempo_max=1.5,
        max_speedup=2.0,
        rubberband_available=False,
    )
    assert decision.tempo == 1.5
    assert decision.output_seconds == 2.0
    assert decision.warning == "speech_truncated_to_prevent_overlap"
    assert safe_slot_seconds(0, 3000, 2500) == 2.5


def test_diarization_overlap_uses_safe_fallback() -> None:
    result = assign_speakers(
        [(0, 1000), (1000, 2000)],
        [
            SpeakerTurn(0, 1000, "a"),
            SpeakerTurn(1000, 1700, "a"),
            SpeakerTurn(1200, 2000, "b"),
        ],
    )
    assert result[0] == ("a", False)
    assert result[1] == (None, True)


def test_speaker_turn_text_is_split_to_bounded_tts_slots() -> None:
    assert split_speaker_turns(
        [SpeakerTurn(0, 9000, "speaker_1", "one two three four five six")],
        max_duration_ms=3000,
    ) == [
        SpeakerTurn(0, 3000, "speaker_1", "one two"),
        SpeakerTurn(3000, 6000, "speaker_1", "three four"),
        SpeakerTurn(6000, 9000, "speaker_1", "five six"),
    ]


# --- source validation --------------------------------------------------------


def test_validate_source_accepts_valid_mp4() -> None:
    validate_source(_info(), _settings())


def test_validate_source_rejects_oversize() -> None:
    with pytest.raises(PipelineError) as exc:
        validate_source(_info(size_bytes=600 * 1024 * 1024), _settings())
    assert exc.value.code == errors.SOURCE_TOO_LARGE


def test_validate_source_rejects_over_ten_minutes() -> None:
    with pytest.raises(PipelineError) as exc:
        validate_source(_info(duration_seconds=601.0), _settings())
    assert exc.value.code == errors.SOURCE_TOO_LONG


def test_validate_source_rejects_unknown_container() -> None:
    with pytest.raises(PipelineError) as exc:
        validate_source(_info(format_names=frozenset({"avi"})), _settings())
    assert exc.value.code == errors.SOURCE_UNSUPPORTED_CONTAINER


def test_validate_source_rejects_missing_audio() -> None:
    with pytest.raises(PipelineError) as exc:
        validate_source(_info(has_audio=False), _settings())
    assert exc.value.code == errors.SOURCE_NO_AUDIO


def test_validate_source_limits_configurable() -> None:
    settings = _settings(
        max_source_duration_seconds=1200, allowed_source_containers="webm"
    )
    validate_source(
        _info(duration_seconds=900, format_names=frozenset({"webm"})), settings
    )


def test_parse_probe_output() -> None:
    payload = json.dumps(
        {
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "12.5"},
            "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
        }
    )
    info = parse_probe_output(payload, 123)
    assert info.duration_seconds == 12.5
    assert "mp4" in info.format_names
    assert info.has_audio and info.has_video
    assert info.size_bytes == 123


def test_parse_probe_output_invalid_json() -> None:
    with pytest.raises(PipelineError) as exc:
        parse_probe_output("not json", 0)
    assert exc.value.code == errors.PROBE_FAILED


# --- subtitles ------------------------------------------------------------------


def test_format_ass_time() -> None:
    assert format_ass_time(0) == "0:00:00.00"
    assert format_ass_time(1234) == "0:00:01.23"
    assert format_ass_time(3_600_000 + 61_500) == "1:01:01.50"
    assert format_ass_time(-5) == "0:00:00.00"


def test_escape_ass_text() -> None:
    assert escape_ass_text("a{b}c") == "a\\{b\\}c"
    assert escape_ass_text("line1\nline2") == "line1\\Nline2"
    assert escape_ass_text("back\\slash") == "back\\\\slash"


def _segments() -> list[dict]:
    return [
        {"start_ms": 0, "end_ms": 2000, "source_text": "안녕", "target_text": "Hello"},
        {"start_ms": 2000, "end_ms": 4000, "source_text": "", "target_text": "World"},
    ]


def test_build_ass_none_mode() -> None:
    assert build_ass(_segments(), "none") is None


def test_build_ass_target_mode() -> None:
    doc = build_ass(_segments(), "target")
    assert doc is not None
    assert "Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,Hello" in doc
    assert "World" in doc
    assert ",2,60,60,160,1" in doc
    assert doc.startswith("[Script Info]")


def test_build_ass_source_mode_skips_empty() -> None:
    doc = build_ass(_segments(), "source")
    assert doc is not None
    assert "안녕" in doc
    assert ",2,60,60,50,1" in doc
    assert doc.count("Dialogue:") == 1  # second segment has no source text


# --- Whisper / translation parsing ------------------------------------------------


def test_parse_whisper_segments_orders_and_clamps() -> None:
    payload = {
        "segments": [
            {"start": 0.0, "end": 2.5, "text": " Hello "},
            {"start": 2.5, "end": 2.5, "text": "tiny"},  # zero-length -> +1ms
            {"start": 3.0, "end": 4.0, "text": "   "},  # empty -> dropped
        ]
    }
    drafts = parse_whisper_segments(payload)
    assert len(drafts) == 2
    assert drafts[0].start_ms == 0 and drafts[0].end_ms == 2500
    assert drafts[0].text == "Hello"
    assert drafts[1].end_ms == drafts[1].start_ms + 1


def test_parse_translation_content_roundtrip() -> None:
    content = json.dumps(
        {"translations": [{"idx": 0, "text": "A"}, {"idx": 1, "text": "B"}]}
    )
    assert parse_translation_content(content, [0, 1]) == {0: "A", 1: "B"}


def test_parse_translation_content_missing_idx_is_retryable() -> None:
    content = json.dumps({"translations": [{"idx": 0, "text": "A"}]})
    with pytest.raises(PipelineError) as exc:
        parse_translation_content(content, [0, 1])
    assert exc.value.code == errors.TRANSLATION_FAILED
    assert exc.value.retryable


def test_parse_translation_content_bad_json_is_retryable() -> None:
    with pytest.raises(PipelineError) as exc:
        parse_translation_content("garbage", [0])
    assert exc.value.retryable


# --- config guards -----------------------------------------------------------------


def test_production_rejects_mock_pipeline() -> None:
    with pytest.raises(ValueError, match="PIPELINE_MODE"):
        Settings(
            _env_file=None,
            app_env="production",
            pipeline_mode="mock",
            supabase_url="https://x.supabase.co",
            database_url="postgresql://u:p@h/db",
            r2_account_id="a",
            r2_access_key_id="k",
            r2_secret_access_key="s",
        )


def test_output_key_derivation() -> None:
    from app.storage.r2 import R2Storage

    storage = R2Storage(_settings())
    key = storage.output_key_for_source(
        "users/u1/projects/p1/source/video.mp4", "dub_en.mp4"
    )
    assert key == "users/u1/projects/p1/outputs/dub_en.mp4"
