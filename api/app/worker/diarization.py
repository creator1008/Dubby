"""Optional speaker diarization behind a provider-neutral interface."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from ..config import Settings
from . import errors
from .errors import PipelineError


@dataclass(frozen=True)
class SpeakerTurn:
    start_ms: int
    end_ms: int
    speaker_id: str
    text: str = ""


class DiarizationProvider(Protocol):
    async def diarize(
        self, audio_path: str, *, language: str | None = None
    ) -> list[SpeakerTurn]: ...


class MockDiarizationProvider:
    async def diarize(
        self, audio_path: str, *, language: str | None = None
    ) -> list[SpeakerTurn]:
        del audio_path, language
        return [
            SpeakerTurn(0, 2667, "speaker_0"),
            SpeakerTurn(2667, 5334, "speaker_1"),
            SpeakerTurn(5334, 8000, "speaker_0"),
        ]


class PyannoteDiarizationProvider:
    def __init__(self, settings: Settings) -> None:
        if not settings.pyannote_auth_token:
            raise PipelineError(errors.CONFIG_MISSING, "PYANNOTE_AUTH_TOKEN is not configured")
        self._settings = settings

    async def diarize(
        self, audio_path: str, *, language: str | None = None
    ) -> list[SpeakerTurn]:
        del language

        def _run() -> list[SpeakerTurn]:
            try:
                from pyannote.audio import Pipeline
            except ImportError as exc:
                raise PipelineError(
                    errors.CONFIG_MISSING,
                    "pyannote.audio is not installed; install worker diarization extras",
                ) from exc
            pipeline = Pipeline.from_pretrained(
                self._settings.pyannote_model,
                use_auth_token=self._settings.pyannote_auth_token,
            )
            result = pipeline(audio_path)
            return [
                SpeakerTurn(
                    max(0, round(turn.start * 1000)),
                    max(1, round(turn.end * 1000)),
                    str(label),
                )
                for turn, _, label in result.itertracks(yield_label=True)
            ]

        return await asyncio.to_thread(_run)


class OpenAIDiarizationProvider:
    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise PipelineError(errors.CONFIG_MISSING, "OPENAI_API_KEY is not configured")
        self._settings = settings

    async def diarize(
        self, audio_path: str, *, language: str | None = None
    ) -> list[SpeakerTurn]:
        data: dict[str, str] = {
            "model": self._settings.diarization_model,
            "response_format": "diarized_json",
            "chunking_strategy": "auto",
        }
        lang = (language or "").strip().lower().split("-", 1)[0]
        if lang:
            data["language"] = lang
        files = {
            "file": (
                Path(audio_path).name,
                Path(audio_path).read_bytes(),
                "audio/mpeg",
            )
        }
        async with httpx.AsyncClient(timeout=600) as client:
            response = await client.post(
                f"{self._settings.openai_base_url.rstrip('/')}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._settings.openai_api_key}"},
                data=data,
                files=files,
            )
        if response.status_code >= 400:
            raise PipelineError(
                errors.ASR_FAILED,
                f"OpenAI diarization returned {response.status_code}: "
                f"{response.text[:300]}",
                retryable=response.status_code == 429 or response.status_code >= 500,
            )
        return [
            SpeakerTurn(
                start_ms=max(0, round(float(segment.get("start", 0)) * 1000)),
                end_ms=max(1, round(float(segment.get("end", 0)) * 1000)),
                speaker_id=str(segment.get("speaker") or "speaker_0"),
                text=str(segment.get("text") or "").strip(),
            )
            for segment in response.json().get("segments") or []
            if float(segment.get("end", 0)) > float(segment.get("start", 0))
        ]


def create_diarization_provider(settings: Settings) -> DiarizationProvider | None:
    if settings.diarization_provider == "mock":
        return MockDiarizationProvider()
    if settings.diarization_provider == "pyannote":
        return PyannoteDiarizationProvider(settings)
    if settings.diarization_provider == "openai":
        return OpenAIDiarizationProvider(settings)
    return None


def normalize_speaker_ids(turns: list[SpeakerTurn]) -> list[SpeakerTurn]:
    """Map provider labels (A/B, SPEAKER_00, …) to ``speaker_1``, ``speaker_2``, …

    First-appearance order matches New Dub 화자 1 / 화자 2 voice slots.
    """
    mapping: dict[str, str] = {}
    normalized: list[SpeakerTurn] = []
    for turn in turns:
        raw = (turn.speaker_id or "").strip() or "speaker_0"
        if raw not in mapping:
            mapping[raw] = f"speaker_{len(mapping) + 1}"
        normalized.append(
            SpeakerTurn(
                start_ms=turn.start_ms,
                end_ms=turn.end_ms,
                speaker_id=mapping[raw],
                text=turn.text,
            )
        )
    return normalized


def assign_speakers(
    segments: list[tuple[int, int]], turns: list[SpeakerTurn]
) -> list[tuple[str | None, bool]]:
    """Assign the largest-overlap speaker; flag soft overlaps without dropping the id.

    Clearing ``speaker_id`` on overlap made multi-speaker dubs fall back to one
    default voice. Keep the majority speaker so 화자 1 / 화자 2 stay distinct.
    """
    assigned: list[tuple[str | None, bool]] = []
    for start, end in segments:
        by_speaker: dict[str, int] = {}
        for turn in turns:
            overlap = max(0, min(end, turn.end_ms) - max(start, turn.start_ms))
            if overlap:
                by_speaker[turn.speaker_id] = by_speaker.get(turn.speaker_id, 0) + overlap
        ranked = sorted(by_speaker.items(), key=lambda item: item[1], reverse=True)
        total = sum(by_speaker.values())
        ambiguous = len(ranked) > 1 and ranked[1][1] >= ranked[0][1] * 0.5
        speaker = ranked[0][0] if ranked and total > 0 else None
        assigned.append((speaker, ambiguous))
    return assigned


_TEXT_TOKEN_RE = re.compile(r"\S+")


def split_text_by_weights(text: str, weights: list[int]) -> list[str]:
    """Split ``text`` into ``len(weights)`` pieces proportional to weights."""
    tokens = _TEXT_TOKEN_RE.findall(text or "")
    if not weights:
        return []
    if len(weights) == 1:
        return [text.strip()]
    if not tokens:
        return [""] * len(weights)
    safe = [max(1, int(w)) for w in weights]
    total_w = sum(safe)
    raw_counts = [max(1, round(len(tokens) * w / total_w)) for w in safe]
    while sum(raw_counts) > len(tokens):
        i = max(range(len(raw_counts)), key=lambda j: (raw_counts[j], j))
        if raw_counts[i] <= 1:
            break
        raw_counts[i] -= 1
    while sum(raw_counts) < len(tokens):
        raw_counts[-1] += 1
    pieces: list[str] = []
    idx = 0
    for count in raw_counts:
        pieces.append(" ".join(tokens[idx : idx + count]).strip())
        idx += count
    return pieces


def _merged_span_ms(rows: list[tuple[int, int, str, str]]) -> list[tuple[int, int]]:
    spans = sorted((int(start), int(end)) for start, end, _, _ in rows if end > start)
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _uncovered_ms(
    start: int, end: int, rows: list[tuple[int, int, str, str]]
) -> int:
    cursor = start
    missing = 0
    for span_start, span_end in _merged_span_ms(rows):
        clipped_start = max(start, span_start)
        clipped_end = min(end, span_end)
        if clipped_end <= clipped_start:
            continue
        if clipped_start > cursor:
            missing += clipped_start - cursor
        cursor = max(cursor, clipped_end)
    if end > cursor:
        missing += end - cursor
    return missing


def _close_split_holes(
    start: int,
    end: int,
    rows: list[tuple[int, int, str, str]],
) -> list[tuple[int, int, str, str]]:
    """Stretch neighboring split rows so small diarize holes stay scrubbed."""
    ordered = sorted(rows, key=lambda row: (row[0], row[1]))
    if not ordered:
        return rows
    first = ordered[0]
    if first[0] > start:
        ordered[0] = (start, first[1], first[2], first[3])
    last = ordered[-1]
    if last[1] < end:
        ordered[-1] = (last[0], end, last[2], last[3])
    closed: list[tuple[int, int, str, str]] = [ordered[0]]
    for row in ordered[1:]:
        prev = closed[-1]
        if row[0] > prev[1]:
            mid = (prev[1] + row[0]) // 2
            closed[-1] = (prev[0], max(prev[1], mid), prev[2], prev[3])
            row = (min(row[0], mid), row[1], row[2], row[3])
        closed.append(row)
    return closed


def split_timed_texts_on_turns(
    segments: list[tuple[int, int, str, str]],
    turns: list[SpeakerTurn],
    *,
    min_overlap_ms: int = 400,
) -> list[tuple[int, int, str, str]]:
    """Cut transcript segments wherever diarization speaker turns change.

    Gemini STT often labels A/B but merges a whole conversation into one or two
    long captions. OpenAI/pyannote turns supply the real change points; Gemini
    wording is kept and sliced by duration share.
    """
    unique = {(t.speaker_id or "").strip() for t in turns if (t.speaker_id or "").strip()}
    if len(unique) < 2 or not segments:
        return segments

    out: list[tuple[int, int, str, str]] = []
    for start, end, text, speaker in segments:
        overlapping = [
            turn
            for turn in turns
            if max(0, min(end, turn.end_ms) - max(start, turn.start_ms)) >= min_overlap_ms
        ]
        overlapping.sort(key=lambda t: (t.start_ms, t.end_ms))
        if len(overlapping) <= 1:
            sid = overlapping[0].speaker_id if overlapping else speaker
            out.append((start, end, text, sid or speaker))
            continue
        weights = [
            max(1, min(end, turn.end_ms) - max(start, turn.start_ms))
            for turn in overlapping
        ]
        pieces = split_text_by_weights(text, weights)
        emitted_rows: list[tuple[int, int, str, str]] = []
        for turn, piece in zip(overlapping, pieces):
            piece_start = max(start, turn.start_ms)
            piece_end = min(end, turn.end_ms)
            if piece_end <= piece_start:
                continue
            spoken = (piece or turn.text or "").strip()
            if not spoken:
                continue
            emitted_rows.append(
                (piece_start, piece_end, spoken, turn.speaker_id or speaker)
            )
        if not emitted_rows:
            out.append((start, end, text, speaker))
            continue
        # Diarize often skips a mid-caption turn. Splitting then drops that
        # time range, so the mix keeps original speech in the hole.
        if _uncovered_ms(start, end, emitted_rows) >= max(
            min_overlap_ms, int((end - start) * 0.15)
        ):
            majority = max(
                overlapping,
                key=lambda t: max(
                    0, min(end, t.end_ms) - max(start, t.start_ms)
                ),
            )
            out.append((start, end, text, majority.speaker_id or speaker))
            continue
        out.extend(_close_split_holes(start, end, emitted_rows))
    out.sort(key=lambda row: (row[0], row[1]))
    return out


def collapse_minor_speakers(
    assignments: list[tuple[str | None, bool]],
    segments: list[tuple[int, int]],
    *,
    min_total_ms: int | None = None,
    min_share: float = 0.08,
) -> list[tuple[str | None, bool]]:
    """Fold tiny / spurious diarization labels into neighboring major speakers.

    OpenAI diarize often invents a third label for 1–2 short fragments. Those
    steal a Voice Box slot and scramble 화자 1/2 mapping.

    Absolute 3s floors wipe a real second speaker on short clips (a 2.5s
    reply in a 15s video). Scale the floor to the clip span.
    """
    if not assignments or not segments or len(assignments) != len(segments):
        return assignments

    totals: dict[str, int] = {}
    for (sid, _), (start, end) in zip(assignments, segments):
        if not sid:
            continue
        totals[sid] = totals.get(sid, 0) + max(0, int(end) - int(start))
    if not totals:
        return assignments

    span = max((int(end) for _, end in segments), default=0) - min(
        (int(start) for start, _ in segments), default=0
    )
    floor = (
        int(min_total_ms)
        if min_total_ms is not None
        else min(3000, max(800, int(max(0, span) * 0.12)))
    )

    grand = max(1, sum(totals.values()))
    majors = {
        sid
        for sid, dur in totals.items()
        if dur >= floor and (dur / grand) >= min_share
    }
    if not majors:
        majors = {max(totals.items(), key=lambda item: item[1])[0]}
    if len(majors) >= len(totals):
        return _renumber_speaker_assignments(assignments)

    primary = max(majors, key=lambda sid: totals.get(sid, 0))

    def nearest_major(index: int) -> str:
        for j in range(index - 1, -1, -1):
            sid = assignments[j][0]
            if sid in majors:
                return sid
        for j in range(index + 1, len(assignments)):
            sid = assignments[j][0]
            if sid in majors:
                return sid
        return primary

    collapsed: list[tuple[str | None, bool]] = []
    for index, (sid, overlap) in enumerate(assignments):
        if sid is None or sid in majors:
            collapsed.append((sid, overlap))
        else:
            collapsed.append((nearest_major(index), False))
    return _renumber_speaker_assignments(collapsed)


def _renumber_speaker_assignments(
    assignments: list[tuple[str | None, bool]],
) -> list[tuple[str | None, bool]]:
    """Re-label to speaker_1..N in first-appearance order (Voice Box slot order)."""
    mapping: dict[str, str] = {}
    out: list[tuple[str | None, bool]] = []
    for sid, overlap in assignments:
        if not sid:
            out.append((None, overlap))
            continue
        if sid not in mapping:
            mapping[sid] = f"speaker_{len(mapping) + 1}"
        out.append((mapping[sid], overlap))
    return out


def split_speaker_turns(
    turns: list[SpeakerTurn],
    max_duration_ms: int = 6000,
) -> list[SpeakerTurn]:
    """Cut transcript-bearing turns at speaker changes and time intervals."""
    result: list[SpeakerTurn] = []
    for turn in turns:
        clean = turn.text.strip()
        duration = turn.end_ms - turn.start_ms
        if not clean or duration <= 0:
            continue
        part_count = max(1, (duration + max_duration_ms - 1) // max_duration_ms)
        words = clean.split()
        part_count = min(part_count, len(words))
        if part_count == 1:
            result.append(turn)
            continue

        cursor = 0
        for part_idx in range(part_count):
            remaining_words = len(words) - cursor
            remaining_parts = part_count - part_idx
            take = max(1, round(remaining_words / remaining_parts))
            part_words = words[cursor : cursor + take]
            part_start = turn.start_ms + round(duration * cursor / len(words))
            cursor += take
            part_end = (
                turn.end_ms
                if part_idx == part_count - 1
                else turn.start_ms + round(duration * cursor / len(words))
            )
            result.append(
                SpeakerTurn(
                    start_ms=part_start,
                    end_ms=part_end,
                    speaker_id=turn.speaker_id,
                    text=" ".join(part_words),
                )
            )
    return result
