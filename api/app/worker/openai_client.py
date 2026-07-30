"""OpenAI integrations: Whisper ASR and GPT structured translation.

Thin httpx wrappers plus pure parsing helpers (the parsers are unit-tested
without network access). Transient failures raise retryable
:class:`PipelineError`; the orchestrator owns the retry loop.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..config import Settings
from ..languages import LANGUAGE_NAMES
from . import errors
from .asr_quality import parse_whisper_words, refine_whisper_drafts
from .errors import PipelineError
from .utterance_pipeline import TimedToken

logger = logging.getLogger("dubby.worker.openai")

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


@dataclass(frozen=True)
class TranscribeResult:
    drafts: list[SegmentDraft]
    speech_ranges: list[tuple[int, int]]
    words: list[TimedToken] = field(default_factory=list)


# --- parsing (pure) -------------------------------------------------------------


def parse_whisper_segments(payload: dict) -> list[SegmentDraft]:
    """verbose_json response -> filtered, non-overlapping segment drafts.

    Applies local_step12 hallucination filters and long-segment regrouping.
    Guarantees start_ms >= 0 and end_ms > start_ms.
    """
    return [
        SegmentDraft(start_ms=start, end_ms=end, text=text)
        for start, end, text in refine_whisper_drafts(payload)
    ]


def parse_whisper_word_ranges(payload: dict) -> list[tuple[int, int]]:
    """Word timestamps -> merged voiced ranges (tight gaps only)."""
    from .media import merge_speech_ranges

    ranges = [(word.start_ms, word.end_ms) for word in parse_whisper_words(payload)]
    merged = merge_speech_ranges(ranges, max_gap_ms=120)
    if merged:
        return merged
    return [
        (draft.start_ms, draft.end_ms)
        for draft in parse_whisper_segments(payload)
        if draft.end_ms > draft.start_ms
    ]


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
    items: list[tuple[int, str, float]],
    source_lang: str,
    target_lang: str,
    *,
    document_context: str | None = None,
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
        "translation per idx. Use the full transcript context so pronouns, "
        "names, tense, and register stay consistent across segments — but still "
        "translate each idx independently without borrowing words from neighbors."
    )
    payload: dict[str, object] = {
        "segments": [
            {"idx": idx, "text": text, "target_seconds": round(seconds, 2)}
            for idx, text, seconds in items
        ]
    }
    if document_context and document_context.strip():
        payload["full_transcript"] = document_context.strip()
    user = json.dumps(payload, ensure_ascii=False)
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

    async def transcribe(self, audio_path: str, language: str) -> TranscribeResult:
        data = {
            "model": self._settings.whisper_model,
            "language": language,
            "response_format": "verbose_json",
            "timestamp_granularities[]": ["word", "segment"],
            # Deterministic decoding — matches local_step12 quality path.
            "temperature": "0",
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
        payload = resp.json()
        return TranscribeResult(
            drafts=parse_whisper_segments(payload),
            speech_ranges=parse_whisper_word_ranges(payload),
            words=parse_whisper_words(payload),
        )

    async def translate_batch(
        self,
        items: list[tuple[int, str, float]],
        source_lang: str,
        target_lang: str,
        *,
        document_context: str | None = None,
    ) -> dict[int, str]:
        body = {
            "model": self._settings.translation_model,
            "messages": build_translation_messages(
                items,
                source_lang,
                target_lang,
                document_context=document_context,
            ),
            "response_format": _TRANSLATION_SCHEMA,
            "temperature": 0.1,
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

    async def correct_transcript(
        self,
        items: list[tuple[int, str]],
        language: str,
    ) -> dict[int, str]:
        """Context-aware ASR proofreading; returns corrected text per idx."""
        if not items:
            return {}
        lang = LANGUAGE_NAMES.get(language, language)
        body = {
            "model": self._settings.translation_model,
            "temperature": 0,
            "response_format": _TRANSLATION_SCHEMA,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"You proofread {lang} ASR (speech-to-text) subtitles for "
                        "dubbing. Fix clear recognition errors using full context "
                        "(wrong homophones, truncated words, nonsense). Remove "
                        "duplicated phrases that were repeated across neighboring "
                        "idxs. Do not rewrite style or add new meaning. Keep each "
                        "idx as its own subtitle line. Return JSON "
                        '{"translations":[{"idx":0,"text":"..."}]} — use the same '
                        "idxs; field name is translations but values are corrected "
                        "source lines."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "full_transcript": "\n".join(
                                f"[{idx}] {text}" for idx, text in items
                            ),
                            "segments": [
                                {"idx": idx, "text": text} for idx, text in items
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
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
                f"ASR correction failed: {exc}",
                retryable=True,
            ) from exc
        _raise_for_status(resp, errors.TRANSLATION_FAILED)
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise PipelineError(
                errors.TRANSLATION_FAILED,
                "unexpected ASR-correction response shape",
                retryable=True,
            ) from exc
        return parse_translation_content(content, [idx for idx, _ in items])

    async def translate_document(
        self,
        source_text: str,
        source_lang: str,
        target_lang: str,
    ) -> str:
        """Translate the full corrected transcript as one coherent dubbing script."""
        src = LANGUAGE_NAMES.get(source_lang, source_lang)
        tgt = LANGUAGE_NAMES.get(target_lang, target_lang)
        cleaned = (source_text or "").strip()
        if not cleaned:
            return ""
        if source_lang == target_lang:
            return cleaned
        schema = {
            "type": "json_schema",
            "json_schema": {
                "name": "document_translation",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"translation": {"type": "string"}},
                    "required": ["translation"],
                    "additionalProperties": False,
                },
            },
        }
        body = {
            "model": self._settings.translation_model,
            "temperature": 0.1,
            "response_format": schema,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"Translate this complete {src} transcript into natural "
                        f"spoken {tgt} for voice-over dubbing. Preserve narrative "
                        "flow, character names, tone, and pronouns consistently. "
                        "Do not add narrator notes or timestamps. Return JSON "
                        '{"translation":"..."}.'
                    ),
                },
                {"role": "user", "content": cleaned},
            ],
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
                f"document translation failed: {exc}",
                retryable=True,
            ) from exc
        _raise_for_status(resp, errors.TRANSLATION_FAILED)
        try:
            content = resp.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
            text = str(data["translation"]).strip()
        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise PipelineError(
                errors.TRANSLATION_FAILED,
                "unexpected document-translation response shape",
                retryable=True,
            ) from exc
        if not text:
            raise PipelineError(
                errors.TRANSLATION_FAILED,
                "document translation was empty",
                retryable=True,
            )
        return text

    async def align_translation_to_segments(
        self,
        items: list[tuple[int, str]],
        document_translation: str,
        source_lang: str,
        target_lang: str,
    ) -> dict[int, str]:
        """Split a full-document translation onto source idxs without bleed."""
        if not items:
            return {}
        src = LANGUAGE_NAMES.get(source_lang, source_lang)
        tgt = LANGUAGE_NAMES.get(target_lang, target_lang)
        if source_lang == target_lang:
            return {idx: text for idx, text in items}
        body = {
            "model": self._settings.translation_model,
            "temperature": 0,
            "response_format": _TRANSLATION_SCHEMA,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"You align a complete {tgt} dubbing translation onto "
                        f"numbered {src} source segments. Rules: each idx gets "
                        "ONLY the meaning of that source line; never continue a "
                        "previous sentence into the next idx; never borrow words "
                        "from neighbors; keep natural spoken phrasing; cover the "
                        "whole document translation without dropping content. "
                        "Return JSON "
                        '{"translations":[{"idx":0,"text":"..."}]}.'
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "full_translation": document_translation,
                            "segments": [
                                {"idx": idx, "source_text": text}
                                for idx, text in items
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
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
                f"translation alignment failed: {exc}",
                retryable=True,
            ) from exc
        _raise_for_status(resp, errors.TRANSLATION_FAILED)
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise PipelineError(
                errors.TRANSLATION_FAILED,
                "unexpected alignment response shape",
                retryable=True,
            ) from exc
        return parse_translation_content(content, [idx for idx, _ in items])

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
            "temperature": 0.1,
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
            return str(resp.json()["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise PipelineError(
                errors.TRANSLATION_FAILED,
                "unexpected timing-rewrite response shape",
                retryable=True,
            ) from exc
