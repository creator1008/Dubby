"""Tests for minor-speaker collapse after diarization assignment."""

from __future__ import annotations

from app.worker.diarization import (
    SpeakerTurn,
    assign_speakers,
    collapse_minor_speakers,
    normalize_speaker_ids,
)


def test_collapse_merges_micro_speaker_into_neighbors() -> None:
    # Two real speakers + a tiny spurious label (phuc-like speaker_2 crumbs).
    turns = normalize_speaker_ids(
        [
            SpeakerTurn(0, 5000, "A"),
            SpeakerTurn(5000, 5200, "B"),
            SpeakerTurn(5200, 12000, "C"),
            SpeakerTurn(12000, 18000, "A"),
            SpeakerTurn(18000, 25000, "C"),
        ]
    )
    segments = [(t.start_ms, t.end_ms) for t in turns]
    assigned = assign_speakers(segments, turns)
    collapsed = collapse_minor_speakers(assigned, segments)
    labels = [sid for sid, _ in collapsed]
    # Micro B (~200ms) folded away; only two major labels remain, renumbered.
    assert set(labels) == {"speaker_1", "speaker_2"}
    assert labels[0] == "speaker_1"
    assert labels[2] == "speaker_2"


def test_collapse_keeps_two_balanced_speakers() -> None:
    turns = normalize_speaker_ids(
        [
            SpeakerTurn(0, 8000, "A"),
            SpeakerTurn(8000, 16000, "B"),
            SpeakerTurn(16000, 24000, "A"),
            SpeakerTurn(24000, 32000, "B"),
        ]
    )
    segments = [(t.start_ms, t.end_ms) for t in turns]
    assigned = assign_speakers(segments, turns)
    collapsed = collapse_minor_speakers(assigned, segments)
    assert [sid for sid, _ in collapsed] == [
        "speaker_1",
        "speaker_2",
        "speaker_1",
        "speaker_2",
    ]


def test_split_text_by_weights_keeps_all_tokens() -> None:
    from app.worker.diarization import split_text_by_weights

    pieces = split_text_by_weights("one two three four five six", [2, 1, 3])
    assert " ".join(pieces).split() == ["one", "two", "three", "four", "five", "six"]
    assert len(pieces) == 3


def test_split_timed_texts_cuts_merged_couple_caption() -> None:
    from app.worker.diarization import SpeakerTurn, split_timed_texts_on_turns

    # couple-like: Gemini merged the man's line and a woman's interjection.
    segments = [
        (
            600,
            8800,
            "Toi thuong co ay la thuong that long chu hong phai vi tien dau nha",
            "speaker_1",
        ),
        (10000, 13000, "Nut luoi cai em moi tin", "speaker_2"),
    ]
    turns = [
        SpeakerTurn(600, 3200, "speaker_1"),
        SpeakerTurn(3200, 5200, "speaker_2"),
        SpeakerTurn(5200, 8800, "speaker_1"),
        SpeakerTurn(10000, 13000, "speaker_2"),
    ]
    split = split_timed_texts_on_turns(segments, turns)
    speakers = [row[3] for row in split]
    assert len(split) >= 3
    assert "speaker_1" in speakers and "speaker_2" in speakers
    assert split[0][0] == 600
    assert any(row[0] >= 10000 for row in split)
