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


def test_collapse_keeps_brief_second_speaker_on_short_clip() -> None:
    turns = normalize_speaker_ids(
        [
            SpeakerTurn(0, 9000, "A"),
            SpeakerTurn(9000, 11500, "B"),
            SpeakerTurn(11500, 15000, "A"),
        ]
    )
    segments = [(t.start_ms, t.end_ms) for t in turns]
    assigned = assign_speakers(segments, turns)
    collapsed = collapse_minor_speakers(assigned, segments)
    assert set(sid for sid, _ in collapsed) == {"speaker_1", "speaker_2"}


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


def test_split_timed_texts_keeps_caption_when_turns_have_a_hole() -> None:
    from app.worker.diarization import SpeakerTurn, split_timed_texts_on_turns

    segments = [
        (0, 10000, "cau chuyen dai o giua bi bo", "speaker_1"),
    ]
    turns = [
        SpeakerTurn(0, 2500, "speaker_1"),
        SpeakerTurn(7500, 10000, "speaker_2"),
    ]
    split = split_timed_texts_on_turns(segments, turns)
    assert len(split) == 1
    assert split[0][0] == 0
    assert split[0][1] == 10000
    assert split[0][2] == "cau chuyen dai o giua bi bo"


def test_vn_couple_question_tails_keep_same_speaker() -> None:
    """VN_couple 7–10: mid-sentence question tails are not a new speaker."""
    from app.worker.diarization import (
        SpeakerTurn,
        inherit_continuation_speakers,
        split_timed_texts_on_turns,
    )
    from app.worker.utterance_pipeline import (
        UtteranceChunk,
        is_grammatical_continuation,
        merge_continuation_chunks,
    )

    assert is_grammatical_continuation("Anh có đưa hết tiền cho", "em không")
    assert is_grammatical_continuation("Không đưa cho em thì", "đưa cho ai?")
    # Finished question + a real reply must stay two speakers.
    assert not is_grammatical_continuation(
        "Anh có đưa hết tiền cho em không?", "Không."
    )

    turns = [
        SpeakerTurn(7000, 8200, "speaker_1"),
        SpeakerTurn(8500, 9200, "speaker_2"),
        SpeakerTurn(9400, 10800, "speaker_1"),
        SpeakerTurn(11100, 12000, "speaker_2"),
    ]
    captions = [
        (7000, 9200, "Anh có đưa hết tiền cho em không", "speaker_1"),
        (9400, 12000, "Không đưa cho em thì đưa cho ai?", "speaker_1"),
    ]
    split = split_timed_texts_on_turns(captions, turns)
    assert len(split) == 2
    assert split[0][3] == "speaker_1"
    assert split[1][3] == "speaker_1"
    assert "em không" in split[0][2]
    assert "đưa cho ai" in split[1][2]

    already_split = [
        UtteranceChunk(7000, 8200, "Anh có đưa hết tiền cho", "speaker_1", ()),
        UtteranceChunk(8500, 9200, "em không", "speaker_2", ()),
        UtteranceChunk(9400, 10800, "Không đưa cho em thì", "speaker_1", ()),
        UtteranceChunk(11100, 12000, "đưa cho ai?", "speaker_2", ()),
    ]
    assigned = [(c.speaker_id, False) for c in already_split]
    inherited = inherit_continuation_speakers(
        assigned,
        [c.text for c in already_split],
        [(c.start_ms, c.end_ms) for c in already_split],
    )
    assert [sid for sid, _ in inherited] == [
        "speaker_1",
        "speaker_1",
        "speaker_1",
        "speaker_1",
    ]
    merged, merged_labels = merge_continuation_chunks(already_split, inherited)
    assert len(merged) == 2
    assert "em không" in merged[0].text
    assert "đưa cho ai" in merged[1].text
    assert [sid for sid, _ in (merged_labels or [])] == ["speaker_1", "speaker_1"]

    # Real VN_couple idx 6–9: 80ms gaps, 350ms tail, stem of Q2 already flipped.
    from app.worker.diarization import inherit_adjacent_question_speakers

    vn = [
        UtteranceChunk(9490, 10720, "Anh có đưa hết tiền cho", "speaker_1", ()),
        UtteranceChunk(10800, 11150, "em không?", "speaker_2", ()),
        UtteranceChunk(11230, 12170, "Không đưa cho em thì", "speaker_2", ()),
        UtteranceChunk(12250, 12720, "đưa cho ai?", "speaker_1", ()),
    ]
    vn_assigned = [(c.speaker_id, False) for c in vn]
    vn_inherited = inherit_continuation_speakers(
        vn_assigned,
        [c.text for c in vn],
        [(c.start_ms, c.end_ms) for c in vn],
    )
    vn_merged, vn_labels = merge_continuation_chunks(vn, vn_inherited)
    assert len(vn_merged) == 2
    vn_labels = inherit_adjacent_question_speakers(
        vn_labels or [],
        [c.text for c in vn_merged],
        [(c.start_ms, c.end_ms) for c in vn_merged],
    )
    assert [sid for sid, _ in vn_labels] == ["speaker_1", "speaker_1"]
