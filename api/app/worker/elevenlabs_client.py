"""ElevenLabs Instant Voice Clone + TTS (single speaker).

When ``ELEVENLABS_VOICE_ID`` is configured the clone step is skipped and
that stock/pre-made voice is used for every project.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from ..config import Settings
from . import errors
from .errors import PipelineError

logger = logging.getLogger("dubby.worker.elevenlabs")

_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=10.0)


def tts_model_for_language(configured_model: str, language: str) -> str:
    """Select a model that actually supports the requested language."""
    normalized = language.strip().lower().split("-", 1)[0]
    if normalized == "vi" and configured_model in {
        "eleven_multilingual_v1",
        "eleven_multilingual_v2",
    }:
        return "eleven_flash_v2_5"
    return configured_model


def _raise_for_status(resp: httpx.Response, code: str) -> None:
    if resp.status_code < 400:
        return
    retryable = resp.status_code == 429 or resp.status_code >= 500
    raise PipelineError(
        code,
        f"ElevenLabs API returned {resp.status_code}: {resp.text[:300]}",
        retryable=retryable,
    )


class ElevenLabsClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.elevenlabs_api_key:
            raise PipelineError(
                errors.CONFIG_MISSING, "ELEVENLABS_API_KEY is not configured"
            )
        self._settings = settings
        self._base = settings.elevenlabs_base_url.rstrip("/")
        self._headers = {"xi-api-key": settings.elevenlabs_api_key}

    async def create_voice(self, sample_path: str, name: str) -> str:
        """Instant Voice Clone from a single reference sample; returns voice_id."""
        sample = Path(sample_path)
        files = {"files": (sample.name, sample.read_bytes(), "audio/mpeg")}
        data = {
            "name": name[:100],
            "description": "Dubby per-project instant voice clone",
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._base}/v1/voices/add",
                    headers=self._headers,
                    data=data,
                    files=files,
                )
        except httpx.HTTPError as exc:
            raise PipelineError(
                errors.VOICE_CLONE_FAILED,
                f"voice clone request failed: {exc}",
                retryable=True,
            ) from exc
        _raise_for_status(resp, errors.VOICE_CLONE_FAILED)
        voice_id = resp.json().get("voice_id")
        if not voice_id:
            raise PipelineError(
                errors.VOICE_CLONE_FAILED, "voice clone response had no voice_id"
            )
        return str(voice_id)

    async def delete_voice(self, voice_id: str) -> None:
        """Best-effort cleanup of a per-project cloned voice."""
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                await client.delete(
                    f"{self._base}/v1/voices/{voice_id}", headers=self._headers
                )
        except httpx.HTTPError:
            logger.warning("could not delete cloned voice %s", voice_id)

    async def tts_to_file(
        self,
        text: str,
        voice_id: str,
        out_path: str,
        tone_style: str = "neutral",
        language: str = "",
    ) -> None:
        """Synthesize one segment to MP3 at ``out_path``."""
        model = tts_model_for_language(
            self._settings.elevenlabs_tts_model, language
        )
        body = {
            "text": text,
            "model_id": model,
            "voice_settings": {
                "neutral": {"stability": 0.55, "similarity_boost": 0.75, "style": 0.0},
                "warm": {"stability": 0.48, "similarity_boost": 0.78, "style": 0.25},
                "energetic": {"stability": 0.32, "similarity_boost": 0.72, "style": 0.65},
                "serious": {"stability": 0.75, "similarity_boost": 0.8, "style": 0.15},
            }.get(tone_style, {"stability": 0.55, "similarity_boost": 0.75, "style": 0.0}),
            "apply_text_normalization": "on",
        }
        if language and model != "eleven_multilingual_v2":
            body["language_code"] = language.strip().lower().split("-", 1)[0]
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._base}/v1/text-to-speech/{voice_id}",
                    params={"output_format": "mp3_44100_128"},
                    headers=self._headers,
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise PipelineError(
                errors.TTS_FAILED, f"TTS request failed: {exc}", retryable=True
            ) from exc
        _raise_for_status(resp, errors.TTS_FAILED)
        Path(out_path).write_bytes(resp.content)
