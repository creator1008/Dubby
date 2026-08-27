"""Utterance chunking for translation (stage 1) and fine dub timing (stage 3/4).

Timestamps are quantized to 10 ms (0.01 s) so dub slots align with speaker
delivery rather than arbitrary sentence cuts.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

_SENTENCE_END_RE = re.compile(r"[.!?。？！][\"'”’)]*$")
# Spoken Korean often omits punctuation; treat common closers as utterance ends.
# Bare ``게`` must not match the dative particle ``에게`` (false sentence end).
_KO_UTTERANCE_END_RE = re.compile(
    r"(?:다|요|까|냐|네|군|죠|야|어|아|지|라|(?<!에)게|(?<![는은])데|군려)[\"'”’)]*$"
)
# Vietnamese VO rarely uses a period; particles still close the spoken beat.
_VI_UTTERANCE_END_RE = re.compile(
    r"(?:rồi|nhé|nhá|nha|nhỉ|chứ|thôi|đi|ạ|à|ư|hả|sao|nè|luôn|"
    r"không|được|đấy|thế|vậy|mà)[\"'”’)]*$",
    re.IGNORECASE,
)
_SOURCE_WEIGHT_TOKEN_RE = re.compile(
    r"[A-Za-z0-9]+(?:'[A-Za-z]+)?|[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]+"
)


def quantize_ms(value: int, *, step_ms: int = 10) -> int:
    """Round to the nearest ``step_ms`` (default 10 ms = 0.01 s)."""
    if step_ms <= 1:
        return max(0, int(value))
    return max(0, int(round(int(value) / step_ms) * step_ms))


@dataclass(frozen=True)
class TimedToken:
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class UtteranceChunk:
    """Stage-1 translation unit (also shown in the subtitle editor)."""

    start_ms: int
    end_ms: int
    text: str
    speaker_id: str
    words: tuple[TimedToken, ...]


@dataclass(frozen=True)
class FineUtterance:
    """Stage-3 fine delivery slot used for dub matching / TTS placement."""

    parent_idx: int
    start_ms: int
    end_ms: int
    source_text: str
    words: tuple[TimedToken, ...]


def _is_hangul_char(char: str) -> bool:
    if not char:
        return False
    code = ord(char)
    return (
        0xAC00 <= code <= 0xD7A3
        or 0x1100 <= code <= 0x11FF
        or 0x3130 <= code <= 0x318F
    )


def _is_cjk_nospace_char(char: str) -> bool:
    """Kana / Han ideographs that traditionally join without spaces."""
    if not char:
        return False
    code = ord(char)
    return 0x3040 <= code <= 0x30FF or 0x4E00 <= code <= 0x9FFF


def _join_tokens(tokens: list[TimedToken]) -> str:
    """Join timed ASR tokens, preserving Korean word spacing from Whisper."""
    parts = [token.text.strip() for token in tokens if token.text.strip()]
    if not parts:
        return ""
    joined: list[str] = []
    for part in parts:
        if not joined:
            joined.append(part)
            continue
        prev = joined[-1]
        if part[0] in ",.!?;:)]}%>”’" or (prev and prev[-1] in "([{<“‘"):
            joined.append(part)
            continue
        # Keep Japanese/Chinese script unbroken; always space Hangul words so
        # editor text matches natural Korean spacing (난 너희 부녀를 …).
        if (
            prev
            and part
            and _is_cjk_nospace_char(prev[-1])
            and _is_cjk_nospace_char(part[0])
            and not (_is_hangul_char(prev[-1]) or _is_hangul_char(part[0]))
        ):
            joined.append(part)
        else:
            joined.append(" " + part)
    return "".join(joined).strip()


def group_tokens_by_pause(
    tokens: list[TimedToken],
    *,
    pause_ms: int,
    max_duration_ms: int,
    min_duration_ms: int = 200,
) -> list[list[TimedToken]]:
    """Group timed tokens on silence gaps / max duration (not punctuation alone)."""
    clean = [
        TimedToken(
            quantize_ms(token.start_ms),
            max(quantize_ms(token.end_ms), quantize_ms(token.start_ms) + 10),
            token.text,
        )
        for token in tokens
        if token.text.strip() and token.end_ms > token.start_ms
    ]
    if not clean:
        return []

    groups: list[list[TimedToken]] = []
    current: list[TimedToken] = []
    for index, token in enumerate(clean):
        current.append(token)
        nxt = clean[index + 1] if index + 1 < len(clean) else None
        duration = token.end_ms - current[0].start_ms
        long_gap = nxt is not None and (nxt.start_ms - token.end_ms) >= pause_ms
        too_long = duration >= max_duration_ms and len(current) >= 2
        if nxt is None or long_gap or too_long:
            if current[-1].end_ms - current[0].start_ms >= min_duration_ms or nxt is None:
                groups.append(current)
                current = []
            # If the group is still too short, keep accumulating unless finished.
            elif nxt is None:
                groups.append(current)
                current = []
    if current:
        groups.append(current)
    return groups


def group_tokens_by_breath(
    tokens: list[TimedToken],
    *,
    breath_pause_ms: int,
    min_duration_ms: int = 200,
) -> list[list[TimedToken]]:
    """Split only on speaker breath/silence — never on max-duration alone.

    Continuous multi-sentence flow without a breath stays one group. Cutting on
    duration alone shreds delivery and hurts meaning for dubbing.
    """
    return group_tokens_by_pause(
        tokens,
        pause_ms=breath_pause_ms,
        # No duration cut here; oversize groups are soft-split separately.
        max_duration_ms=10**9,
        min_duration_ms=min_duration_ms,
    )


def soft_split_overlong_groups(
    groups: list[list[TimedToken]],
    *,
    max_duration_ms: int,
    soft_pause_ms: int,
) -> list[list[TimedToken]]:
    """Split only groups that exceed ``max_duration_ms``, at the best soft pause.

    Prefers the largest internal gap >= ``soft_pause_ms``. If no such gap
    exists, the continuous run is kept whole — forced mid-phrase cuts hurt
    intent more than a longer slot.
    """
    if max_duration_ms <= 0:
        return groups
    out: list[list[TimedToken]] = []
    for group in groups:
        out.extend(
            _soft_split_one_group(
                group,
                max_duration_ms=max_duration_ms,
                soft_pause_ms=soft_pause_ms,
            )
        )
    return out


def _soft_split_one_group(
    group: list[TimedToken],
    *,
    max_duration_ms: int,
    soft_pause_ms: int,
) -> list[list[TimedToken]]:
    if len(group) < 2:
        return [group]
    duration = group[-1].end_ms - group[0].start_ms
    if duration <= max_duration_ms:
        return [group]

    best_idx = -1
    best_gap = -1
    for index in range(len(group) - 1):
        gap = group[index + 1].start_ms - group[index].end_ms
        left_dur = group[index].end_ms - group[0].start_ms
        right_dur = group[-1].end_ms - group[index + 1].start_ms
        if gap < soft_pause_ms:
            continue
        # Prefer splits that leave both sides usable and closer to max.
        if left_dur < 800 or right_dur < 800:
            continue
        if gap > best_gap:
            best_gap = gap
            best_idx = index
    if best_idx < 0:
        return [group]

    left = group[: best_idx + 1]
    right = group[best_idx + 1 :]
    return [
        *_soft_split_one_group(
            left, max_duration_ms=max_duration_ms, soft_pause_ms=soft_pause_ms
        ),
        *_soft_split_one_group(
            right, max_duration_ms=max_duration_ms, soft_pause_ms=soft_pause_ms
        ),
    ]


def build_breath_utterances(
    words: list[TimedToken],
    speaker_turns: list[tuple[int, int, str, str]] | None,
    *,
    breath_pause_ms: int = 650,
    max_duration_ms: int = 20000,
    soft_pause_ms: int = 400,
) -> list[UtteranceChunk]:
    """Build dub/translation units from speaker breath gaps.

    Meaning unit here means a continuous spoken delivery: several punctuated
    sentences may stay together when the speaker does not breathe between them.
    A real breath/silence always starts a new voice chunk.
    """
    if not words and not speaker_turns:
        return []

    def _chunks_from_words(
        in_words: list[TimedToken], speaker: str
    ) -> list[UtteranceChunk]:
        groups = group_tokens_by_breath(
            in_words, breath_pause_ms=breath_pause_ms
        )
        groups = soft_split_overlong_groups(
            groups,
            max_duration_ms=max_duration_ms,
            soft_pause_ms=soft_pause_ms,
        )
        chunks: list[UtteranceChunk] = []
        for group in groups:
            text = _join_tokens(group)
            if not text:
                continue
            chunks.append(
                UtteranceChunk(
                    start_ms=group[0].start_ms,
                    end_ms=group[-1].end_ms,
                    text=text,
                    speaker_id=speaker or "speaker_0",
                    words=tuple(group),
                )
            )
        return chunks

    if speaker_turns:
        chunks: list[UtteranceChunk] = []
        for turn_start, turn_end, speaker, turn_text in speaker_turns:
            turn_start = quantize_ms(turn_start)
            turn_end = quantize_ms(turn_end)
            if turn_end <= turn_start:
                continue
            in_turn = [
                word
                for word in words
                if word.end_ms > turn_start and word.start_ms < turn_end
            ]
            built = _chunks_from_words(in_turn, speaker or "speaker_0")
            if built:
                chunks.extend(built)
            else:
                clean = (
                    _join_tokens(in_turn)
                    if in_turn
                    else (turn_text or "").strip()
                )
                if clean:
                    chunks.append(
                        UtteranceChunk(
                            start_ms=turn_start,
                            end_ms=turn_end,
                            text=clean,
                            speaker_id=speaker or "speaker_0",
                            words=tuple(in_turn),
                        )
                    )
        return _dedupe_chunks(chunks)

    return _dedupe_chunks(_chunks_from_words(words, "speaker_0"))


def looks_like_sentence_end(text: str) -> bool:
    """True when ``text`` already closes a spoken utterance.

    Latin/CJK punctuation counts, and so do common Korean / Vietnamese
    spoken closers because dramatic VO rarely carries a period.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return True
    if _SENTENCE_END_RE.search(cleaned):
        return True
    if _KO_UTTERANCE_END_RE.search(cleaned):
        return True
    return bool(_VI_UTTERANCE_END_RE.search(cleaned))


def is_translation_dangling(text: str) -> bool:
    """True only for scraps that cannot be translated alone.

    Breath stamps like ``너를`` must borrow the next beat. Longer phrases such
    as ``민지를 위해서`` keep their own translation even without 다/요 — merging
    them with ``너를 제거한다`` scrambled source/target pairs.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    # Mid-clause particles first — before sentence-end heuristics that can
    # false-positive on endings like ``게`` inside ``에게``.
    if re.search(
        r"(?:에게|한테|께|으로|로서|이며|고|며|는데|지만|거나|든지|은데|는데도)$",
        cleaned,
    ):
        return True
    if looks_like_sentence_end(cleaned):
        return False
    # Adverbial / connective endings that already form a subtitle beat.
    if re.search(
        r"(?:위해서|위해|동안|때문에|부터|까지|처럼|같이|보고)$",
        cleaned,
    ):
        return False
    tokens = _SOURCE_WEIGHT_TOKEN_RE.findall(cleaned)
    if len(tokens) <= 1:
        return True
    # Extremely short two-token scraps only (e.g. \"그 놈\"), not full phrases.
    return len(tokens) == 2 and len(cleaned.replace(" ", "")) <= 4


def source_text_weight(text: str) -> int:
    """Relative length used when laying one translation across timed slots."""
    tokens = _SOURCE_WEIGHT_TOKEN_RE.findall(text or "")
    if tokens:
        return max(1, len(tokens))
    cleaned = (text or "").strip()
    return max(1, len(cleaned))


def group_indices_for_translation(
    chunks: list[UtteranceChunk],
    *,
    max_gap_ms: int = 2500,
) -> list[list[int]]:
    """Group breath-split scraps that still form one spoken thought.

    Timing slots stay separate (breath boundaries). Only short incomplete
    fragments (``너를``) share a translation with the following beat; complete
    phrases keep a 1:1 source/target pair.
    """
    if not chunks:
        return []
    groups: list[list[int]] = []
    current = [0]
    for index in range(1, len(chunks)):
        prev = chunks[index - 1]
        nxt = chunks[index]
        gap = nxt.start_ms - prev.end_ms
        same_speaker = (prev.speaker_id or "") == (nxt.speaker_id or "")
        dangling = is_translation_dangling(prev.text)
        if same_speaker and dangling and 0 <= gap <= max_gap_ms:
            current.append(index)
        else:
            groups.append(current)
            current = [index]
    groups.append(current)
    return groups


def expand_grouped_translations(
    chunks: list[UtteranceChunk],
    group_indices: list[list[int]],
    group_translations: list[str],
) -> list[str]:
    """Lay each group translation onto its timed source slots by source weight."""
    if not chunks:
        return []
    out = [""] * len(chunks)
    for indices, translated in zip(group_indices, group_translations):
        text = (translated or "").strip()
        if len(indices) == 1:
            out[indices[0]] = text
            continue
        weights = [source_text_weight(chunks[i].text) for i in indices]
        parts = _split_text_by_weights(text, weights)
        for slot_i, part in zip(indices, parts):
            out[slot_i] = (part or "").strip()
        # Never leave a timed slot empty when the group had a translation.
        if text and any(not out[i] for i in indices):
            parts = _split_text_by_weights(text, [1] * len(indices))
            for slot_i, part in zip(indices, parts):
                if not out[slot_i]:
                    out[slot_i] = (part or "").strip() or text
    return out


def merge_dangling_chunks(
    chunks: list[UtteranceChunk],
    *,
    max_gap_ms: int = 1500,
    max_duration_ms: int = 13000,
) -> list[UtteranceChunk]:
    """Merge consecutive fragments that do not yet complete a sentence.

    Corporate / interview VO often pauses mid-clause (\"So could the\" / \"open
    itself and\"). Translating each breath alone loses meaning; glue them until
    punctuation or a long silence ends the thought.
    """
    if not chunks:
        return []
    merged: list[UtteranceChunk] = []
    current = chunks[0]
    for nxt in chunks[1:]:
        gap = nxt.start_ms - current.end_ms
        combined = nxt.end_ms - current.start_ms
        same_speaker = (current.speaker_id or "") == (nxt.speaker_id or "")
        dangling = not looks_like_sentence_end(current.text)
        if (
            same_speaker
            and dangling
            and 0 <= gap < max_gap_ms
            and combined <= max_duration_ms
        ):
            words = tuple([*current.words, *nxt.words])
            if words:
                text = _join_tokens(list(words))
            else:
                text = f"{current.text.strip()} {nxt.text.strip()}".strip()
            current = UtteranceChunk(
                start_ms=current.start_ms,
                end_ms=nxt.end_ms,
                text=text or current.text,
                speaker_id=current.speaker_id,
                words=words,
            )
        else:
            merged.append(current)
            current = nxt
    merged.append(current)
    return merged


def build_stage1_chunks(
    words: list[TimedToken],
    speaker_turns: list[tuple[int, int, str, str]] | None,
    *,
    pause_ms: int = 280,
    max_duration_ms: int = 8000,
) -> list[UtteranceChunk]:
    """Stage 1: natural translation chunks from word timing (+ optional diarization).

    Prefer speaker-turn text when available (more accurate than Whisper alone),
    but cut on real acoustic pauses using word timestamps — never even time-slicing.
    """
    if not words and not speaker_turns:
        return []

    if speaker_turns:
        chunks: list[UtteranceChunk] = []
        for turn_start, turn_end, speaker, turn_text in speaker_turns:
            turn_start = quantize_ms(turn_start)
            turn_end = quantize_ms(turn_end)
            if turn_end <= turn_start:
                continue
            in_turn = [
                word
                for word in words
                if word.end_ms > turn_start and word.start_ms < turn_end
            ]
            groups = group_tokens_by_pause(
                in_turn,
                pause_ms=pause_ms,
                max_duration_ms=max_duration_ms,
            )
            if groups:
                for group in groups:
                    text = _join_tokens(group)
                    if not text:
                        continue
                    chunks.append(
                        UtteranceChunk(
                            start_ms=group[0].start_ms,
                            end_ms=group[-1].end_ms,
                            text=text,
                            speaker_id=speaker or "speaker_0",
                            words=tuple(group),
                        )
                    )
            else:
                # Prefer spaced word join over raw turn text (often missing spaces).
                clean = (
                    _join_tokens(in_turn)
                    if in_turn
                    else (turn_text or "").strip()
                )
                if clean:
                    chunks.append(
                        UtteranceChunk(
                            start_ms=turn_start,
                            end_ms=turn_end,
                            text=clean,
                            speaker_id=speaker or "speaker_0",
                            words=tuple(in_turn),
                        )
                    )
        return _dedupe_chunks(chunks)

    groups = group_tokens_by_pause(
        words, pause_ms=pause_ms, max_duration_ms=max_duration_ms
    )
    return _dedupe_chunks(
        [
            UtteranceChunk(
                start_ms=group[0].start_ms,
                end_ms=group[-1].end_ms,
                text=_join_tokens(group),
                speaker_id="speaker_0",
                words=tuple(group),
            )
            for group in groups
            if _join_tokens(group)
        ]
    )


def build_fine_utterances(
    chunks: list[UtteranceChunk],
    *,
    pause_ms: int = 120,
    max_duration_ms: int = 3500,
) -> list[FineUtterance]:
    """Stage 3: subdivide each stage-1 chunk on tighter pauses for dub slots."""
    fines: list[FineUtterance] = []
    for parent_idx, chunk in enumerate(chunks):
        tokens = list(chunk.words)
        if not tokens:
            fines.append(
                FineUtterance(
                    parent_idx=parent_idx,
                    start_ms=chunk.start_ms,
                    end_ms=chunk.end_ms,
                    source_text=chunk.text,
                    words=(),
                )
            )
            continue
        groups = group_tokens_by_pause(
            tokens,
            pause_ms=pause_ms,
            max_duration_ms=max_duration_ms,
            min_duration_ms=120,
        )
        if not groups:
            groups = [tokens]
        for group in groups:
            text = _join_tokens(group)
            if not text:
                continue
            fines.append(
                FineUtterance(
                    parent_idx=parent_idx,
                    start_ms=group[0].start_ms,
                    end_ms=group[-1].end_ms,
                    source_text=text,
                    words=tuple(group),
                )
            )
    return fines


def allocate_target_parts(
    target_text: str,
    fine_units: list[FineUtterance],
) -> list[str]:
    """Stage 4 helper: split dub text across fine slots by source length share.

    A later LLM pass can refine; this keeps timing deterministic as a baseline.
    """
    target = (target_text or "").strip()
    if not fine_units:
        return []
    if len(fine_units) == 1:
        return [target]
    if not target:
        return [""] * len(fine_units)

    weights = [max(1, len(unit.source_text.strip())) for unit in fine_units]
    return _split_text_by_weights(target, weights)


_BOUNDARY_DEDUPE_MIN_CHARS = 4
_BOUNDARY_CONTAINED_MIN_CHARS = 6
_BOUNDARY_DEDUPE_MAX_GAP_MS = 280


def _boundary_overlap_chars(left: str, right: str) -> int:
    """Longest suffix of ``left`` that equals a prefix of ``right``."""
    a = (left or "").strip()
    b = (right or "").strip()
    if not a or not b:
        return 0
    max_n = min(len(a), len(b))
    for n in range(max_n, _BOUNDARY_DEDUPE_MIN_CHARS - 1, -1):
        if a.endswith(b[:n]):
            return n
    return 0


def dedupe_boundary_overlaps(chunks: list[UtteranceChunk]) -> list[UtteranceChunk]:
    """Remove duplicated phrase tails/heads between consecutive ASR chunks.

    Whisper often repeats a clause across a breath boundary (e.g. both chunks
    contain ``놀라는데``). Keep timing; trim the repeated prefix from the next
    chunk, or merge when the next text is fully contained in the previous.
    Short Vietnamese syllables must not be treated as duplicates.
    """
    if not chunks:
        return []
    result: list[UtteranceChunk] = [chunks[0]]
    for nxt in chunks[1:]:
        prev = result[-1]
        prev_text = prev.text.strip()
        nxt_text = nxt.text.strip()
        if not nxt_text:
            continue
        if not prev_text:
            result.append(nxt)
            continue
        adjacent = (nxt.start_ms - prev.end_ms) <= _BOUNDARY_DEDUPE_MAX_GAP_MS
        compact_prev = re.sub(r"\s+", "", prev_text)
        compact_nxt = re.sub(r"\s+", "", nxt_text)
        if adjacent and (
            compact_nxt == compact_prev
            or (
                compact_nxt
                and compact_prev.endswith(compact_nxt)
                and len(compact_nxt) >= _BOUNDARY_CONTAINED_MIN_CHARS
            )
        ):
            result[-1] = UtteranceChunk(
                start_ms=prev.start_ms,
                end_ms=max(prev.end_ms, nxt.end_ms),
                text=prev_text,
                speaker_id=prev.speaker_id,
                words=tuple([*prev.words, *nxt.words]) if prev.words or nxt.words else (),
            )
            continue
        overlap = _boundary_overlap_chars(prev_text, nxt_text) if adjacent else 0
        if overlap >= _BOUNDARY_DEDUPE_MIN_CHARS:
            trimmed = nxt_text[overlap:].lstrip(" ,،、")
            if not trimmed:
                result[-1] = UtteranceChunk(
                    start_ms=prev.start_ms,
                    end_ms=max(prev.end_ms, nxt.end_ms),
                    text=prev_text,
                    speaker_id=prev.speaker_id,
                    words=tuple([*prev.words, *nxt.words])
                    if prev.words or nxt.words
                    else (),
                )
                continue
            nxt = UtteranceChunk(
                start_ms=nxt.start_ms,
                end_ms=nxt.end_ms,
                text=trimmed,
                speaker_id=nxt.speaker_id,
                words=nxt.words,
            )
        result.append(nxt)
    return result


def align_document_translation(
    chunks: list[UtteranceChunk],
    document_translation: str,
) -> list[str]:
    """Lay one full-document translation onto timed source chunks by weight."""
    if not chunks:
        return []
    fine = [
        FineUtterance(
            parent_idx=index,
            start_ms=chunk.start_ms,
            end_ms=chunk.end_ms,
            source_text=chunk.text,
            words=chunk.words,
        )
        for index, chunk in enumerate(chunks)
    ]
    return allocate_target_parts(document_translation, fine)


def _split_text_by_weights(text: str, weights: list[int]) -> list[str]:
    """Split ``text`` into ``len(weights)`` pieces proportional to ``weights``."""
    target = (text or "").strip()
    if not weights:
        return []
    if len(weights) == 1:
        return [target]
    if not target:
        return [""] * len(weights)

    safe_weights = [max(1, int(weight)) for weight in weights]
    total_w = sum(safe_weights)
    tokens = target.split()
    if len(tokens) >= len(safe_weights):
        parts: list[str] = []
        cursor = 0
        for index, weight in enumerate(safe_weights):
            if index == len(safe_weights) - 1:
                parts.append(" ".join(tokens[cursor:]).strip())
                break
            # ceil biases setup words onto earlier dramatic beats
            # (너를 → "I'm going to", 제거한다 → "eliminate you.").
            take = max(1, math.ceil(len(tokens) * weight / total_w))
            take = min(
                take, len(tokens) - cursor - (len(safe_weights) - index - 1)
            )
            parts.append(" ".join(tokens[cursor : cursor + take]).strip())
            cursor += take
        return parts

    # No spaces (e.g. CJK target): split by character weight.
    chars = list(target)
    parts = []
    cursor = 0
    for index, weight in enumerate(safe_weights):
        if index == len(safe_weights) - 1:
            parts.append("".join(chars[cursor:]).strip())
            break
        take = max(1, math.ceil(len(chars) * weight / total_w))
        take = min(take, len(chars) - cursor - (len(safe_weights) - index - 1))
        parts.append("".join(chars[cursor : cursor + take]).strip())
        cursor += take
    return parts


def _force_split_tokens_by_duration(
    tokens: list[TimedToken],
    *,
    max_duration_ms: int,
) -> list[list[TimedToken]]:
    """Hard-cut a continuous word run so each group stays within ``max_duration_ms``."""
    if not tokens:
        return []
    if tokens[-1].end_ms - tokens[0].start_ms <= max_duration_ms:
        return [tokens]

    groups: list[list[TimedToken]] = []
    current: list[TimedToken] = []
    for token in tokens:
        if (
            current
            and token.end_ms - current[0].start_ms > max_duration_ms
            and len(current) >= 1
        ):
            groups.append(current)
            current = [token]
        else:
            current.append(token)
    if current:
        groups.append(current)
    return groups


def split_overlong_chunks(
    chunks: list[UtteranceChunk],
    *,
    max_duration_ms: int,
) -> list[UtteranceChunk]:
    """Hard-cap utterance length even when Whisper omitted word timestamps.

    dalat's live transcript was two Whisper drafts (16s + 3.5s) because the
    multipart form never requested ``words``. Those drafts must still be cut
    to ``speech_segment_max_seconds`` or TTS/mix treat them as one line.
    """
    if max_duration_ms <= 0 or not chunks:
        return list(chunks)
    out: list[UtteranceChunk] = []
    for chunk in chunks:
        duration = chunk.end_ms - chunk.start_ms
        words = [word for word in chunk.words if word.text.strip()]
        if duration <= max_duration_ms:
            out.append(chunk)
            continue
        if len(words) >= 2:
            groups = _force_split_tokens_by_duration(
                words, max_duration_ms=max_duration_ms
            )
            parts = _split_text_by_weights(
                chunk.text,
                [max(1, len(_join_tokens(group))) for group in groups],
            )
            for group, part in zip(groups, parts):
                start_ms = quantize_ms(group[0].start_ms)
                end_ms = quantize_ms(max(group[-1].end_ms, start_ms + 80))
                text = (part or "").strip() or _join_tokens(group)
                if not text or end_ms <= start_ms:
                    continue
                out.append(
                    UtteranceChunk(
                        start_ms=start_ms,
                        end_ms=end_ms,
                        text=text,
                        speaker_id=chunk.speaker_id,
                        words=tuple(group),
                    )
                )
            continue
        part_count = max(1, (duration + max_duration_ms - 1) // max_duration_ms)
        tokens = chunk.text.split()
        if tokens:
            part_count = min(part_count, len(tokens))
        if part_count <= 1:
            out.append(chunk)
            continue
        weights = [1] * part_count
        if tokens and len(tokens) >= part_count:
            weights = []
            cursor = 0
            for index in range(part_count):
                remaining_tokens = len(tokens) - cursor
                remaining_parts = part_count - index
                take = max(1, round(remaining_tokens / remaining_parts))
                weights.append(max(1, take))
                cursor += take
        parts = _split_text_by_weights(chunk.text, weights)
        for index, part in enumerate(parts):
            start_ms = chunk.start_ms + round(duration * index / part_count)
            end_ms = (
                chunk.end_ms
                if index == part_count - 1
                else chunk.start_ms + round(duration * (index + 1) / part_count)
            )
            text = (part or "").strip() or chunk.text
            if end_ms <= start_ms:
                continue
            out.append(
                UtteranceChunk(
                    start_ms=quantize_ms(start_ms),
                    end_ms=quantize_ms(end_ms),
                    text=text,
                    speaker_id=chunk.speaker_id,
                    words=(),
                )
            )
    return out or list(chunks)


def place_long_units_by_timestamps(
    chunks: list[UtteranceChunk],
    translations: list[str],
    *,
    max_duration_ms: int = 13000,
    pause_ms: int = 450,
) -> tuple[list[UtteranceChunk], list[str]]:
    """Translate-sized meaning units stay intact until placement.

    Units longer than ``max_duration_ms`` are split on original word pauses
    (falling back to duration cuts) and both source/target texts are
    proportionally laid onto those audio windows.
    """
    if not chunks:
        return [], []
    if len(translations) < len(chunks):
        translations = list(translations) + [""] * (len(chunks) - len(translations))

    out_chunks: list[UtteranceChunk] = []
    out_translations: list[str] = []
    for chunk, target in zip(chunks, translations):
        duration = chunk.end_ms - chunk.start_ms
        words = [word for word in chunk.words if word.text.strip()]
        if duration <= max_duration_ms or len(words) < 2:
            out_chunks.append(chunk)
            out_translations.append(target)
            continue

        groups = group_tokens_by_pause(
            words,
            pause_ms=pause_ms,
            max_duration_ms=max_duration_ms,
            min_duration_ms=200,
        )
        if not groups:
            groups = [words]

        expanded: list[list[TimedToken]] = []
        for group in groups:
            if group[-1].end_ms - group[0].start_ms <= max_duration_ms:
                expanded.append(group)
            else:
                expanded.extend(
                    _force_split_tokens_by_duration(
                        group, max_duration_ms=max_duration_ms
                    )
                )
        if len(expanded) <= 1:
            out_chunks.append(chunk)
            out_translations.append(target)
            continue

        fine_stubs = [
            FineUtterance(
                parent_idx=0,
                start_ms=group[0].start_ms,
                end_ms=group[-1].end_ms,
                source_text=_join_tokens(group) or " ",
                words=tuple(group),
            )
            for group in expanded
        ]
        source_parts = _split_text_by_weights(
            chunk.text,
            [max(1, len(unit.source_text.strip())) for unit in fine_stubs],
        )
        target_parts = allocate_target_parts(target, fine_stubs)
        for group, source_part, target_part, stub in zip(
            expanded, source_parts, target_parts, fine_stubs
        ):
            start_ms = quantize_ms(group[0].start_ms)
            end_ms = quantize_ms(max(group[-1].end_ms, start_ms + 80))
            text = (source_part or "").strip() or stub.source_text.strip()
            if not text:
                continue
            out_chunks.append(
                UtteranceChunk(
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=text,
                    speaker_id=chunk.speaker_id,
                    words=tuple(group),
                )
            )
            out_translations.append((target_part or "").strip() or text)
    return out_chunks, out_translations


def _dedupe_chunks(chunks: list[UtteranceChunk]) -> list[UtteranceChunk]:
    ordered = sorted(chunks, key=lambda item: (item.start_ms, item.end_ms))
    result: list[UtteranceChunk] = []
    for chunk in ordered:
        start = chunk.start_ms
        end = chunk.end_ms
        if result and start < result[-1].end_ms:
            start = result[-1].end_ms
        if end <= start or not chunk.text.strip():
            continue
        result.append(
            UtteranceChunk(
                start_ms=start,
                end_ms=end,
                text=chunk.text.strip(),
                speaker_id=chunk.speaker_id,
                words=chunk.words,
            )
        )
    return result
