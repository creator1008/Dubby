"""Translate Voice Library descriptions into the UI locale."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import httpx

from .config import Settings
from .languages import LANGUAGE_NAMES

logger = logging.getLogger("dubby.voices.translate")

_CACHE: dict[str, str] = {}
_CACHE_MAX = 2000

_LOCALE_NAMES = {
    "ko": "Korean",
    "en": "English",
    "vi": "Vietnamese",
}


def _cache_key(text: str, locale: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    return f"{locale}:{digest}"


def _remember(key: str, value: str) -> str:
    if len(_CACHE) >= _CACHE_MAX:
        # Drop an arbitrary older half when full.
        for stale in list(_CACHE.keys())[: _CACHE_MAX // 2]:
            _CACHE.pop(stale, None)
    _CACHE[key] = value
    return value


async def translate_descriptions(
    settings: Settings,
    texts: list[str],
    *,
    ui_locale: str,
) -> list[str]:
    """Return texts translated into ``ui_locale`` (ko/en/vi).

    Empty strings and English-locale requests are returned unchanged.
    Failures fall back to the original text so the library still renders.
    """
    locale = (ui_locale or "en").strip().lower().split("-", 1)[0]
    if locale == "en" or not texts:
        return list(texts)
    from .worker.openai_client import is_grok_model

    needs_key = (
        settings.xai_api_key
        if is_grok_model(settings.translation_model or "gpt-4o-mini")
        else settings.openai_api_key
    )
    if not needs_key:
        return list(texts)

    target_name = _LOCALE_NAMES.get(locale) or LANGUAGE_NAMES.get(locale) or locale
    out: list[str | None] = [None] * len(texts)
    pending: list[tuple[int, str]] = []

    for idx, raw in enumerate(texts):
        text = (raw or "").strip()
        if not text:
            out[idx] = raw or ""
            continue
        key = _cache_key(text, locale)
        cached = _CACHE.get(key)
        if cached is not None:
            out[idx] = cached
        else:
            pending.append((idx, text))

    if not pending:
        return [item if item is not None else "" for item in out]

    # Batch in chunks to keep prompts small.
    chunk_size = 20
    for start in range(0, len(pending), chunk_size):
        chunk = pending[start : start + chunk_size]
        try:
            translated = await _translate_chunk(settings, chunk, target_name)
        except Exception:  # noqa: BLE001 - UI should still show originals
            logger.exception("voice description translation failed")
            translated = [text for _, text in chunk]
        for (idx, original), value in zip(chunk, translated, strict=False):
            cleaned = (value or "").strip() or original
            out[idx] = _remember(_cache_key(original, locale), cleaned)

    return [item if item is not None else "" for item in out]


async def _translate_chunk(
    settings: Settings,
    chunk: list[tuple[int, str]],
    target_name: str,
) -> list[str]:
    from .worker.openai_client import chat_endpoint_for_model

    payload_items = [{"id": i, "text": text} for i, (_, text) in enumerate(chunk)]
    system = (
        "You translate short voice-profile descriptions for a dubbing app UI. "
        f"Translate each item's text into {target_name}. "
        "Keep meaning, tone, and proper nouns when appropriate. "
        "Do not translate personal names that are already proper nouns unless "
        "natural in the target language. "
        "Return ONLY a JSON object: {\"items\":[{\"id\":0,\"text\":\"...\"}, ...]} "
        "with the same ids and count as the input."
    )
    model = settings.translation_model or "gpt-4o-mini"
    base, headers, extras = chat_endpoint_for_model(
        model,
        openai_api_key=settings.openai_api_key,
        openai_base_url=settings.openai_base_url,
        xai_api_key=settings.xai_api_key,
        xai_base_url=settings.xai_base_url,
        reasoning_effort=settings.translation_reasoning_effort,
    )
    body: dict[str, Any] = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({"items": payload_items})},
        ],
        **extras,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(45.0)) as client:
        resp = await client.post(
            f"{base.rstrip('/')}/chat/completions",
            headers={**headers, "Content-Type": "application/json"},
            json=body,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"voice description translate failed ({resp.status_code})")
    data = resp.json()
    content = (
        (((data.get("choices") or [{}])[0]).get("message") or {}).get("content")
        or ""
    )
    parsed = json.loads(content)
    items = parsed.get("items") if isinstance(parsed, dict) else None
    by_id: dict[int, str] = {}
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                item_id = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            by_id[item_id] = str(item.get("text") or "")
    return [by_id.get(i, text) for i, (_, text) in enumerate(chunk)]
