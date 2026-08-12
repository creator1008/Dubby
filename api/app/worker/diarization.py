"""Optional speaker diarization behind a provider-neutral interface."""

from __future__ import annotations

import asyncio
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


def collapse_minor_speakers(
    assignments: list[tuple[str | None, bool]],
    segments: list[tuple[int, int]],
    *,
    min_total_ms: int = 3000,
    min_share: float = 0.08,
) -> list[tuple[str | None, bool]]:
    """Fold tiny / spurious diarization labels into neighboring major speakers.

    OpenAI diarize often invents a third label for 1–2 short fragments. Those
    steal a Voice Box slot and scramble 화자 1/2 mapping.
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

    grand = max(1, sum(totals.values()))
    majors = {
        sid
        for sid, dur in totals.items()
        if dur >= min_total_ms and (dur / grand) >= min_share
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
