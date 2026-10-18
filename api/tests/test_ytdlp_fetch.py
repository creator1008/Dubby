"""Internal home-fetch endpoint used when Lightsail is YouTube bot-gated."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.remote_media import RemoteMediaError
from app.routers.ytdlp_fetch import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_ytdlp_fetch_hidden_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routers.ytdlp_fetch._expected_token", lambda: "")
    resp = _client().post(
        "/v1/internal/ytdlp-fetch",
        json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )
    assert resp.status_code == 404


def test_ytdlp_fetch_rejects_bad_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routers.ytdlp_fetch._expected_token", lambda: "secret")
    resp = _client().post(
        "/v1/internal/ytdlp-fetch",
        json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        headers={"Authorization": "Bearer nope"},
    )
    assert resp.status_code == 401


def test_ytdlp_fetch_streams_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "source.mp4"
    video.write_bytes(b"ok-video" * 16)

    async def fake_ingest(url: str, dest: Path, allow_home_fetch: bool = True):
        assert allow_home_fetch is False
        assert "youtube.com" in url
        return video

    monkeypatch.setattr("app.routers.ytdlp_fetch._expected_token", lambda: "secret")
    monkeypatch.setattr("app.routers.ytdlp_fetch.ingest_remote_media", fake_ingest)
    resp = _client().post(
        "/v1/internal/ytdlp-fetch",
        json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        headers={"Authorization": "Bearer secret"},
    )
    assert resp.status_code == 200
    assert resp.content.startswith(b"ok-video")
    assert "video/mp4" in (resp.headers.get("content-type") or "")


def test_ytdlp_fetch_maps_remote_media_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(*_a, **_k):
        raise RemoteMediaError("집 PC에서도 실패")

    monkeypatch.setattr("app.routers.ytdlp_fetch._expected_token", lambda: "secret")
    monkeypatch.setattr("app.routers.ytdlp_fetch.ingest_remote_media", boom)
    resp = _client().post(
        "/v1/internal/ytdlp-fetch",
        json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        headers={"Authorization": "Bearer secret"},
    )
    assert resp.status_code == 400
    assert "집 PC에서도 실패" in resp.text
