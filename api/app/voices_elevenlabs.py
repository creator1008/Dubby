"""ElevenLabs Voice Library helpers for the authenticated API."""

from __future__ import annotations

from typing import Any

import httpx

from .config import Settings
from .errors import BadRequestError, FeatureUnavailableError

# Common accents keyed by language code. Accent filter is only meaningful
# after a language is chosen; the UI disables Accent until then.
ACCENTS_BY_LANGUAGE: dict[str, list[str]] = {
    "en": [
        "american",
        "british",
        "australian",
        "canadian",
        "irish",
        "scottish",
        "indian",
        "south african",
        "new zealand",
    ],
    "ko": ["standard", "seoul"],
    "vi": ["northern", "southern", "central"],
    "zh": ["mandarin", "cantonese", "taiwanese"],
    "ja": ["standard", "tokyo"],
    "es": ["spanish", "mexican", "argentinian", "colombian", "castilian"],
    "fr": ["french", "canadian french", "parisian"],
    "pt": ["brazilian", "portuguese"],
    "de": ["german", "austrian", "swiss"],
    "ru": ["russian"],
    "ar": ["standard", "egyptian", "gulf"],
    "id": ["indonesian"],
    "ms": ["malaysian"],
    "tr": ["turkish"],
    "ta": ["indian", "sri lankan"],
    "ur": ["pakistani", "indian"],
}

GENDER_OPTIONS = ["male", "female", "neutral"]
AGE_OPTIONS = ["young", "middle_aged", "old"]
CATEGORY_OPTIONS = ["professional", "high_quality", "famous"]


def _headers(settings: Settings) -> dict[str, str]:
    if not settings.elevenlabs_api_key:
        raise FeatureUnavailableError("ElevenLabs API key is not configured")
    return {"xi-api-key": settings.elevenlabs_api_key}


async def fetch_shared_voices(
    settings: Settings,
    *,
    page: int = 0,
    page_size: int = 30,
    language: str | None = None,
    accent: str | None = None,
    category: str | None = None,
    gender: str | None = None,
    age: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    params: dict[str, str | int] = {
        "page": max(0, page),
        "page_size": min(100, max(1, page_size)),
        "sort": "trending",
    }
    if language:
        params["language"] = language
    if accent:
        if not language:
            raise BadRequestError("Accent requires a language to be selected")
        params["accent"] = accent
    if category:
        params["category"] = category
    if gender:
        params["gender"] = gender
    if age:
        params["age"] = age
    if search:
        params["search"] = search

    base = settings.elevenlabs_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.get(
            f"{base}/v1/shared-voices",
            headers=_headers(settings),
            params=params,
        )
    if resp.status_code >= 400:
        raise FeatureUnavailableError(
            f"ElevenLabs shared voices failed ({resp.status_code})"
        )
    payload = resp.json()
    if not isinstance(payload, dict):
        raise FeatureUnavailableError("Unexpected ElevenLabs response")
    return payload


async def add_shared_voice_to_account(
    settings: Settings,
    *,
    public_owner_id: str,
    voice_id: str,
    new_name: str,
) -> str:
    """Add a shared library voice to the ElevenLabs account; return voice_id."""
    base = settings.elevenlabs_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.post(
            f"{base}/v1/voices/add/{public_owner_id}/{voice_id}",
            headers=_headers(settings),
            json={"new_name": new_name, "bookmarked": True},
        )
    if resp.status_code >= 400:
        # Still allow saving to Dubby Voice Box using the shared voice_id.
        # Some plans already own the voice or reject duplicates.
        text = (resp.text or "").lower()
        if resp.status_code in {400, 409, 422} and (
            "already" in text or "exists" in text
        ):
            return voice_id
        raise BadRequestError(
            f"Failed to add voice to ElevenLabs account ({resp.status_code})"
        )
    payload = resp.json() if resp.content else {}
    account_voice_id = (
        payload.get("voice_id") if isinstance(payload, dict) else None
    )
    return str(account_voice_id or voice_id)
