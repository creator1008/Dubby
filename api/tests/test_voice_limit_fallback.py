"""Tests for ElevenLabs monthly voice-add limit fallback."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.worker.elevenlabs_client import ElevenLabsClient


def test_create_voice_falls_back_to_configured_id_on_monthly_limit(tmp_path) -> None:
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio")

    settings = SimpleNamespace(
        elevenlabs_api_key="test-key",
        elevenlabs_base_url="https://api.elevenlabs.io",
        elevenlabs_voice_id="env-voice-123",
    )
    client = ElevenLabsClient(settings)  # type: ignore[arg-type]

    limit_resp = MagicMock()
    limit_resp.status_code = 400
    limit_resp.text = '{"detail":{"status":"voice_add_edit_limit_reached"}}'

    client._client = AsyncMock()
    client._client.post = AsyncMock(return_value=limit_resp)
    client.list_voices = AsyncMock(return_value=[])  # type: ignore[method-assign]

    async def _run() -> tuple[str, bool]:
        try:
            return await client.create_voice(str(sample), "dubby-test")
        finally:
            await client.aclose()

    voice_id, used_fallback = asyncio.run(_run())
    assert voice_id == "env-voice-123"
    assert used_fallback is True


def test_create_voice_falls_back_to_existing_account_voice(tmp_path) -> None:
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio")

    settings = SimpleNamespace(
        elevenlabs_api_key="test-key",
        elevenlabs_base_url="https://api.elevenlabs.io",
        elevenlabs_voice_id="",
    )
    client = ElevenLabsClient(settings)  # type: ignore[arg-type]

    limit_resp = MagicMock()
    limit_resp.status_code = 400
    limit_resp.text = "monthly limit of voice add/edit operations"

    client._client = AsyncMock()
    client._client.post = AsyncMock(return_value=limit_resp)
    client.list_voices = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "voice_id": "cloned-abc",
                "category": "cloned",
                "name": "My Clone",
                "description": "",
            }
        ]
    )

    async def _run() -> tuple[str, bool]:
        try:
            return await client.create_voice(str(sample), "dubby-test")
        finally:
            await client.aclose()

    voice_id, used_fallback = asyncio.run(_run())
    assert voice_id == "cloned-abc"
    assert used_fallback is True
