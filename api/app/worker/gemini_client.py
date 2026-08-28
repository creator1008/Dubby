"""Gemini 3.7 Flash: full-document STT, timestamps, speakers, spoken translation.

Uses the Google AI Studio REST API (generativelanguage.googleapis.com).
Call order for Ver 3.0:

1. Listen to the whole audio once → complete source transcript + timed segments.
2. Translate that document as a whole, then assign spoken lines per timestamp.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings
from ..languages import LANGUAGE_NAMES
from . import errors
from .errors import PipelineError
from .locale_rules import (
    apply_translation_postprocess,
    spoken_char_budget,
    translation_pair_rules,
)
from .openai_client import SegmentDraft, TranscribeResult, parse_translation_content

logger = logging.getLogger("dubby.worker.gemini")

_INLINE_MAX_BYTES = 15 * 1024 * 1024
_GENERATE_TIMEOUT = httpx.Timeout(connect=20.0, read=900.0, write=120.0, pool=10.0)
_UPLOAD_TIMEOUT = httpx.Timeout(connect=20.0, read=120.0, write=300.0, pool=10.0)

_TRANSCRIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "full_transcript": {"type": "string"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_sec": {"type": "number"},
                    "end_sec": {"type": "number"},
                    "speaker": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["start_sec", "end_sec", "speaker", "text"],
            },
        },
    },
    "required": ["full_transcript", "segments"],
}

_TRANSLATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "full_translation": {"type": "string"},
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "idx": {"type": "integer"},
                    "text": {"type": "string"},
                },
                "required": ["idx", "text"],
            },
        },
    },
    "required": ["full_translation", "translations"],
}

_CLOCK_RE = re.compile(
    r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)$"
)


def _raise_for_status(resp: httpx.Response, code: str) -> None:
    if resp.status_code < 400:
        return
    retryable = resp.status_code == 429 or resp.status_code >= 500
    raise PipelineError(
        code,
        f"Gemini API returned {resp.status_code}: {resp.text[:400]}",
        retryable=retryable,
    )


def parse_clock_or_number_to_ms(value: object, *, duration_ms: int) -> int:
    """Accept seconds, milliseconds, or mm:ss / hh:mm:ss clocks."""
    if value is None:
        return 0
    if isinstance(value, str):
        raw = value.strip()
        clock = _CLOCK_RE.match(raw)
        if clock:
            hours = int(clock.group(1) or 0)
            minutes = int(clock.group(2))
            seconds = float(clock.group(3))
            return max(0, int(round(((hours * 60 + minutes) * 60 + seconds) * 1000)))
        try:
            value = float(raw)
        except ValueError:
            return 0
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    duration_s = max(0.001, duration_ms / 1000.0)
    if number > duration_s + 1.5:
        return max(0, int(round(number)))
    return max(0, int(round(number * 1000)))


def normalize_transcript_segments(
    payload: dict[str, Any],
    *,
    duration_ms: int,
) -> tuple[str, list[SegmentDraft]]:
    """JSON from Gemini → full transcript + non-empty timed drafts."""
    full = str(payload.get("full_transcript") or "").strip()
    raw_segments = payload.get("segments")
    if not isinstance(raw_segments, list):
        raw_segments = []

    speaker_map: dict[str, str] = {}
    drafts: list[SegmentDraft] = []
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        start_raw = item.get("start_sec", item.get("start_ms", item.get("start")))
        end_raw = item.get("end_sec", item.get("end_ms", item.get("end")))
        start_ms = parse_clock_or_number_to_ms(start_raw, duration_ms=duration_ms)
        end_ms = parse_clock_or_number_to_ms(end_raw, duration_ms=duration_ms)
        if duration_ms > 0:
            start_ms = min(start_ms, duration_ms)
            end_ms = min(end_ms, duration_ms)
        if end_ms <= start_ms:
            end_ms = min(duration_ms or start_ms + 400, start_ms + 400)
        if end_ms <= start_ms:
            continue
        speaker_raw = str(item.get("speaker") or "A").strip() or "A"
        if speaker_raw not in speaker_map:
            speaker_map[speaker_raw] = f"speaker_{len(speaker_map) + 1}"
        drafts.append(
            SegmentDraft(
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
                speaker_id=speaker_map[speaker_raw],
            )
        )

    drafts.sort(key=lambda d: (d.start_ms, d.end_ms))
    # Snap tiny overlaps so mix/TTS slots stay ordered.
    cleaned: list[SegmentDraft] = []
    for draft in drafts:
        if cleaned and draft.start_ms < cleaned[-1].end_ms:
            prev = cleaned[-1]
            mid = (prev.end_ms + draft.start_ms) // 2
            if mid > prev.start_ms:
                cleaned[-1] = SegmentDraft(
                    start_ms=prev.start_ms,
                    end_ms=mid,
                    text=prev.text,
                    speaker_id=prev.speaker_id,
                )
                start_ms = max(draft.start_ms, mid)
            else:
                start_ms = draft.start_ms
            if draft.end_ms <= start_ms:
                continue
            draft = SegmentDraft(
                start_ms=start_ms,
                end_ms=draft.end_ms,
                text=draft.text,
                speaker_id=draft.speaker_id,
            )
        cleaned.append(draft)

    if not full and cleaned:
        full = " ".join(d.text for d in cleaned).strip()
    return full, cleaned


def extract_gemini_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise PipelineError(
            errors.ASR_FAILED,
            "Gemini returned no candidates",
            retryable=True,
        )
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        raise PipelineError(
            errors.ASR_FAILED,
            "Gemini response had no text parts",
            retryable=True,
        )
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict) and part.get("text"):
            chunks.append(str(part["text"]))
    text = "".join(chunks).strip()
    if not text:
        finish = candidates[0].get("finishReason") if isinstance(candidates[0], dict) else ""
        raise PipelineError(
            errors.ASR_FAILED,
            f"Gemini returned empty text (finish={finish})",
            retryable=True,
        )
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _audio_mime(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".webm": "audio/webm",
    }.get(suffix, "audio/mpeg")


class GeminiClient:
    def __init__(self, settings: Settings) -> None:
        key = (settings.gemini_api_key or "").strip()
        if not key:
            raise PipelineError(
                errors.CONFIG_MISSING, "GEMINI_API_KEY is not configured"
            )
        self._settings = settings
        self._key = key
        self._base = (settings.gemini_base_url or "").rstrip("/")
        self._model = (settings.gemini_model or "gemini-3.7-flash").strip()
        self._headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
        self._client = httpx.AsyncClient(timeout=_GENERATE_TIMEOUT)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def transcribe(
        self,
        asr_audio_path: str,
        language: str,
        *,
        duration_seconds: float | None = None,
        diarize: bool = True,
    ) -> TranscribeResult:
        duration_ms = max(1, int(round((duration_seconds or 0) * 1000)))
        if duration_ms <= 1:
            duration_ms = 24 * 60 * 60 * 1000
        lang = LANGUAGE_NAMES.get(language, language)
        speaker_rule = (
            "Assign a stable speaker label (A, B, C, …) so the same person keeps "
            "the same letter across the whole clip. Split when the speaker changes."
            if diarize
            else "This is treated as a single narrator: set speaker to A on every segment."
        )
        prompt = (
            f"You are a professional transcriber for video dubbing.\n"
            f"Listen to the ENTIRE audio. The spoken language is {lang}.\n"
            "Transcribe ALL speech verbatim with correct spelling and diacritics. "
            "Keep greetings, closings, and speech under background music. "
            "Do not invent words. Do not omit the second half of the clip.\n"
            f"{speaker_rule}\n"
            "Return JSON with:\n"
            "- full_transcript: the complete source transcript as one coherent document "
            "(no timestamps).\n"
            "- segments: utterance captions covering every spoken word, typically 2–12 "
            "seconds, split on sentence or speaker change.\n"
            "  start_sec / end_sec are seconds from the start of this audio file "
            "(use audio timestamps; include milliseconds as decimals).\n"
            "  text is exactly what was spoken in that range.\n"
            "The concatenation of segment texts must cover the same words as "
            "full_transcript. Sort segments by start_sec. start_sec < end_sec."
        )
        payload = await self._generate_json(
            prompt,
            schema=_TRANSCRIPT_SCHEMA,
            audio_path=asr_audio_path,
            audio_timestamp=True,
            error_code=errors.ASR_FAILED,
        )
        full, drafts = normalize_transcript_segments(payload, duration_ms=duration_ms)
        if not drafts:
            raise PipelineError(errors.NO_SEGMENTS, "Gemini produced no speech segments")
        if not full:
            full = " ".join(d.text for d in drafts)
        return TranscribeResult(
            drafts=drafts,
            speech_ranges=[(d.start_ms, d.end_ms) for d in drafts],
            words=[],
            full_transcript=full,
            speakers_labeled=True,
            skip_proofread=True,
            provider="gemini",
        )

    async def translate_batch(
        self,
        items: list[tuple[int, str, float]],
        source_lang: str,
        target_lang: str,
        *,
        document_context: str | None = None,
    ) -> dict[int, str]:
        if not items:
            return {}
        if source_lang == target_lang:
            return {idx: text for idx, text, _ in items}

        src = LANGUAGE_NAMES.get(source_lang, source_lang)
        tgt = LANGUAGE_NAMES.get(target_lang, target_lang)
        full_source = (document_context or "").strip() or "\n".join(
            f"[{idx}] {text.strip()}" for idx, text, _ in items if text.strip()
        )
        extra = translation_pair_rules(source_lang, target_lang)
        segments_payload = []
        for idx, text, seconds in items:
            duration = max(0.35, float(seconds))
            segments_payload.append(
                {
                    "idx": idx,
                    "source_text": text,
                    "target_seconds": round(duration, 2),
                    "max_chars": spoken_char_budget(target_lang, duration),
                }
            )
        prompt = (
            f"You are a professional dubbing translator.\n"
            f"First, translate the COMPLETE {src} transcript into natural spoken {tgt} "
            "as one document. Preserve meaning, names, tone, and genre terms. "
            "Use colloquial voice-over language, not stiff subtitle-ese. "
            "Do not add narrator notes.\n"
            "Then assign each numbered segment a spoken line taken from that document "
            "translation:\n"
            "- keep idx order; never merge, split, drop, or reorder idxs\n"
            "- do not borrow words from neighboring idxs\n"
            "- every clause in the full translation must land on some idx "
            "(no dropped ending)\n"
            "- fit target_seconds / max_chars by tightening wording, not by deleting meaning\n"
            "- spell numbers and abbreviations as they should be spoken\n"
            f"{extra}\n"
            "Return JSON with full_translation plus translations[{idx,text}]."
        )
        user = json.dumps(
            {
                "full_transcript": full_source,
                "segments": segments_payload,
            },
            ensure_ascii=False,
        )
        payload = await self._generate_json(
            f"{prompt}\n\n{user}",
            schema=_TRANSLATION_SCHEMA,
            audio_path=None,
            audio_timestamp=False,
            error_code=errors.TRANSLATION_FAILED,
        )
        wanted = [idx for idx, _, _ in items]
        # parse_translation_content expects {"translations":[...]} which we have.
        parsed = parse_translation_content(json.dumps(payload, ensure_ascii=False), wanted)
        source_by_idx = {idx: text for idx, text, _ in items}
        return {
            idx: apply_translation_postprocess(
                source_by_idx.get(idx, ""),
                text,
                source_lang,
                target_lang,
            )
            for idx, text in parsed.items()
        }

    async def _generate_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        audio_path: str | None,
        audio_timestamp: bool,
        error_code: str,
    ) -> dict[str, Any]:
        file_name: str | None = None
        try:
            parts: list[dict[str, Any]] = []
            if audio_path:
                audio_part, file_name = await self._audio_part(audio_path)
                parts.append(audio_part)
            parts.append({"text": prompt})
            generation: dict[str, Any] = {
                "temperature": 0.1,
                "responseMimeType": "application/json",
                "responseSchema": schema,
                "thinkingConfig": {"thinkingLevel": "LOW"},
            }
            if audio_timestamp:
                generation["audioTimestamp"] = True
            body = {
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": generation,
            }
            url = f"{self._base}/models/{self._model}:generateContent"
            try:
                resp = await self._client.post(url, headers=self._headers, json=body)
            except httpx.HTTPError as exc:
                raise PipelineError(
                    error_code, f"Gemini request failed: {exc}", retryable=True
                ) from exc
            if resp.status_code == 400 and "thinking" in (resp.text or "").lower():
                generation.pop("thinkingConfig", None)
                body["generationConfig"] = generation
                resp = await self._client.post(url, headers=self._headers, json=body)
            _raise_for_status(resp, error_code)
            try:
                raw = extract_gemini_text(resp.json())
                parsed = json.loads(raw)
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                raise PipelineError(
                    error_code,
                    "Gemini returned invalid JSON",
                    retryable=True,
                ) from exc
            if not isinstance(parsed, dict):
                raise PipelineError(
                    error_code, "Gemini JSON was not an object", retryable=True
                )
            return parsed
        finally:
            if file_name:
                await self._delete_file(file_name)

    async def _audio_part(self, path: str) -> tuple[dict[str, Any], str | None]:
        data = Path(path).read_bytes()
        mime = _audio_mime(path)
        if len(data) <= _INLINE_MAX_BYTES:
            encoded = base64.b64encode(data).decode("ascii")
            return {"inline_data": {"mime_type": mime, "data": encoded}}, None
        file_uri = await self._upload_file(path, data, mime)
        return {"file_data": {"file_uri": file_uri, "mime_type": mime}}, file_uri

    async def _upload_file(self, path: str, data: bytes, mime: str) -> str:
        """Resumable upload to the Gemini Files API; wait until ACTIVE."""
        display = Path(path).name
        start_url = (
            f"{self._base[: -len('/v1beta')]}/upload/v1beta/files"
            if self._base.endswith("/v1beta")
            else f"{self._base}/upload/v1beta/files"
        )
        try:
            start = await self._client.post(
                start_url,
                headers={
                    "x-goog-api-key": self._key,
                    "X-Goog-Upload-Protocol": "resumable",
                    "X-Goog-Upload-Command": "start",
                    "X-Goog-Upload-Header-Content-Length": str(len(data)),
                    "X-Goog-Upload-Header-Content-Type": mime,
                    "Content-Type": "application/json",
                },
                json={"file": {"display_name": display}},
                timeout=_UPLOAD_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise PipelineError(
                errors.ASR_FAILED, f"Gemini file upload start failed: {exc}", retryable=True
            ) from exc
        _raise_for_status(start, errors.ASR_FAILED)
        upload_url = start.headers.get("x-goog-upload-url") or start.headers.get(
            "X-Goog-Upload-URL"
        )
        if not upload_url:
            raise PipelineError(
                errors.ASR_FAILED, "Gemini file upload URL missing", retryable=True
            )
        try:
            finish = await self._client.post(
                upload_url,
                headers={
                    "x-goog-api-key": self._key,
                    "Content-Length": str(len(data)),
                    "X-Goog-Upload-Offset": "0",
                    "X-Goog-Upload-Command": "upload, finalize",
                },
                content=data,
                timeout=_UPLOAD_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise PipelineError(
                errors.ASR_FAILED, f"Gemini file upload failed: {exc}", retryable=True
            ) from exc
        _raise_for_status(finish, errors.ASR_FAILED)
        meta = finish.json() if finish.content else {}
        file_obj = meta.get("file") if isinstance(meta, dict) else None
        if not isinstance(file_obj, dict):
            file_obj = meta if isinstance(meta, dict) else {}
        name = str(file_obj.get("name") or "").strip()
        uri = str(file_obj.get("uri") or "").strip()
        if not uri and name:
            uri = f"{self._base}/{name}" if not name.startswith("http") else name
        if not name:
            raise PipelineError(
                errors.ASR_FAILED, "Gemini file upload returned no name", retryable=True
            )
        await self._wait_file_active(name)
        return uri or name

    async def _wait_file_active(self, name: str) -> None:
        file_id = name if name.startswith("files/") else f"files/{name}"
        url = f"{self._base}/{file_id}"
        for _ in range(30):
            try:
                resp = await self._client.get(url, headers=self._headers)
            except httpx.HTTPError:
                await asyncio.sleep(1.0)
                continue
            if resp.status_code >= 400:
                await asyncio.sleep(1.0)
                continue
            body = resp.json() if resp.content else {}
            state = str(body.get("state") or "").upper()
            if state in {"ACTIVE", "STATE_ACTIVE", ""}:
                return
            if state in {"FAILED", "STATE_FAILED"}:
                raise PipelineError(
                    errors.ASR_FAILED, "Gemini file processing failed", retryable=True
                )
            await asyncio.sleep(1.0)
        raise PipelineError(
            errors.ASR_FAILED, "Gemini file was not ready in time", retryable=True
        )

    async def _delete_file(self, file_uri: str) -> None:
        name = file_uri.strip()
        if "/files/" in name:
            name = "files/" + name.rsplit("/files/", 1)[-1]
        elif not name.startswith("files/"):
            return
        try:
            await self._client.delete(f"{self._base}/{name}", headers=self._headers)
        except httpx.HTTPError:
            logger.warning("Gemini file delete failed for %s", name)
