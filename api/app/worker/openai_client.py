"""ASR (OpenAI Whisper) and structured translation (xAI Grok / OpenAI chat).

Thin httpx wrappers plus pure parsing helpers (the parsers are unit-tested
without network access). Transient failures raise retryable
:class:`PipelineError`; the orchestrator owns the retry loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

import httpx

from ..config import Settings
from ..languages import LANGUAGE_NAMES
from . import errors
from .asr_quality import parse_whisper_words, refine_whisper_drafts
from .errors import PipelineError
from .utterance_pipeline import TimedToken

logger = logging.getLogger("dubby.worker.openai")

_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=10.0)
_CHAT_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=60.0, pool=10.0)
T = TypeVar("T")


def _raise_for_status(resp: httpx.Response, code: str) -> None:
    if resp.status_code < 400:
        return
    retryable = resp.status_code == 429 or resp.status_code >= 500
    snippet = resp.text[:300]
    lowered = snippet.lower()
    if "incorrect api key" in lowered or "invalid api key" in lowered:
        raise PipelineError(
            code,
            "XAI_API_KEY was rejected by xAI. Put a real Grok key from "
            "https://console.x.ai into infra/.env (starts with xai-, not an "
            "OpenAI sk- key), then recreate api and worker.",
            retryable=False,
        )
    raise PipelineError(
        code,
        f"LLM API returned {resp.status_code}: {snippet}",
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


def sanitize_secret(value: str) -> str:
    """Strip whitespace, wrapping quotes, and an accidental ``Bearer `` prefix."""
    text = (value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    return text


def whisper_transcription_form(
    model: str,
    language: str | None,
    *,
    prompt: str | None = None,
) -> list[tuple[str, str]]:
    """Form fields so Whisper actually returns word timestamps.

    A Python list under one ``timestamp_granularities[]`` key is sent as the
    string ``"['word', 'segment']"`` and OpenAI then omits ``words``.
    """
    fields: list[tuple[str, str]] = [
        ("model", model),
        ("response_format", "verbose_json"),
        ("timestamp_granularities[]", "word"),
        ("timestamp_granularities[]", "segment"),
        ("temperature", "0"),
    ]
    if language:
        fields.append(("language", str(language)))
    if prompt:
        fields.append(("prompt", prompt))
    return fields


def whisper_multipart_files(
    model: str,
    language: str | None,
    *,
    file: tuple[str, object, str],
    prompt: str | None = None,
) -> list[tuple[str, object]]:
    """httpx ``files=`` parts for Whisper (duplicate keys + audio).

    Do not pass :func:`whisper_transcription_form` as ``data=``. httpx 0.28
    treats a list of tuples as a byte iterator and raises
    ``Attempted to send an sync request with an AsyncClient instance``.
    """
    parts: list[tuple[str, object]] = [
        (key, (None, value))
        for key, value in whisper_transcription_form(model, language, prompt=prompt)
    ]
    parts.append(("file", file))
    return parts


def is_grok_model(model: str) -> bool:
    """True for xAI Grok chat models (``grok-4.6``, ``grok-4.5``, …)."""
    normalized = (model or "").strip().lower().replace("_", "-")
    return normalized.startswith("grok-")


def chunked(items: list[T], size: int) -> list[list[T]]:
    """Split ``items`` into consecutive batches of at most ``size``."""
    n = max(1, int(size))
    return [items[i : i + n] for i in range(0, len(items), n)]


def chat_endpoint_for_model(
    model: str,
    *,
    openai_api_key: str,
    openai_base_url: str,
    xai_api_key: str,
    xai_base_url: str,
    reasoning_effort: str = "low",
) -> tuple[str, dict[str, str], dict[str, object]]:
    """Return ``(base_url, headers, extra_body)`` for chat completions."""
    if is_grok_model(model):
        key = sanitize_secret(xai_api_key)
        if not key:
            raise PipelineError(
                errors.CONFIG_MISSING, "XAI_API_KEY is not configured"
            )
        if key.startswith("sk-"):
            raise PipelineError(
                errors.CONFIG_MISSING,
                "XAI_API_KEY looks like an OpenAI key (sk-…). "
                "Create a Grok key at https://console.x.ai and set XAI_API_KEY "
                "to the value that starts with xai-",
            )
        extras: dict[str, object] = {}
        effort = (reasoning_effort or "").strip().lower()
        if effort:
            extras["reasoning_effort"] = effort
        return (
            (xai_base_url or "https://api.x.ai/v1").rstrip("/"),
            {"Authorization": f"Bearer {key}"},
            extras,
        )
    key = sanitize_secret(openai_api_key)
    if not key:
        raise PipelineError(
            errors.CONFIG_MISSING, "OPENAI_API_KEY is not configured"
        )
    return (
        (openai_base_url or "https://api.openai.com/v1").rstrip("/"),
        {"Authorization": f"Bearer {key}"},
        {},
    )


def coerce_model_json(content: str) -> str:
    """Strip markdown fences so Grok/GPT JSON payloads still parse."""
    text = (content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def assistant_message_text(payload: dict) -> str:
    """Read the assistant text from a chat-completions JSON body."""
    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise PipelineError(
            errors.TRANSLATION_FAILED,
            "unexpected chat completion shape",
            retryable=True,
        ) from exc
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if text:
                    parts.append(str(text))
        text = "".join(parts).strip()
    else:
        text = str(content or "").strip()
    if not text:
        refusal = (
            message.get("refusal") if isinstance(message, dict) else None
        )
        raise PipelineError(
            errors.TRANSLATION_FAILED,
            f"empty chat completion{f': {refusal}' if refusal else ''}",
            retryable=True,
        )
    return text


def parse_translation_content(content: str, expected_idxs: list[int]) -> dict[int, str]:
    """Model JSON -> {idx: translated text}; every expected idx must appear."""
    try:
        data = json.loads(coerce_model_json(content))
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
    from .locale_rules import translation_pair_rules

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
    extra = translation_pair_rules(source_lang, target_lang)
    if extra:
        system = f"{system}\n\n{extra}"
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
        if is_grok_model(settings.translation_model):
            self._chat_endpoint()

    def _chat_endpoint(self) -> tuple[str, dict[str, str], dict[str, object]]:
        return chat_endpoint_for_model(
            self._settings.translation_model,
            openai_api_key=self._settings.openai_api_key,
            openai_base_url=self._settings.openai_base_url,
            xai_api_key=self._settings.xai_api_key,
            xai_base_url=self._settings.xai_base_url,
            reasoning_effort=self._settings.translation_reasoning_effort,
        )

    async def _chat_completion(self, body: dict, fail_prefix: str) -> str:
        base, headers, extras = self._chat_endpoint()
        payload = {
            **body,
            "model": self._settings.translation_model,
            **extras,
        }
        try:
            async with httpx.AsyncClient(timeout=_CHAT_TIMEOUT) as client:
                resp = await client.post(
                    f"{base}/chat/completions",
                    headers={**headers, "Content-Type": "application/json"},
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise PipelineError(
                errors.TRANSLATION_FAILED,
                f"{fail_prefix}: {exc}",
                retryable=True,
            ) from exc
        _raise_for_status(resp, errors.TRANSLATION_FAILED)
        return assistant_message_text(resp.json())

    async def transcribe(self, audio_path: str, language: str) -> TranscribeResult:
        from .locale_rules import whisper_vocab_prompt

        vocab = whisper_vocab_prompt(language)
        file_bytes = Path(audio_path).read_bytes()
        files = whisper_multipart_files(
            self._settings.whisper_model,
            language,
            file=(Path(audio_path).name, file_bytes, "audio/mpeg"),
            prompt=vocab,
        )
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._base}/audio/transcriptions",
                    headers=self._headers,
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
        if not items:
            return {}
        parts = await asyncio.gather(
            *[
                self._translate_chunk(
                    chunk,
                    source_lang,
                    target_lang,
                    document_context=document_context,
                )
                for chunk in chunked(items, self._settings.translation_batch_size)
            ]
        )
        merged: dict[int, str] = {}
        for part in parts:
            merged.update(part)
        return merged

    async def _translate_chunk(
        self,
        items: list[tuple[int, str, float]],
        source_lang: str,
        target_lang: str,
        *,
        document_context: str | None = None,
    ) -> dict[int, str]:
        content = await self._chat_completion(
            {
                "messages": build_translation_messages(
                    items,
                    source_lang,
                    target_lang,
                    document_context=document_context,
                ),
                "response_format": _TRANSLATION_SCHEMA,
                "temperature": 0.1,
            },
            "translation request failed",
        )
        parsed = parse_translation_content(content, [idx for idx, _, _ in items])
        from .locale_rules import apply_translation_postprocess

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

    async def correct_transcript(
        self,
        items: list[tuple[int, str]],
        language: str,
    ) -> dict[int, str]:
        """Context-aware ASR proofreading; returns corrected text per idx."""
        if not items:
            return {}
        from .locale_rules import asr_proofread_rules

        lang = LANGUAGE_NAMES.get(language, language)
        proofread = (
            f"You proofread {lang} ASR (speech-to-text) subtitles for "
            "dubbing. Fix clear recognition errors using FULL narrative "
            "context across neighboring idxs: wrong near-homophones, "
            "truncated words, nonsense tokens. "
            "Remove duplicated phrases repeated across neighboring "
            "idxs by shortening the later line — never blank an idx "
            "and never drop a subtitle. Do not rewrite style or add new meaning. Keep each "
            "idx as its own subtitle line. Return JSON "
            '{"translations":[{"idx":0,"text":"..."}]} — use the same '
            "idxs; field name is translations but values are corrected "
            "source lines."
        )
        extra = asr_proofread_rules(language)
        if extra:
            proofread = f"{proofread}\n\n{extra}"
        full_transcript = "\n".join(f"[{idx}] {text}" for idx, text in items)

        async def _correct_chunk(chunk: list[tuple[int, str]]) -> dict[int, str]:
            content = await self._chat_completion(
                {
                    "temperature": 0,
                    "response_format": _TRANSLATION_SCHEMA,
                    "messages": [
                        {"role": "system", "content": proofread},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "full_transcript": full_transcript,
                                    "segments": [
                                        {"idx": idx, "text": text}
                                        for idx, text in chunk
                                    ],
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ],
                },
                "ASR correction failed",
            )
            return parse_translation_content(content, [idx for idx, _ in chunk])

        parts = await asyncio.gather(
            *[
                _correct_chunk(chunk)
                for chunk in chunked(items, self._settings.translation_batch_size)
            ]
        )
        merged: dict[int, str] = {}
        for part in parts:
            merged.update(part)
        return merged

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
        content = await self._chat_completion(
            {
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
            },
            "document translation failed",
        )
        try:
            data = json.loads(coerce_model_json(content))
            text = str(data["translation"]).strip()
        except (
            KeyError,
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
        merged: dict[int, str] = {}
        for chunk in chunked(items, self._settings.translation_batch_size):
            content = await self._chat_completion(
                {
                    "temperature": 0,
                    "response_format": _TRANSLATION_SCHEMA,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                f"You align a complete {tgt} dubbing translation onto "
                                f"numbered {src} source segments. CRITICAL RULES: "
                                "(1) idx N may express ONLY the meaning of source N — "
                                "never attach leftover clauses from a previous source "
                                "onto a short following line; "
                                "(2) if source N contains two clauses, both of their "
                                "meanings stay on idx N; "
                                "(3) never continue a previous sentence into the next "
                                "idx; never borrow words from neighbors; "
                                "(4) a short source (e.g. one clause) must get a short "
                                "matching translation — do not pad it with previous "
                                "leftover dialogue; "
                                "(5) cover the whole document without dropping content "
                                "by placing each clause on the idx that owns that "
                                "meaning. Return JSON "
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
                                        for idx, text in chunk
                                    ],
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ],
                },
                "translation alignment failed",
            )
            merged.update(
                parse_translation_content(content, [idx for idx, _ in chunk])
            )
        return merged

    async def adjust_translation(
        self, text: str, target_lang: str, target_seconds: float, direction: str
    ) -> str:
        """Compress or expand one line while preserving meaning and tone."""
        instruction = (
            f"{direction.capitalize()} this {LANGUAGE_NAMES.get(target_lang, target_lang)} "
            f"dubbing line to speak naturally in about {target_seconds:.2f} seconds. "
            "CRITICAL: keep the SAME meaning and the SAME content words. "
            "Do not add new clauses, names, or ideas that are not already in the line. "
            "Do not finish a neighboring sentence. Return only the rewritten line."
        )
        return (
            await self._chat_completion(
                {
                    "messages": [
                        {"role": "system", "content": instruction},
                        {"role": "user", "content": text},
                    ],
                    "temperature": 0.1,
                },
                "timing rewrite failed",
            )
        ).strip()

