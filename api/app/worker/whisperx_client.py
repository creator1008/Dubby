"""Local WhisperX fallback when ElevenLabs Scribe cannot run.

This is not a cloud API. It only runs if the optional ``whisperx`` package is
installed on the worker. Production Lightsail typically has no GPU, so the
engine tries Scribe first and uses WhisperX only when Scribe fails *and*
WhisperX is importable. Otherwise Gemini/OpenAI STT is used.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from . import errors
from .errors import PipelineError
from .openai_client import SegmentDraft, TranscribeResult
from .utterance_pipeline import TimedToken

logger = logging.getLogger("dubby.worker.whisperx")


def whisperx_available() -> bool:
    try:
        import whisperx  # noqa: F401

        return True
    except Exception:
        return False


def _segments_from_whisperx(payload: dict[str, Any]) -> tuple[list[SegmentDraft], list[TimedToken]]:
    drafts: list[SegmentDraft] = []
    tokens: list[TimedToken] = []
    for item in payload.get("segments") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        try:
            start_ms = max(0, int(round(float(item.get("start") or 0) * 1000)))
            end_ms = max(start_ms + 1, int(round(float(item.get("end") or 0) * 1000)))
        except (TypeError, ValueError):
            continue
        speaker = str(item.get("speaker") or item.get("speaker_id") or "").strip()
        if text and end_ms > start_ms:
            drafts.append(
                SegmentDraft(
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=text,
                    speaker_id=speaker or "speaker_1",
                )
            )
        for word in item.get("words") or []:
            if not isinstance(word, dict):
                continue
            wtext = str(word.get("word") or word.get("text") or "").strip()
            if not wtext:
                continue
            try:
                wstart = max(0, int(round(float(word.get("start") or 0) * 1000)))
                wend = max(wstart + 1, int(round(float(word.get("end") or 0) * 1000)))
            except (TypeError, ValueError):
                continue
            tokens.append(
                TimedToken(
                    text=wtext,
                    start_ms=wstart,
                    end_ms=wend,
                    speaker_id=str(word.get("speaker") or speaker or "").strip() or None,
                )
            )
    return drafts, tokens


def _transcribe_whisperx_sync(audio_path: str, language: str) -> TranscribeResult:
    try:
        import whisperx
    except Exception as exc:  # pragma: no cover - optional dependency
        raise PipelineError(
            errors.ASR_FAILED,
            f"WhisperX is not installed: {exc}",
            retryable=False,
        ) from exc

    lang = (language or "").strip().lower().split("-", 1)[0] or None
    device = "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            device = "cuda"
    except Exception:
        device = "cpu"

    try:
        model = whisperx.load_model("large-v2", device, compute_type="int8")
        result = model.transcribe(audio_path, language=lang, batch_size=8)
        detected = str(result.get("language") or lang or "").strip() or None
        align_model, metadata = whisperx.load_align_model(
            language_code=detected, device=device
        )
        aligned = whisperx.align(
            result.get("segments") or [],
            align_model,
            metadata,
            audio_path,
            device,
            return_char_alignments=False,
        )
        payload = aligned if isinstance(aligned, dict) else {"segments": aligned}
        drafts, tokens = _segments_from_whisperx(payload)
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError(
            errors.ASR_FAILED, f"WhisperX transcription failed: {exc}", retryable=True
        ) from exc

    if not drafts:
        raise PipelineError(errors.NO_SEGMENTS, "WhisperX produced no speech segments")
    unique = {str(d.speaker_id or "").strip() for d in drafts if str(d.speaker_id or "").strip()}
    return TranscribeResult(
        drafts=drafts,
        speech_ranges=[(d.start_ms, d.end_ms) for d in drafts],
        words=tokens,
        full_transcript=" ".join(d.text for d in drafts),
        speakers_labeled=len(unique) >= 2,
        skip_proofread=True,
        provider="whisperx",
    )


async def transcribe_whisperx(audio_path: str, language: str) -> TranscribeResult:
    return await asyncio.to_thread(_transcribe_whisperx_sync, audio_path, language)
