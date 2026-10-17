"""Utterance-aware 3-minute windows for long-form dub jobs.

Extract (download + STT + translation) still runs on the full source.
Dub (Demucs / speech-scrub / mix) is split so a 40-minute project like 지영
does not keep one Demucs/mix graph in RAM. Cuts wait until the line that
crosses the 3-minute mark finishes, then land at the next utterance start
so TTS that spills a little into the gap is not chopped.

Windows are contiguous ``[start_ms, end_ms)`` covering ``[0, duration_ms)``.
The dubbed PCM pieces are concatenated sample-accurately and muxed once
onto the original video so the picture never has a cut.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

# Product defaults (overridable via Settings).
DEFAULT_MIN_DURATION_MS = 5 * 60 * 1000
DEFAULT_TARGET_MS = 3 * 60 * 1000
DEFAULT_MAX_OVERRUN_MS = 2 * 60 * 1000
DEFAULT_MIN_TAIL_MS = 20 * 1000


def _int_ms(value: object) -> int | None:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed


def utterance_span(row: dict[str, Any]) -> tuple[int, int] | None:
    """``(start_ms, end_ms)`` using the later of source/translation ends."""
    start = _int_ms(row.get("start_ms"))
    if start is None:
        return None
    source_end = _int_ms(row.get("source_end_ms"))
    spoken_end = _int_ms(row.get("end_ms"))
    end = max(value for value in (source_end, spoken_end) if value is not None) if (
        source_end is not None or spoken_end is not None
    ) else None
    if end is None or end <= start:
        return None
    return start, end


def utterance_spans(segments: Iterable[dict[str, Any]]) -> list[tuple[int, int]]:
    spans = [span for row in segments if (span := utterance_span(row))]
    spans.sort(key=lambda item: (item[0], item[1]))
    return spans


def cut_after_target(
    spans: Sequence[tuple[int, int]],
    target_ms: int,
    duration_ms: int,
    *,
    max_overrun_ms: int = DEFAULT_MAX_OVERRUN_MS,
) -> int:
    """First safe cut at/after ``target_ms`` that does not bisect a line.

    If a caption is still speaking at ``target_ms``, wait for that line to
    finish (capped by ``max_overrun_ms``), then cut at the next caption's
    start so the trailing gap stays with the finishing line.
    If ``target_ms`` is already in silence, cut there.
    """
    duration_ms = max(0, int(duration_ms))
    target_ms = min(max(0, int(target_ms)), duration_ms)
    if target_ms >= duration_ms:
        return duration_ms
    overlapping = [span for span in spans if span[0] <= target_ms < span[1]]
    if overlapping:
        line_end = max(span[1] for span in overlapping)
        line_end = min(line_end, target_ms + max(0, int(max_overrun_ms)), duration_ms)
        next_starts = [span[0] for span in spans if span[0] >= line_end]
        if next_starts:
            return min(next_starts[0], duration_ms)
        return min(line_end, duration_ms)
    return target_ms


def plan_dub_chunks(
    segments: Iterable[dict[str, Any]],
    duration_ms: int,
    *,
    min_duration_ms: int = DEFAULT_MIN_DURATION_MS,
    target_ms: int = DEFAULT_TARGET_MS,
    max_overrun_ms: int = DEFAULT_MAX_OVERRUN_MS,
    min_tail_ms: int = DEFAULT_MIN_TAIL_MS,
) -> list[tuple[int, int]]:
    """Contiguous windows covering the source. One window if under 5 minutes."""
    duration_ms = max(0, int(duration_ms))
    if duration_ms <= 0:
        return [(0, 0)]
    if duration_ms < max(1, int(min_duration_ms)):
        return [(0, duration_ms)]
    target_ms = max(1_000, int(target_ms))
    spans = utterance_spans(segments)
    cuts = [0]
    cursor = target_ms
    while cursor < duration_ms:
        cut = cut_after_target(
            spans, cursor, duration_ms, max_overrun_ms=max_overrun_ms
        )
        if cut <= cuts[-1] + 250:
            cursor = cuts[-1] + target_ms
            if cursor >= duration_ms:
                break
            continue
        remaining = duration_ms - cut
        if remaining < max(0, int(min_tail_ms)):
            break
        cuts.append(cut)
        cursor = cut + target_ms
    cuts.append(duration_ms)
    windows = [
        (start, end) for start, end in zip(cuts, cuts[1:]) if end > start
    ]
    return windows or [(0, duration_ms)]


def plan_dub_chunks_from_settings(
    segments: Iterable[dict[str, Any]],
    duration_ms: int,
    settings: Any,
) -> list[tuple[int, int]]:
    return plan_dub_chunks(
        segments,
        duration_ms,
        min_duration_ms=int(getattr(settings, "dub_chunk_min_duration_seconds", 300) * 1000),
        target_ms=int(getattr(settings, "dub_chunk_target_seconds", 180) * 1000),
        max_overrun_ms=int(getattr(settings, "dub_chunk_max_overrun_seconds", 120) * 1000),
        min_tail_ms=int(getattr(settings, "dub_chunk_min_tail_seconds", 20) * 1000),
    )


def shift_ranges(
    ranges_ms: Sequence[tuple[int, int]],
    start_ms: int,
    end_ms: int,
) -> list[tuple[int, int]]:
    """Intersect ``ranges_ms`` with ``[start_ms, end_ms)`` and zero the origin."""
    shifted: list[tuple[int, int]] = []
    for raw_start, raw_end in ranges_ms:
        lo = max(int(raw_start), start_ms)
        hi = min(int(raw_end), end_ms)
        if hi > lo:
            shifted.append((lo - start_ms, hi - start_ms))
    return shifted


def shift_placed_clips(
    placed_clips: Sequence[tuple[str, int]],
    start_ms: int,
    end_ms: int,
) -> list[tuple[str, int]]:
    """Keep clips whose start falls inside the window; rebase to 0."""
    local: list[tuple[str, int]] = []
    for path, offset in placed_clips:
        if start_ms <= int(offset) < end_ms:
            local.append((path, int(offset) - start_ms))
    return local
