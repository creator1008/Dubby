"""ElevenLabs Scribe v2 STT + speaker diarization + word timestamps.

Official API (do not invent endpoints):

- POST https://api.elevenlabs.io/v1/speech-to-text
  Docs: https://elevenlabs.io/docs/api-reference/speech-to-text/convert
- ``model_id=scribe_v2``, ``diarize=true``, ``timestamps_granularity=word``
- Each word may include ``start``, ``end``, ``speaker_id``, ``type``

Scribe is the production default (word timestamps + diarize). WhisperX is a
local-only fallback when Scribe fails and the optional ``whisperx`` package
is installed. There is no invented Scribe endpoint — convert is POST
``/v1/speech-to-text`` with ``model_id=scribe_v2``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings
from . import errors
from .errors import PipelineError
from .openai_client import SegmentDraft, TranscribeResult
from .utterance_pipeline import TimedToken

logger = logging.getLogger("dubby.worker.scribe")

# Convert API supports large files; Dubby already caps sources at 500 MB / 30 min.
_TIMEOUT = httpx.Timeout(connect=20.0, read=900.0, write=300.0, pool=10.0)

# Quickstart / convert docs use ISO 639-3 style codes (``eng``).
_LANGUAGE_TO_SCRIBE: dict[str, str] = {
    "en": "eng",
    "ko": "kor",
    "vi": "vie",
    "zh": "zho",
    "ja": "jpn",
    "es": "spa",
    "fr": "fra",
    "pt": "por",
    "de": "deu",
    "ru": "rus",
    "ar": "ara",
    "ur": "urd",
    "id": "ind",
    "ms": "msa",
    "tr": "tur",
    "ta": "tam",
    "th": "tha",
    "my": "mya",
}


def scribe_language_code(language: str | None) -> str | None:
    """ISO-639-1 or ISO-639-3, as documented on the convert endpoint."""
    raw = (language or "").strip().lower().split("-", 1)[0]
    if not raw:
        return None
    if len(raw) in {2, 3}:
        return raw
    return _LANGUAGE_TO_SCRIBE.get(raw)


def _audio_mime(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".webm": "audio/webm",
        ".flac": "audio/flac",
    }.get(suffix, "audio/mpeg")


def parse_scribe_words(payload: dict[str, Any]) -> list[dict[str, Any]]:
    words = payload.get("words")
    if not isinstance(words, list):
        return []
    return [word for word in words if isinstance(word, dict)]


def scribe_words_to_tokens(words: list[dict[str, Any]]) -> list[TimedToken]:
    tokens: list[TimedToken] = []
    for word in words:
        kind = str(word.get("type") or "word").strip().lower()
        if kind in {"spacing", "audio_event"}:
            continue
        text = str(word.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(word.get("start") if word.get("start") is not None else 0)
            end = float(word.get("end") if word.get("end") is not None else start)
        except (TypeError, ValueError):
            continue
        start_ms = max(0, int(round(start * 1000)))
        end_ms = max(start_ms + 1, int(round(end * 1000)))
        tokens.append(
            TimedToken(
                text=text,
                start_ms=start_ms,
                end_ms=end_ms,
                speaker_id=str(word.get("speaker_id") or "").strip() or None,
            )
        )
    return tokens


def group_scribe_tokens(
    tokens: list[TimedToken],
    *,
    breath_pause_ms: int = 650,
    max_segment_seconds: float = 6.0,
) -> list[SegmentDraft]:
    """Turn word timestamps + speaker_id into stable caption units.

    Split on speaker change, breath pauses, or max duration. Do not invent
    gap-filling segments for uncovered silence — those ranges keep original
    audio in the mix.
    """
    if not tokens:
        return []
    max_ms = max(1000, int(round(float(max_segment_seconds) * 1000)))
    groups: list[list[TimedToken]] = []
    current: list[TimedToken] = [tokens[0]]
    for token in tokens[1:]:
        prev = current[-1]
        gap = token.start_ms - prev.end_ms
        speaker_changed = (token.speaker_id or "") != (prev.speaker_id or "")
        span = token.end_ms - current[0].start_ms
        if speaker_changed or gap >= breath_pause_ms or span >= max_ms:
            groups.append(current)
            current = [token]
        else:
            current.append(token)
    groups.append(current)

    drafts: list[SegmentDraft] = []
    for group in groups:
        text = " ".join(tok.text for tok in group).strip()
        text = " ".join(text.split())
        if not text:
            continue
        start_ms = group[0].start_ms
        end_ms = max(start_ms + 1, group[-1].end_ms)
        drafts.append(
            SegmentDraft(
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
                speaker_id=group[0].speaker_id or "speaker_0",
            )
        )
    return drafts


def transcribe_result_from_scribe(
    payload: dict[str, Any],
    *,
    breath_pause_ms: int = 650,
    max_segment_seconds: float = 6.0,
) -> TranscribeResult:
    words = parse_scribe_words(payload)
    tokens = scribe_words_to_tokens(words)
    drafts = group_scribe_tokens(
        tokens,
        breath_pause_ms=breath_pause_ms,
        max_segment_seconds=max_segment_seconds,
    )
    speaker_map: dict[str, str] = {}
    normalized: list[SegmentDraft] = []
    for draft in drafts:
        raw = str(draft.speaker_id or "").strip() or "speaker_0"
        if raw not in speaker_map:
            speaker_map[raw] = f"speaker_{len(speaker_map) + 1}"
        normalized.append(
            SegmentDraft(
                start_ms=draft.start_ms,
                end_ms=draft.end_ms,
                text=draft.text,
                speaker_id=speaker_map[raw],
                emotion_tone=draft.emotion_tone,
                emotion_tag=draft.emotion_tag,
            )
        )
    drafts = normalized
    full = str(payload.get("text") or "").strip()
    if not full:
        full = " ".join(d.text for d in drafts)
    speech_ranges = [(d.start_ms, d.end_ms) for d in drafts if d.end_ms > d.start_ms]
    labeled = len(speaker_map) >= 2
    return TranscribeResult(
        drafts=drafts,
        speech_ranges=speech_ranges,
        words=tokens,
        full_transcript=full,
        speakers_labeled=labeled,
        skip_proofread=True,
        provider="scribe",
    )


class ScribeClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.elevenlabs_api_key:
            raise PipelineError(
                errors.CONFIG_MISSING, "ELEVENLABS_API_KEY is not configured"
            )
        self._settings = settings
        self._base = settings.elevenlabs_base_url.rstrip("/")
        self._headers = {"xi-api-key": settings.elevenlabs_api_key}
        self._client = httpx.AsyncClient(timeout=_TIMEOUT)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def transcribe(
        self,
        audio_path: str,
        language: str,
        *,
        diarize: bool = True,
    ) -> TranscribeResult:
        """POST /v1/speech-to-text with Scribe v2 + optional diarization."""
        lang = scribe_language_code(language)
        # Official convert fields: model_id, timestamps_granularity, diarize,
        # language_code (ISO-639-1 or ISO-639-3), tag_audio_events.
        data: dict[str, str] = {
            "model_id": "scribe_v2",
            "timestamps_granularity": "word",
            "diarize": "true" if diarize else "false",
            "tag_audio_events": "true",
        }
        if lang:
            data["language_code"] = lang
        path = Path(audio_path)
        files = {
            "file": (path.name, path.read_bytes(), _audio_mime(str(path))),
        }
        try:
            resp = await self._client.post(
                f"{self._base}/v1/speech-to-text",
                headers=self._headers,
                data=data,
                files=files,
            )
        except httpx.HTTPError as exc:
            raise PipelineError(
                errors.ASR_FAILED, f"Scribe request failed: {exc}", retryable=True
            ) from exc
        if resp.status_code >= 400:
            raise PipelineError(
                errors.ASR_FAILED,
                f"Scribe returned {resp.status_code}: {resp.text[:300]}",
                retryable=resp.status_code == 429 or resp.status_code >= 500,
            )
        payload = resp.json()
        if not isinstance(payload, dict):
            raise PipelineError(errors.ASR_FAILED, "Scribe JSON was not an object")
        result = transcribe_result_from_scribe(
            payload,
            breath_pause_ms=self._settings.breath_pause_ms,
            max_segment_seconds=self._settings.speech_segment_max_seconds,
        )
        if not result.drafts and not result.words:
            raise PipelineError(errors.NO_SEGMENTS, "Scribe produced no speech words")
        logger.info(
            "Scribe transcribed %d words → %d segments (diarize=%s)",
            len(result.words),
            len(result.drafts),
            diarize,
        )
        return result
