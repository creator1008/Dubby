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
    async def diarize(self, audio_path: str) -> list[SpeakerTurn]: ...


class MockDiarizationProvider:
    async def diarize(self, audio_path: str) -> list[SpeakerTurn]:
        del audio_path
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

    async def diarize(self, audio_path: str) -> list[SpeakerTurn]:
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

    async def diarize(self, audio_path: str) -> list[SpeakerTurn]:
        data = {
            "model": self._settings.diarization_model,
            "response_format": "diarized_json",
            "chunking_strategy": "auto",
        }
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
                errors.TRANSCRIPTION_FAILED,
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


def assign_speakers(
    segments: list[tuple[int, int]], turns: list[SpeakerTurn]
) -> list[tuple[str | None, bool]]:
    """Assign the largest-overlap speaker; ambiguous overlap safely falls back."""
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
        speaker = ranked[0][0] if ranked and not ambiguous and total > 0 else None
        assigned.append((speaker, ambiguous))
    return assigned


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
