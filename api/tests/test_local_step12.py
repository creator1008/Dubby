from app.local_step12 import (
    TimedWord,
    _assign_speaker_ids,
    _cover_recognized_phrase_boundaries,
    _merge_speech_ranges,
    _matched_loudness_gain,
    _relative_loudness_gains,
    _speech_mask_expression,
    _split_diarized_turns,
    group_words,
)
from app.worker.elevenlabs_client import tts_model_for_language


def test_group_words_splits_on_sentence_punctuation_and_long_gap() -> None:
    words = [
        TimedWord(100, 400, " 안녕하세요."),
        TimedWord(450, 700, " 반갑습니다."),
        TimedWord(1600, 1900, " 다음"),
        TimedWord(1950, 2300, " 문장입니다"),
    ]

    assert group_words(words) == [
        (100, 400, "안녕하세요."),
        (450, 700, "반갑습니다."),
        (1600, 2300, "다음 문장입니다"),
    ]


def test_vietnamese_uses_supported_elevenlabs_model() -> None:
    assert (
        tts_model_for_language("eleven_multilingual_v2", "vi")
        == "eleven_flash_v2_5"
    )
    assert (
        tts_model_for_language("eleven_multilingual_v2", "ko")
        == "eleven_multilingual_v2"
    )


def test_group_words_splits_long_phrase_and_keeps_non_overlapping_ranges() -> None:
    words = [
        TimedWord(0, 4500, "긴"),
        TimedWord(4400, 9200, " 문장"),
        TimedWord(9300, 9600, " 끝"),
    ]

    assert group_words(words, max_duration_ms=8000) == [
        (0, 9200, "긴 문장"),
        (9300, 9600, "끝"),
    ]


def test_group_words_discards_empty_and_invalid_words() -> None:
    words = [
        TimedWord(0, 100, " "),
        TimedWord(500, 400, "invalid"),
        TimedWord(1000, 1300, "hello"),
    ]

    assert group_words(words) == [(1000, 1300, "hello")]


def test_merge_speech_ranges_keeps_non_language_gaps() -> None:
    assert _merge_speech_ranges(
        [(5000, 8000), (1000, 3000), (2800, 4000), (-100, 100), (9000, 9000)]
    ) == [(0, 100), (1000, 4000), (5000, 8000)]


def test_merge_speech_ranges_joins_only_tightly_adjacent_words() -> None:
    assert _merge_speech_ranges(
        [(1000, 1200), (1280, 1500), (1800, 2100)],
        max_gap_ms=120,
    ) == [(1000, 1500), (1800, 2100)]


def test_phrase_boundary_coverage_does_not_fill_internal_sobbing_gaps() -> None:
    assert _cover_recognized_phrase_boundaries(
        [(3970, 6150), (8390, 9730), (11590, 13070), (21400, 22360)],
        [(3060, 8480), (8480, 13940), (18780, 23940)],
    ) == [
        (3060, 6150),
        (8390, 9730),
        (11590, 13940),
        (18780, 23940),
    ]


def test_phrase_boundary_coverage_masks_transcript_without_word_ranges() -> None:
    assert _cover_recognized_phrase_boundaries(
        [(230, 1050), (16050, 17270)],
        [(100, 750), (9000, 9400), (14950, 16850)],
    ) == [
        (100, 1050),
        (9000, 9400),
        (14950, 17270),
    ]


def test_speech_mask_uses_crossfades_only_inside_recognized_ranges() -> None:
    expression = _speech_mask_expression([(1000, 3000)])

    assert "lt(t,0.780000)" in expression
    assert "(t-0.780000)/0.060000" in expression
    assert "lt(t,3.080000),1" in expression
    assert "(3.140000-t)/0.060000" in expression
    assert _speech_mask_expression([]) == "0"


def test_assign_speaker_ids_uses_largest_time_overlap() -> None:
    drafts = [(0, 1000, "one"), (1000, 2000, "two"), (3000, 3500, "three")]
    turns = [
        (0, 800, "A", "one"),
        (800, 1300, "B", "two"),
        (1300, 2000, "B", "three"),
    ]

    assert _assign_speaker_ids(drafts, turns) == ["A", "B", "speaker_0"]


def test_diarized_turns_split_on_speaker_and_max_interval() -> None:
    assert _split_diarized_turns(
        [
            (100, 8100, "A", "one two three four"),
            (8200, 9000, "B", "reply"),
        ],
        max_duration_ms=4000,
    ) == [
        (100, 4100, "one two", "A"),
        (4100, 8100, "three four", "A"),
        (8200, 9000, "reply", "B"),
    ]


def test_relative_loudness_gains_follow_source_levels_with_bounds() -> None:
    assert _relative_loudness_gains({0: -40.0, 1: -20.0, 2: -10.0}) == {
        0: -8.0,
        1: 0.0,
        2: 6.0,
    }


def test_matched_loudness_gain_compensates_tts_level_with_bounds() -> None:
    assert _matched_loudness_gain(-24.0, -18.0) == -6.0
    assert _matched_loudness_gain(-12.0, -24.0) == 6.0
    assert _matched_loudness_gain(-40.0, -18.0) == -8.0
