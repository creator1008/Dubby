"""Utterance-safe 3-minute dub windows (no ffmpeg)."""

from __future__ import annotations

from app.worker.dub_chunks import (
    cut_after_target,
    plan_dub_chunks,
    shift_placed_clips,
    shift_ranges,
    utterance_span,
)


def _seg(start: int, end: int, *, source_end: int | None = None) -> dict:
    row = {"start_ms": start, "end_ms": end}
    if source_end is not None:
        row["source_end_ms"] = source_end
    return row


def test_short_source_stays_one_window() -> None:
    segments = [_seg(0, 4_000), _seg(4_200, 8_000)]
    assert plan_dub_chunks(segments, 4 * 60 * 1000) == [(0, 4 * 60 * 1000)]


def test_five_minutes_splits_after_line_not_mid_sentence() -> None:
    # Line at 3:00 still talking until 3:08; next line at 3:10.
    segments = [
        _seg(0, 20_000),
        _seg(178_000, 188_000),
        _seg(190_000, 200_000),
        _seg(300_000, 310_000),
    ]
    windows = plan_dub_chunks(segments, 5 * 60 * 1000 + 10_000)
    assert windows[0] == (0, 190_000)
    assert windows[0][1] == 190_000  # next utterance start, not 180_000
    assert windows[-1][1] == 5 * 60 * 1000 + 10_000
    assert all(end > start for start, end in windows)
    covered = sum(end - start for start, end in windows)
    assert covered == 5 * 60 * 1000 + 10_000


def test_cut_in_silence_uses_three_minute_mark() -> None:
    spans = [(0, 60_000), (200_000, 220_000)]
    assert cut_after_target(spans, 180_000, 400_000) == 180_000


def test_cut_waits_for_overlapping_line_then_next_start() -> None:
    spans = [(170_000, 188_000), (192_000, 210_000)]
    assert cut_after_target(spans, 180_000, 400_000) == 192_000


def test_cut_caps_runaway_caption() -> None:
    spans = [(100_000, 400_000)]
    cut = cut_after_target(spans, 180_000, 400_000, max_overrun_ms=30_000)
    assert cut == 210_000


def test_tiny_tail_is_absorbed() -> None:
    segments = [_seg(0, 170_000), _seg(175_000, 185_000), _seg(186_000, 195_000)]
    windows = plan_dub_chunks(
        segments,
        200_000,
        min_duration_ms=100_000,
        target_ms=180_000,
        min_tail_ms=20_000,
    )
    assert windows == [(0, 200_000)]


def test_uses_later_of_source_and_translation_end() -> None:
    assert utterance_span(_seg(0, 1_000, source_end=1_400)) == (0, 1_400)
    assert utterance_span(_seg(0, 2_000, source_end=1_200)) == (0, 2_000)


def test_shift_ranges_and_clips_rebase_to_window() -> None:
    ranges = shift_ranges([(1_000, 4_000), (8_000, 12_000), (20_000, 21_000)], 3_000, 10_000)
    assert ranges == [(0, 1_000), (5_000, 7_000)]
    clips = shift_placed_clips(
        [("a.wav", 2_500), ("b.wav", 3_000), ("c.wav", 10_000)],
        3_000,
        10_000,
    )
    assert clips == [("b.wav", 0)]
