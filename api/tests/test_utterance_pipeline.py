"""Unit tests for utterance chunking (stage 1 / 3 / 4)."""

from __future__ import annotations

from app.worker.utterance_pipeline import (
    TimedToken,
    allocate_target_parts,
    build_fine_utterances,
    build_stage1_chunks,
    quantize_ms,
)


def test_quantize_ms_to_centisecond() -> None:
    assert quantize_ms(1234) == 1230
    assert quantize_ms(1235) == 1240
    assert quantize_ms(5) == 10 or quantize_ms(5) == 0


def test_korean_word_spacing_preserved() -> None:
    from app.worker.utterance_pipeline import _join_tokens

    words = [
        TimedToken(3200, 3260, "난"),
        TimedToken(3260, 3640, "너희"),
        TimedToken(3640, 4000, "부녀를"),
        TimedToken(4000, 4340, "위해서"),
        TimedToken(4340, 4720, "호의를"),
        TimedToken(4720, 5220, "베푸는"),
        TimedToken(5220, 5420, "거야"),
    ]
    assert _join_tokens(words) == "난 너희 부녀를 위해서 호의를 베푸는 거야"
    chunks = build_stage1_chunks(words, None, pause_ms=500, max_duration_ms=8000)
    assert len(chunks) == 1
    assert "너희 부녀를" in chunks[0].text
    assert "호의를 베푸는" in chunks[0].text


def test_stage1_splits_on_pause_not_even_time() -> None:
    words = [
        TimedToken(0, 400, "아직"),
        TimedToken(400, 900, "상황"),
        TimedToken(900, 1500, "파악이"),
        # long pause
        TimedToken(3200, 3600, "난"),
        TimedToken(3600, 4200, "너희를"),
        TimedToken(4200, 5000, "위해서"),
    ]
    turns = [(0, 5500, "A", "아직 상황 파악이 난 너희를 위해서")]
    chunks = build_stage1_chunks(words, turns, pause_ms=280, max_duration_ms=8000)
    assert len(chunks) == 2
    assert chunks[0].end_ms <= 1600
    assert chunks[1].start_ms >= 3000
    assert "상황" in chunks[0].text


def test_fine_utterances_subdivide_parent() -> None:
    words = [
        TimedToken(0, 300, "one"),
        TimedToken(300, 600, "two"),
        TimedToken(900, 1200, "three"),
        TimedToken(1200, 1500, "four"),
    ]
    chunks = build_stage1_chunks(words, None, pause_ms=250, max_duration_ms=8000)
    fines = build_fine_utterances(chunks, pause_ms=200, max_duration_ms=3500)
    assert len(fines) >= 2
    assert all(unit.end_ms > unit.start_ms for unit in fines)


def test_allocate_target_parts_covers_all_slots() -> None:
    words = [
        TimedToken(0, 400, "a"),
        TimedToken(800, 1200, "bb"),
        TimedToken(1600, 2200, "ccc"),
    ]
    chunks = build_stage1_chunks(words, None, pause_ms=200, max_duration_ms=8000)
    fines = build_fine_utterances(chunks, pause_ms=200, max_duration_ms=3500)
    parts = allocate_target_parts("Hello world today friends", fines)
    assert len(parts) == len(fines)
    assert "".join(parts).replace(" ", "") != "" or not fines


def test_breath_units_keep_continuous_multi_sentence_flow() -> None:
    """Punctuation alone must not split; only a real breath does."""
    from app.worker.utterance_pipeline import build_breath_utterances

    words = [
        TimedToken(0, 400, "You"),
        TimedToken(400, 800, "know"),
        TimedToken(800, 1400, "there"),
        TimedToken(1400, 2000, "will"),
        TimedToken(2000, 2800, "be"),
        TimedToken(2800, 3600, "people"),
        # 400ms micro-gap — not a breath
        TimedToken(4000, 4500, "it's"),
        TimedToken(4500, 5000, "a"),
        TimedToken(5000, 5600, "village"),
        # 500ms — still under breath threshold
        TimedToken(6100, 6600, "it's"),
        TimedToken(6600, 7200, "a"),
        TimedToken(7200, 8000, "town"),
        # 900ms breath — must split
        TimedToken(8900, 9400, "Finally"),
        TimedToken(9400, 10000, "purpose"),
    ]
    chunks = build_breath_utterances(
        words,
        None,
        breath_pause_ms=650,
        max_duration_ms=20000,
        soft_pause_ms=400,
    )
    assert len(chunks) == 2
    assert "village" in chunks[0].text.lower()
    assert "town" in chunks[0].text.lower()
    assert "finally" in chunks[1].text.lower()


def test_translation_groups_neoreul_jegeo_and_lays_english() -> None:
    """너를 / 제거한다 stay two stamps but share one laid-out English line."""
    from app.worker.utterance_pipeline import (
        UtteranceChunk,
        expand_grouped_translations,
        group_indices_for_translation,
        is_translation_dangling,
        looks_like_sentence_end,
    )

    assert not looks_like_sentence_end("너를")
    assert is_translation_dangling("너를")
    assert looks_like_sentence_end("제거한다")
    assert not is_translation_dangling("민지를 위해서")
    assert not is_translation_dangling("이제 확실해졌다 민지를 위해서")

    chunks = [
        UtteranceChunk(26350, 27020, "너를", "A", ()),
        UtteranceChunk(28090, 28980, "제거한다", "A", ()),
    ]
    groups = group_indices_for_translation(chunks, max_gap_ms=2500)
    assert groups == [[0, 1]]
    parts = expand_grouped_translations(
        chunks, groups, ["I'm going to eliminate you."]
    )
    assert parts[0] == "I'm going to"
    assert parts[1] == "eliminate you."

    # Must not glue 민지를 위해서 into 너를 제거한다 (scrambles pairs).
    longer = [
        UtteranceChunk(21030, 24540, "이제 확실해졌다 민지를 위해서", "A", ()),
        UtteranceChunk(26350, 27020, "너를", "A", ()),
        UtteranceChunk(28090, 28980, "제거한다", "A", ()),
    ]
    assert group_indices_for_translation(longer, max_gap_ms=2500) == [
        [0],
        [1, 2],
    ]
    split_case = [
        UtteranceChunk(22800, 24540, "민지를 위해서", "A", ()),
        UtteranceChunk(26350, 28980, "너를 제거한다", "A", ()),
    ]
    assert group_indices_for_translation(split_case, max_gap_ms=2500) == [
        [0],
        [1],
    ]


def test_soft_split_overlong_prefers_pause_not_forced_cut() -> None:
    from app.worker.utterance_pipeline import soft_split_overlong_groups

    # ~22s run with a 450ms soft pause after w10.
    words = []
    t = 0
    for i in range(22):
        if i == 11:
            t += 450
        words.append(TimedToken(t, t + 800, f"w{i}"))
        t += 1000
    groups = soft_split_overlong_groups(
        [words], max_duration_ms=13000, soft_pause_ms=400
    )
    assert len(groups) == 2
    assert groups[0][-1].text == "w10"
    assert groups[1][0].text == "w11"


def test_resplit_after_refine_cuts_whisper_megachunk() -> None:
    """Long mega-chunks may split; short promo breaths must not."""
    from app.local_step12 import _clamp_stage1_chunk_ends, _resplit_stage1_after_refine
    from app.worker.utterance_pipeline import UtteranceChunk

    words = [
        TimedToken(460, 700, "도자기는"),
        TimedToken(840, 1260, "뒤에"),
        TimedToken(1260, 1580, "이제"),
        TimedToken(1880, 2680, "후작업으로"),  # 300ms gap
        TimedToken(2860, 5140, "건조후에"),
        TimedToken(5520, 8180, "초벌재벌"),  # 380ms gap
    ]
    mega = UtteranceChunk(460, 8490, "전체문장", "A", tuple(words))
    # Short enough that only_if_longer_ms=7000 keeps it whole when under threshold
    short = UtteranceChunk(460, 3000, "짧은문장", "A", tuple(words[:3]))
    kept = _resplit_stage1_after_refine(
        [short],
        pause_ms=280,
        max_duration_ms=8000,
        only_if_longer_ms=7000,
    )
    assert len(kept) == 1

    split = _resplit_stage1_after_refine(
        [mega], pause_ms=280, max_duration_ms=8000, only_if_longer_ms=7000
    )
    split = _clamp_stage1_chunk_ends(split)
    assert len(split) >= 2
    assert split[0].end_ms <= 1880
    assert "도자기는" in split[0].text
    assert "후작업으로" in split[1].text


def test_merge_dangling_chunks_rebuilds_promo_sentence() -> None:
    from app.worker.utterance_pipeline import UtteranceChunk, merge_dangling_chunks

    fragments = [
        UtteranceChunk(5350, 6580, "So could the", "A", ()),
        UtteranceChunk(7420, 8900, "open itself and", "A", ()),
        UtteranceChunk(9500, 10740, "be part of the city?", "A", ()),
        UtteranceChunk(18350, 18980, "I think", "A", ()),
        UtteranceChunk(19340, 20520, "he was interested.", "A", ()),
    ]
    merged = merge_dangling_chunks(fragments, max_gap_ms=1500, max_duration_ms=10000)
    assert len(merged) == 2
    assert "So could the open itself and be part of the city?" in merged[0].text
    assert merged[0].start_ms == 5350
    assert merged[0].end_ms == 10740
    assert "I think he was interested." in merged[1].text

def test_expand_onset_leftward_pulls_late_whisper_start(tmp_path) -> None:
    """Whisper start 460ms must not cut speech that began near 60ms."""
    import math
    import struct
    import wave

    from app.local_step12 import _expand_onset_leftward

    rate = 16000
    duration_s = 1.0
    samples = []
    for i in range(int(rate * duration_s)):
        t = i / rate
        if 0.06 <= t < 0.35 or 0.46 <= t < 0.9:
            amp = int(12000 * math.sin(2 * math.pi * 220 * t))
        else:
            amp = int(40 * math.sin(2 * math.pi * 50 * t))
        samples.append(amp)
    path = tmp_path / "onset.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(struct.pack("<" + "h" * len(samples), *samples))

    onset = _expand_onset_leftward(path, 460, lookback_ms=900, stop_before_ms=0)
    assert onset <= 120, onset
