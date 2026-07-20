"""OpenAI integrations: Whisper ASR and GPT structured translation.

Thin httpx wrappers plus pure parsing helpers (the parsers are unit-tested
without network access). Transient failures raise retryable
:class:`PipelineError`; the orchestrator owns the retry loop.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import Settings
from . import errors
from .errors import PipelineError

logger = logging.getLogger("dubby.worker.openai")

LANGUAGE_NAMES = {"en": "English", "ko": "Korean", "vi": "Vietnamese"}

_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=10.0)


def _raise_for_status(resp: httpx.Response, code: str) -> None:
    if resp.status_code < 400:
        return
    retryable = resp.status_code == 429 or resp.status_code >= 500
    raise PipelineError(
        code,
        f"OpenAI API returned {resp.status_code}: {resp.text[:300]}",
        retryable=retryable,
    )


@dataclass(frozen=True)
class SegmentDraft:
    start_ms: int
    end_ms: int
    text: str


# --- parsing (pure) -------------------------------------------------------------


def parse_whisper_segments(payload: dict) -> list[SegmentDraft]:
    """verbose_json response -> ordered, non-empty segment drafts.

    Guarantees the DB constraints: start_ms >= 0 and end_ms > start_ms.
    """
    drafts: list[SegmentDraft] = []
    for seg in payload.get("segments") or []:
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        start_ms = max(0, int(round(float(seg.get("start", 0.0)) * 1000)))
        end_ms = int(round(float(seg.get("end", 0.0)) * 1000))
        if end_ms <= start_ms:
            end_ms = start_ms + 1
        drafts.append(SegmentDraft(start_ms=start_ms, end_ms=end_ms, text=text))
    return drafts


def parse_translation_content(content: str, expected_idxs: list[int]) -> dict[int, str]:
    """Model JSON -> {idx: translated text}; every expected idx must appear."""
    try:
        data = json.loads(content)
        items = data["translations"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PipelineError(
            errors.TRANSLATION_FAILED,
            "translation response was not the expected JSON shape",
            retryable=True,
        ) from exc

    result: dict[int, str] = {}
    for item in items:
        try:
            result[int(item["idx"])] = str(item["text"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PipelineError(
                errors.TRANSLATION_FAILED,
                "translation item missing idx/text",
                retryable=True,
            ) from exc

    missing = [i for i in expected_idxs if i not in result]
    if missing:
        raise PipelineError(
            errors.TRANSLATION_FAILED,
            f"translation missing segments {missing[:10]}",
            retryable=True,
        )
    return result


def build_translation_messages(
    items: list[tuple[int, str, float]], source_lang: str, target_lang: str
) -> list[dict]:
    src = LANGUAGE_NAMES.get(source_lang, source_lang)
    tgt = LANGUAGE_NAMES.get(target_lang, target_lang)
    system = (
        "You translate dubbing subtitles. Translate each numbered segment "
        f"from {src} to {tgt}. Rules: keep the meaning and tone; keep the "
        "translation concise enough to be spoken in the supplied duration; "
        "write natural native spoken language for voice-over; retain all "
        "required diacritics; spell numbers and abbreviations as they should "
        "be spoken; never merge, split, drop, or reorder segments; return one "
        "translation per idx."
    )
    user = json.dumps(
        {
            "segments": [
                {"idx": idx, "text": text, "target_seconds": round(seconds, 2)}
                for idx, text, seconds in items
            ]
        },
        ensure_ascii=False,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


_TRANSLATION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "segment_translations",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "translations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "idx": {"type": "integer"},
                            "text": {"type": "string"},
                        },
                        "required": ["idx", "text"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["translations"],
            "additionalProperties": False,
        },
    },
}


# --- API calls -------------------------------------------------------------------


class OpenAIClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise PipelineError(
                errors.CONFIG_MISSING, "OPENAI_API_KEY is not configured"
            )
        self._settings = settings
        self._base = settings.openai_base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {settings.openai_api_key}"}

    async def transcribe(self, audio_path: str, language: str) -> list[SegmentDraft]:
        data = {
            "model": self._settings.whisper_model,
            "language": language,
            "response_format": "verbose_json",
        }
        file_bytes = Path(audio_path).read_bytes()
        files = {"file": (Path(audio_path).name, file_bytes, "audio/mpeg")}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._base}/audio/transcriptions",
                    headers=self._headers,
                    data=data,
                    files=files,
                )
        except httpx.HTTPError as exc:
            raise PipelineError(
                errors.ASR_FAILED, f"Whisper request failed: {exc}", retryable=True
            ) from exc
        _raise_for_status(resp, errors.ASR_FAILED)
        return parse_whisper_segments(resp.json())

    async def translate_batch(
        self,
        items: list[tuple[int, str, float]],
        source_lang: str,
        target_lang: str,
    ) -> dict[int, str]:
        body = {
            "model": self._settings.translation_model,
            "messages": build_translation_messages(items, source_lang, target_lang),
            "response_format": _TRANSLATION_SCHEMA,
            "temperature": 0.2,
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._base}/chat/completions",
                    headers={**self._headers, "Content-Type": "application/json"},
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise PipelineError(
                errors.TRANSLATION_FAILED,
                f"translation request failed: {exc}",
                retryable=True,
            ) from exc
        _raise_for_status(resp, errors.TRANSLATION_FAILED)
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise PipelineError(
                errors.TRANSLATION_FAILED,
                "unexpected chat completion shape",
                retryable=True,
            ) from exc
        return parse_translation_content(content, [idx for idx, _, _ in items])

    async def adjust_translation(
        self, text: str, target_lang: str, target_seconds: float, direction: str
    ) -> str:
        """Compress or expand one line while preserving meaning and tone."""
        instruction = (
            f"{direction.capitalize()} this {LANGUAGE_NAMES.get(target_lang, target_lang)} "
            f"dubbing line to speak naturally in about {target_seconds:.2f} seconds. "
            "Preserve meaning, names, numbers, and emotional tone. Return only the line."
        )
        body = {
            "model": self._settings.translation_model,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": text},
            ],
            "temperature": 0.2,
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._base}/chat/completions",
                    headers={**self._headers, "Content-Type": "application/json"},
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise PipelineError(
                errors.TRANSLATION_FAILED,
                f"timing rewrite failed: {exc}",
                retryable=True,
            ) from exc
        _raise_for_status(resp, errors.TRANSLATION_FAILED)
        try:
            result = str(resp.json()["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise PipelineError(
                errors.TRANSLATION_FAILED, "unexpected timing rewrite response", retryable=True
            ) from exc
        if not result:
            raise PipelineError(errors.TRANSLATION_FAILED, "timing rewrite was empty")
        return result
