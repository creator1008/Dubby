"""High-quality Whisper post-processing (ported from local_step12 Ver 1.0).

Keep Whisper's sentence-aware segment text. Filter hallucinations. Split a
long or multi-sentence segment on its own word timestamps only when those
words actually cover the span; otherwise split the segment text on
punctuation. Word tokens stay attached for mix timing, not as the transcript.
"""

from __future__ import annotations

import re
from collections import Counter

from .utterance_pipeline import TimedToken

_SENTENCE_END_RE = re.compile(r"[.!?。？！]\s*$")


def whisper_segment_is_hallucination(segment: dict) -> bool:
    """Drop Whisper segments that look like music/noise hallucinations."""
    text = str(segment.get("text", "")).strip()
    if not text:
        return True
    start = float(segment.get("start", 0.0) or 0.0)
    end = float(segment.get("end", 0.0) or 0.0)
    if end <= start:
        return True
    no_speech = float(segment.get("no_speech_prob", 0.0) or 0.0)
    avg_logprob = float(segment.get("avg_logprob", 0.0) or 0.0)
    compression = float(segment.get("compression_ratio", 0.0) or 0.0)
    if compression >= 2.4:
        return True
    tokens = re.findall(r"\S+", text)
    substantial = len(tokens) >= 3 and len(text) >= 8
    # BGM under real VO often yields high no_speech; V1 dropped those lines
    # (dalat1 lost 19s–37s). Only apply no_speech cuts to scraps / noise.
    if not substantial:
        if no_speech > 0.6 and avg_logprob < -0.8:
            return True
        if no_speech > 0.85:
            return True
        if end - start < 0.35 and no_speech > 0.4:
            return True
    if len(tokens) >= 6:
        for n in (2, 3, 4):
            if len(tokens) < n * 3:
                continue
            ngrams = [
                " ".join(tokens[i : i + n]) for i in range(0, len(tokens) - n + 1, n)
            ]
            if len(ngrams) >= 3 and len(set(ngrams)) == 1:
                return True
    return False


def _join_words(words: list[TimedToken]) -> str:
    text = "".join(word.text for word in words).strip()
    if " " not in text and len(words) > 1:
        text = " ".join(word.text.strip() for word in words).strip()
    return re.sub(r"\s+([,.!?。？！])", r"\1", text)


def group_words(
    words: list[TimedToken],
    *,
    gap_ms: int = 650,
    max_duration_ms: int = 9000,
) -> list[tuple[int, int, str]]:
    """Group word timestamps into stable, non-overlapping subtitle phrases."""
    clean = [
        word
        for word in words
        if word.text.strip() and word.end_ms > word.start_ms >= 0
    ]
    if not clean:
        return []

    groups: list[list[TimedToken]] = []
    current: list[TimedToken] = []
    for index, word in enumerate(clean):
        current.append(word)
        next_word = clean[index + 1] if index + 1 < len(clean) else None
        duration = word.end_ms - current[0].start_ms
        long_gap = next_word is not None and next_word.start_ms - word.end_ms >= gap_ms
        sentence_end = bool(_SENTENCE_END_RE.search(word.text.strip()))
        if next_word is None or long_gap or sentence_end or duration >= max_duration_ms:
            groups.append(current)
            current = []

    result: list[tuple[int, int, str]] = []
    for group in groups:
        start = group[0].start_ms
        end = group[-1].end_ms
        text = _join_words(group)
        if not text:
            continue
        if result and start < result[-1][1]:
            start = result[-1][1]
        if end > start:
            result.append((start, end, text))
    return result


def split_segment_text(
    start_ms: int,
    end_ms: int,
    text: str,
) -> list[tuple[int, int, str]]:
    """Keep Whisper's segment text; only cut on punctuation.

    Ver 1.0 local_step12: segment boundaries are sentence-aware. Word tokens
    may be stretched or sparse (dalat ``một`` at 12s) and must not replace
    the transcript. When word timestamps are unusable, split the *segment
    text* on sentence punctuation instead of regrouping words.
    """
    cleaned = (text or "").strip()
    if not cleaned or end_ms <= start_ms:
        return []
    sentences = [
        part.strip()
        for part in re.split(r"(?<=[.!?。？！])\s+", cleaned)
        if part.strip()
    ]
    if len(sentences) <= 1:
        return [(start_ms, end_ms, cleaned)]
    duration = end_ms - start_ms
    weights = [max(1, len(part)) for part in sentences]
    total = sum(weights)
    out: list[tuple[int, int, str]] = []
    cursor = start_ms
    for index, (part, weight) in enumerate(zip(sentences, weights)):
        next_end = (
            end_ms
            if index == len(sentences) - 1
            else cursor + max(80, round(duration * weight / total))
        )
        next_end = min(end_ms, max(cursor + 80, next_end))
        if next_end > cursor and part:
            out.append((cursor, next_end, part))
            cursor = next_end
    return out or [(start_ms, end_ms, cleaned)]


def dedupe_repetitive_drafts(
    drafts: list[tuple[int, int, str]],
) -> list[tuple[int, int, str]]:
    """Collapse consecutive identical/near-identical hallucinated lines."""
    cleaned: list[tuple[int, int, str]] = []
    for start, end, text in drafts:
        compact = re.sub(r"\s+", "", text)
        if cleaned:
            prev_start, prev_end, prev_text = cleaned[-1]
            prev_compact = re.sub(r"\s+", "", prev_text)
            if compact == prev_compact or (
                compact and prev_compact and compact in prev_compact and len(compact) >= 6
            ):
                cleaned[-1] = (prev_start, max(prev_end, end), prev_text)
                continue
            if (
                prev_compact
                and compact
                and prev_compact in compact
                and len(prev_compact) >= 6
            ):
                cleaned[-1] = (prev_start, max(prev_end, end), text)
                continue
        cleaned.append((start, end, text))
    return cleaned


def _compact_len(value: str) -> int:
    return len(re.sub(r"\s+", "", value or ""))


def _split_keeps_segment_text(
    original: str, split: list[tuple[int, int, str]]
) -> bool:
    """Reject word regrouping that drops most of Whisper's segment sentence."""
    if not split:
        return False
    orig = _compact_len(original)
    if orig < 8:
        return True
    kept = sum(_compact_len(text) for _, _, text in split)
    return kept * 100 >= orig * 80


def drafts_look_hallucinated(drafts: list[tuple[int, int, str]]) -> bool:
    """True when the transcript is dominated by a looping junk phrase."""
    if not drafts:
        return False
    tokens = re.findall(r"\S+", " ".join(text for _, _, text in drafts))
    if len(tokens) < 8:
        return False
    # Short function words (Vietnamese là/có/của, Korean 이/가) loop in real speech.
    significant = [token for token in tokens if len(token) >= 4]
    scored = significant if len(significant) >= 8 else tokens
    counts = Counter(scored)
    top_count = counts.most_common(1)[0][1]
    if top_count / len(scored) >= 0.45:
        return True
    if len(drafts) >= 4:
        heads = [re.sub(r"\s+", "", text)[:8] for _, _, text in drafts if text.strip()]
        if heads and max(Counter(heads).values()) / len(heads) >= 0.5:
            return True
    return False


_MAX_WORD_MS = 1600
_MS_PER_CHAR = 220


def _sane_word_end(start_ms: int, end_ms: int, text: str) -> int:
    """Clamp Whisper word ends that swallow many seconds of later speech.

    dalat stored ``một`` from 14.36s–26.84s, so the editor preview of that
    subtitle played 12s of unrelated VO.
    """
    compact = re.sub(r"\s+", "", text)
    n_chars = max(1, len(compact))
    cap = start_ms + min(_MAX_WORD_MS, max(280, n_chars * _MS_PER_CHAR + 180))
    return min(end_ms, cap)


def _covered_duration_ms(words: list[TimedToken], start_ms: int, end_ms: int) -> int:
    total = 0
    for word in words:
        begin = max(start_ms, word.start_ms)
        finish = min(end_ms, word.end_ms)
        if finish > begin:
            total += finish - begin
    return total


def word_timeline_is_reliable(
    words: list[TimedToken],
    drafts: list[tuple[int, int, str]] | list[object],
) -> bool:
    """False when word timestamps are too sparse or stretched to drive cuts."""
    if not words:
        return False
    if any(word.end_ms - word.start_ms > 1800 for word in words):
        return False
    span_start = min(word.start_ms for word in words)
    span_end = max(word.end_ms for word in words)
    span = max(1, span_end - span_start)
    cover = sum(max(0, word.end_ms - word.start_ms) for word in words)
    if cover / span < 0.45:
        return False
    draft_tokens = 0
    for draft in drafts:
        text = getattr(draft, "text", None)
        if text is None:
            text = draft[2]  # type: ignore[index]
        draft_tokens += len(re.findall(r"\S+", str(text)))
    if draft_tokens >= 8 and len(words) < max(3, draft_tokens * 45 // 100):
        return False
    return True


def parse_whisper_words(payload: dict) -> list[TimedToken]:
    words: list[TimedToken] = []
    for word in payload.get("words") or []:
        if word.get("start") is None or word.get("end") is None:
            continue
        text = str(word.get("word", ""))
        start_ms = max(0, round(float(word["start"]) * 1000))
        end_ms = _sane_word_end(
            start_ms, round(float(word["end"]) * 1000), text
        )
        if text.strip() and end_ms > start_ms:
            words.append(TimedToken(start_ms=start_ms, end_ms=end_ms, text=text))
    return words


def refine_whisper_drafts(
    payload: dict,
) -> list[tuple[int, int, str]]:
    """Build filtered, non-overlapping (start, end, text) drafts from Whisper."""
    words = parse_whisper_words(payload)
    drafts: list[tuple[int, int, str]] = []
    raw_segments = payload.get("segments") or []
    kept_segments = 0
    for segment in raw_segments:
        if whisper_segment_is_hallucination(segment):
            continue
        kept_segments += 1
        start = max(0, round(float(segment.get("start", 0)) * 1000))
        end = round(float(segment.get("end", 0)) * 1000)
        text = str(segment.get("text", "")).strip()
        segment_words = [
            word for word in words if word.start_ms < end and word.end_ms > start
        ]
        sentence_count = len(re.findall(r"[.!?。？！]", text))
        duration = end - start
        needs_split = duration > 6500 or sentence_count > 1
        if not needs_split:
            drafts.append((start, end, text))
            continue
        word_cover = _covered_duration_ms(segment_words, start, end)
        words_trustworthy = (
            bool(segment_words)
            and duration > 0
            and word_cover * 100 >= duration * 55
        )
        if words_trustworthy:
            # Ver 1.0: split a long/multi-sentence segment on its own words.
            split = group_words(
                segment_words,
                gap_ms=500,
                max_duration_ms=4500,
            )
            if _split_keeps_segment_text(text, split):
                drafts.extend(split)
            else:
                drafts.extend(
                    split_segment_text(start, end, text) or [(start, end, text)]
                )
        else:
            drafts.extend(split_segment_text(start, end, text))

    if not drafts and not raw_segments:
        usable_words = [
            word
            for word in words
            if word.text.strip() and word.end_ms - word.start_ms >= 40
        ]
        if usable_words and len(usable_words) <= 80:
            drafts = group_words(usable_words, gap_ms=500, max_duration_ms=4500)
    elif not drafts and raw_segments and kept_segments == 0:
        drafts = []

    drafts = dedupe_repetitive_drafts(drafts)
    if drafts_look_hallucinated(drafts):
        drafts = []

    non_overlapping: list[tuple[int, int, str]] = []
    for start, end, text in sorted(drafts, key=lambda item: (item[0], item[1])):
        if non_overlapping and start < non_overlapping[-1][1]:
            start = non_overlapping[-1][1]
        if end > start and text:
            non_overlapping.append((start, end, text))
    return non_overlapping
