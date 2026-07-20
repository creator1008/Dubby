from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from uuid import uuid4

from app.auth import AuthenticatedUser
from app.config import Settings
from app.routers.jobs import create_job
from app.schemas import JobCreate
from app.worker.lipsync import SyncJob, SyncLabsClient


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_sync_response_validation() -> None:
    assert SyncJob.model_validate({"id": "j1", "status": "PENDING"}).id == "j1"
    with pytest.raises(ValidationError):
        SyncJob.model_validate({"id": "", "status": "unknown"})


@pytest.mark.anyio
async def test_sync_create_sends_idempotency_and_validated_payload(monkeypatch) -> None:
    client = SyncLabsClient(Settings(_env_file=None, sync_api_key="test"))
    captured: dict = {}

    async def fake_request(method: str, path: str, **kwargs) -> httpx.Response:
        captured.update(method=method, path=path, **kwargs)
        return httpx.Response(
            200,
            json={"id": "job-1", "status": "PENDING"},
            request=httpx.Request("POST", "https://api.sync.so/v2/generate"),
        )

    monkeypatch.setattr(client, "_request", fake_request)
    job = await client.create("https://video", "https://audio", "idem-1")
    assert job.id == "job-1"
    assert captured["headers"]["Idempotency-Key"] == "idem-1"
    assert captured["json"]["input"][1] == {"type": "audio", "url": "https://audio"}


@pytest.mark.anyio
async def test_lipsync_disabled_is_gracefully_unavailable(monkeypatch) -> None:
    project_id = uuid4()

    class Repo:
        async def get_project(self, owner_id, requested_id):
            return {"id": requested_id, "duration_seconds": 10, "output_key": "dub.mp4"}

    from app.config import get_settings

    monkeypatch.setenv("LIPSYNC_PROVIDER", "disabled")
    get_settings.cache_clear()
    try:
        with pytest.raises(HTTPException) as exc:
            await create_job(
                project_id,
                JobCreate(kind="lipsync"),
                AuthenticatedUser(id=uuid4(), email=None, role="authenticated"),
                Repo(),  # type: ignore[arg-type]
            )
    finally:
        get_settings.cache_clear()
    assert exc.value.status_code == 503
    assert "feature_unavailable" in str(exc.value.detail)
