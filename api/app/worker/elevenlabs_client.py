"""ElevenLabs Instant Voice Clone + TTS (single speaker).

When ``ELEVENLABS_VOICE_ID`` is configured the clone step is skipped and
that stock/pre-made voice is used for every project.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from ..config import Settings
from . import errors
from .errors import PipelineError

logger = logging.getLogger("dubby.worker.elevenlabs")

_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=10.0)


# multilingual_v2 does not support Vietnamese/Urdu; Ver 3.0 uses v3 instead.
_V3_ONLY_LANGUAGES = frozenset({"vi", "ur", "th"})

_V3_STABILITY = {
    "sad": 1.0,
    "whisper": 1.0,
    "calm": 0.5,
    "cheerful": 0.5,
    "angry": 0.0,
    "excited": 0.0,
    "energetic": 0.0,
}

_V3_AUDIO_TAGS = {
    "whisper": "[whispers] ",
    "excited": "[excited] ",
    "angry": "[angry] ",
    "sad": "[sad] ",
    "cheerful": "[happily] ",
    "energetic": "[excited] ",
}


def tts_model_for_language(configured_model: str, language: str) -> str:
    """Select a model that actually supports the requested language."""
    normalized = language.strip().lower().split("-", 1)[0]
    configured = (configured_model or "eleven_v3").strip()
    if configured in {"eleven_v3", "eleven_v3_conversational"}:
        return configured
    if normalized in _V3_ONLY_LANGUAGES and configured in {
        "eleven_multilingual_v1",
        "eleven_multilingual_v2",
    }:
        return "eleven_v3"
    return configured


def _is_eleven_v3(model: str) -> bool:
    return (model or "").startswith("eleven_v3")


def v3_tagged_text(text: str, tone_style: str) -> str:
    prefix = _V3_AUDIO_TAGS.get((tone_style or "").strip().lower(), "")
    if not prefix:
        return text
    if text.startswith("["):
        return text
    return f"{prefix}{text}"


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
        self._client = httpx.AsyncClient(timeout=_TIMEOUT)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_voices(self) -> list[dict]:
        try:
            resp = await self._client.get(
                f"{self._base}/v1/voices", headers=self._headers
            )
        except httpx.HTTPError as exc:
            raise PipelineError(
                errors.VOICE_CLONE_FAILED,
                f"voice list request failed: {exc}",
                retryable=True,
            ) from exc
        _raise_for_status(resp, errors.VOICE_CLONE_FAILED)
        payload = resp.json()
        voices = payload.get("voices") if isinstance(payload, dict) else None
        return [voice for voice in (voices or []) if isinstance(voice, dict)]

    @staticmethod
    def _is_dubby_temp_voice(voice: dict) -> bool:
        category = str(voice.get("category") or "").lower()
        if category in {"premade", "professional", "famous"}:
            return False
        name = str(voice.get("name") or "")
        description = str(voice.get("description") or "")
        return (
            name.startswith("Dubby ")
            or "dubby:temp" in description.lower()
            or "Temporary local Dubby" in description
            or "Dubby per-project" in description
        )

    async def purge_stale_dubby_voices(self, keep_ids: set[str] | None = None) -> int:
        keep = keep_ids or set()
        deleted = 0
        for voice in await self.list_voices():
            voice_id = str(voice.get("voice_id") or "").strip()
            if not voice_id or voice_id in keep:
                continue
            if not self._is_dubby_temp_voice(voice):
                continue
            await self.delete_voice(voice_id)
            deleted += 1
        return deleted

    async def _fallback_voice_on_add_limit(self) -> str | None:
        """Prefer configured / existing account voices when IVC create is blocked."""
        configured = (self._settings.elevenlabs_voice_id or "").strip()
        if configured:
            return configured
        voices = await self.list_voices()
        for voice in voices:
            voice_id = str(voice.get("voice_id") or "").strip()
            if voice_id and self._is_dubby_temp_voice(voice):
                return voice_id
        for voice in voices:
            category = str(voice.get("category") or "").lower()
            voice_id = str(voice.get("voice_id") or "").strip()
            if voice_id and category not in {"premade", "professional", "famous"}:
                return voice_id
        for voice in voices:
            voice_id = str(voice.get("voice_id") or "").strip()
            if voice_id:
                return voice_id
        return None

    async def create_voice(
        self,
        sample_path: str,
        name: str,
        *,
        description: str = "dubby:temp per-project instant voice clone",
        allow_limit_fallback: bool = True,
    ) -> tuple[str, bool]:
        """Instant Voice Clone from a single reference sample.

        Returns ``(voice_id, used_monthly_limit_fallback)``.
        """
        sample = Path(sample_path)
        files = {"files": (sample.name, sample.read_bytes(), "audio/mpeg")}
        data = {
            "name": name[:100],
            "description": (description or "")[:500],
        }

        async def _add() -> httpx.Response:
            try:
                return await self._client.post(
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

        async def _limit_fallback() -> tuple[str, bool]:
            if not allow_limit_fallback:
                raise PipelineError(
                    errors.VOICE_CLONE_FAILED,
                    "ElevenLabs monthly voice add/edit limit reached",
                )
            reused = await self._fallback_voice_on_add_limit()
            if reused:
                logger.warning(
                    "ElevenLabs monthly voice add/edit limit reached; using %s",
                    reused,
                )
                return reused, True
            raise PipelineError(
                errors.VOICE_CLONE_FAILED,
                "ElevenLabs monthly voice add/edit limit reached; "
                "no registered Voice ID available to reuse",
            )

        resp = await _add()
        body = resp.text
        if resp.status_code >= 400 and (
            "voice_add_edit_limit_reached" in body
            or "monthly limit of voice add/edit" in body
            or "voice add/edit operations" in body
        ):
            return await _limit_fallback()
        if resp.status_code >= 400 and (
            "voice_limit_reached" in body
            or "maximum amount of custom voices" in body
        ):
            await self.purge_stale_dubby_voices()
            resp = await _add()
            if resp.status_code >= 400 and (
                "voice_add_edit_limit_reached" in resp.text
                or "monthly limit of voice add/edit" in resp.text
                or "voice add/edit operations" in resp.text
            ):
                return await _limit_fallback()
            if not allow_limit_fallback:
                _raise_for_status(resp, errors.VOICE_CLONE_FAILED)
            reused = await self._fallback_voice_on_add_limit()
            if resp.status_code >= 400 and reused:
                logger.warning(
                    "ElevenLabs custom voice slot full; using existing voice %s",
                    reused,
                )
                return reused, True
        _raise_for_status(resp, errors.VOICE_CLONE_FAILED)
        voice_id = resp.json().get("voice_id")
        if not voice_id:
            raise PipelineError(
                errors.VOICE_CLONE_FAILED, "voice clone response had no voice_id"
            )
        return str(voice_id), False

    async def delete_voice(self, voice_id: str) -> None:
        """Best-effort cleanup of a per-project cloned voice."""
        try:
            await self._client.delete(
                f"{self._base}/v1/voices/{voice_id}", headers=self._headers
            )
        except httpx.HTTPError:
            logger.warning("could not delete cloned voice %s", voice_id)

    async def speech_to_speech_to_file(
        self,
        audio_path: str,
        voice_id: str,
        out_path: str,
        *,
        model_id: str = "eleven_multilingual_sts_v2",
    ) -> None:
        """Voice Changer: map performance in ``audio_path`` onto ``voice_id``."""
        attempts = max(1, self._settings.pipeline_step_retries + 1)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                with Path(audio_path).open("rb") as handle:
                    resp = await self._client.post(
                        f"{self._base}/v1/speech-to-speech/{voice_id}",
                        params={"output_format": "mp3_44100_128"},
                        headers=self._headers,
                        data={
                            "model_id": model_id,
                            "remove_background_noise": "true",
                        },
                        files={
                            "audio": (
                                Path(audio_path).name,
                                handle,
                                "audio/mpeg",
                            )
                        },
                    )
                _raise_for_status(resp, errors.TTS_FAILED)
                Path(out_path).write_bytes(resp.content)
                return
            except PipelineError as exc:
                last_error = exc
                if not exc.retryable or attempt >= attempts:
                    raise
                await asyncio.sleep(
                    self._settings.pipeline_retry_backoff_seconds * attempt
                )
            except httpx.HTTPError as exc:
                last_error = PipelineError(
                    errors.TTS_FAILED,
                    f"Voice Changer request failed: {exc}",
                    retryable=True,
                )
                if attempt >= attempts:
                    raise last_error from exc
                await asyncio.sleep(
                    self._settings.pipeline_retry_backoff_seconds * attempt
                )
        if last_error:
            raise last_error

    async def tts_to_file(
        self,
        text: str,
        voice_id: str,
        out_path: str,
        tone_style: str = "neutral",
        language: str = "",
        speed: float = 1.0,
    ) -> None:
        """Synthesize one segment to MP3 at ``out_path``.

        ``speed`` is ElevenLabs speaking-rate (pitch/timbre preserved). Prefer
        this over post-hoc atempo when fitting long translations into a slot.
        """
        model = tts_model_for_language(
            self._settings.elevenlabs_tts_model, language
        )
        from .emotion import voice_settings_for_emotion

        voice_settings = voice_settings_for_emotion(tone_style)
        clamped_speed = min(
            max(float(speed), 0.7),
            1.2,
        )
        lang_code = language.strip().lower().split("-", 1)[0] if language else ""
        if _is_eleven_v3(model):
            body: dict = {
                "text": v3_tagged_text(text, tone_style),
                "model_id": model,
                "voice_settings": {
                    "stability": _V3_STABILITY.get(
                        (tone_style or "").strip().lower(), 0.5
                    ),
                    "similarity_boost": float(
                        voice_settings.get("similarity_boost", 0.75)
                    ),
                },
            }
            if lang_code:
                body["language_code"] = lang_code
        else:
            body = {
                "text": text,
                "model_id": model,
                "voice_settings": {
                    **voice_settings,
                    "use_speaker_boost": True,
                    "speed": clamped_speed,
                },
                "apply_text_normalization": "on",
            }
            if lang_code and model != "eleven_multilingual_v2":
                body["language_code"] = lang_code
        attempts = max(1, self._settings.pipeline_step_retries + 1)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                resp = await self._client.post(
                    f"{self._base}/v1/text-to-speech/{voice_id}",
                    params={"output_format": "mp3_44100_128"},
                    headers=self._headers,
                    json=body,
                )
                _raise_for_status(resp, errors.TTS_FAILED)
                Path(out_path).write_bytes(resp.content)
                return
            except PipelineError as exc:
                last_error = exc
                if not exc.retryable or attempt >= attempts:
                    raise
                await asyncio.sleep(
                    self._settings.pipeline_retry_backoff_seconds * attempt
                )
            except httpx.HTTPError as exc:
                last_error = PipelineError(
                    errors.TTS_FAILED, f"TTS request failed: {exc}", retryable=True
                )
                if attempt >= attempts:
                    raise last_error from exc
                await asyncio.sleep(
                    self._settings.pipeline_retry_backoff_seconds * attempt
                )
        if last_error:
            raise last_error
