"""Sync Labs premium lip-sync providers with an offline mock."""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from ..config import Settings
from . import errors
from .errors import PipelineError


class SyncJob(BaseModel):
    id: str = Field(min_length=1)
    status: Literal["PENDING", "PROCESSING", "COMPLETED", "FAILED"]
    output_url: str | None = None
    error: str | None = None


class LipSyncProvider(Protocol):
    async def render(
        self,
        video_url: str,
        audio_url: str,
        output_path: str,
        idempotency_key: str,
    ) -> None: ...


class MockLipSyncProvider:
    async def render(
        self,
        video_url: str,
        audio_url: str,
        output_path: str,
        idempotency_key: str,
    ) -> None:
        del audio_url, idempotency_key
        source = video_url.removeprefix("file://")
        shutil.copyfile(source, output_path)


class SyncLabsClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.sync_api_key:
            raise PipelineError(errors.FEATURE_UNAVAILABLE, "SYNC_API_KEY is not configured")
        self._settings = settings
        self._base = settings.sync_base_url.rstrip("/")
        self._headers = {
            "x-api-key": settings.sync_api_key,
            "Content-Type": "application/json",
        }
        self._timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        last: Exception | None = None
        headers = {**self._headers, **kwargs.pop("headers", {})}
        for attempt in range(self._settings.pipeline_step_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.request(
                        method, f"{self._base}{path}", headers=headers, **kwargs
                    )
                if response.status_code != 429 and response.status_code < 500:
                    return response
                last = PipelineError(
                    errors.LIPSYNC_FAILED,
                    f"Sync API returned {response.status_code}",
                    retryable=True,
                )
            except httpx.HTTPError as exc:
                last = exc
            if attempt < self._settings.pipeline_step_retries:
                await asyncio.sleep(
                    self._settings.pipeline_retry_backoff_seconds * (attempt + 1)
                )
        raise PipelineError(
            errors.LIPSYNC_FAILED, f"Sync request failed: {last}", retryable=True
        )

    @staticmethod
    def _parse(response: httpx.Response) -> SyncJob:
        if response.status_code >= 400:
            raise PipelineError(
                errors.LIPSYNC_FAILED,
                f"Sync API returned {response.status_code}: {response.text[:300]}",
                retryable=response.status_code == 429 or response.status_code >= 500,
            )
        try:
            return SyncJob.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise PipelineError(
                errors.LIPSYNC_FAILED, "invalid Sync API response", retryable=True
            ) from exc

    async def create(
        self, video_url: str, audio_url: str, idempotency_key: str
    ) -> SyncJob:
        response = await self._request(
            "POST",
            "/v2/generate",
            headers={**self._headers, "Idempotency-Key": idempotency_key},
            json={
                "model": self._settings.sync_model,
                "input": [{"type": "video", "url": video_url}, {"type": "audio", "url": audio_url}],
            },
        )
        return self._parse(response)

    async def get(self, job_id: str) -> SyncJob:
        return self._parse(await self._request("GET", f"/v2/generate/{job_id}"))

    async def download(self, url: str, output_path: str) -> None:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
            ) as client:
                response = await client.get(url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if not response.content or (
                content_type and "video/" not in content_type and "octet-stream" not in content_type
            ):
                raise PipelineError(
                    errors.LIPSYNC_FAILED,
                    f"invalid lip-sync result content-type: {content_type or 'missing'}",
                )
            Path(output_path).write_bytes(response.content)
        except PipelineError:
            raise
        except httpx.HTTPError as exc:
            raise PipelineError(
                errors.LIPSYNC_FAILED, f"lip-sync result download failed: {exc}", retryable=True
            ) from exc


class SyncLabsProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = SyncLabsClient(settings)

    async def render(
        self,
        video_url: str,
        audio_url: str,
        output_path: str,
        idempotency_key: str,
    ) -> None:
        job = await self._client.create(video_url, audio_url, idempotency_key)
        deadline = time.monotonic() + self._settings.sync_timeout_seconds
        while job.status in ("PENDING", "PROCESSING"):
            if time.monotonic() >= deadline:
                raise PipelineError(errors.LIPSYNC_FAILED, "Sync job polling timed out")
            await asyncio.sleep(self._settings.sync_poll_interval_seconds)
            job = await self._client.get(job.id)
        if job.status != "COMPLETED" or not job.output_url:
            raise PipelineError(
                errors.LIPSYNC_FAILED, f"Sync job failed: {job.error or job.status}"
            )
        await self._client.download(job.output_url, output_path)


def create_lipsync_provider(settings: Settings) -> LipSyncProvider:
    if settings.lipsync_provider == "mock":
        return MockLipSyncProvider()
    if settings.lipsync_provider == "sync":
        return SyncLabsProvider(settings)
    raise PipelineError(errors.FEATURE_UNAVAILABLE, "lip sync provider is disabled")
