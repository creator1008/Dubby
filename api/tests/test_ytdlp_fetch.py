"""Internal home-fetch endpoint used when Lightsail is YouTube bot-gated."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.home_fetch import app as home_fetch_app
from app.remote_media import RemoteMediaError
from app.routers import ytdlp_fetch
from app.routers.ytdlp_fetch import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_jobs() -> None:
    ytdlp_fetch._JOBS.clear()
    yield
    ytdlp_fetch._JOBS.clear()


def _wait_status(client: TestClient, job_id: str, headers: dict) -> dict:
    for _ in range(40):
        resp = client.get(f"/v1/internal/ytdlp-fetch/{job_id}", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError("home-fetch job stayed running")


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


def test_ytdlp_fetch_job_then_file(
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
    headers = {"Authorization": "Bearer secret"}
    client = _client()
    resp = client.post(
        "/v1/internal/ytdlp-fetch",
        json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        headers=headers,
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    assert _wait_status(client, job_id, headers)["status"] == "done"
    file_resp = client.get(f"/v1/internal/ytdlp-fetch/{job_id}/file", headers=headers)
    assert file_resp.status_code == 200
    assert file_resp.content.startswith(b"ok-video")
    assert "video/mp4" in (file_resp.headers.get("content-type") or "")


def test_ytdlp_fetch_maps_remote_media_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(*_a, **_k):
        raise RemoteMediaError("집 PC에서도 실패")

    monkeypatch.setattr("app.routers.ytdlp_fetch._expected_token", lambda: "secret")
    monkeypatch.setattr("app.routers.ytdlp_fetch.ingest_remote_media", boom)
    headers = {"Authorization": "Bearer secret"}
    client = _client()
    resp = client.post(
        "/v1/internal/ytdlp-fetch",
        json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        headers=headers,
    )
    assert resp.status_code == 202
    body = _wait_status(client, resp.json()["job_id"], headers)
    assert body["status"] == "error"
    assert "집 PC에서도 실패" in body["error"]


def test_home_fetch_app_healthz() -> None:
    with TestClient(home_fetch_app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["role"] == "ytdlp-home-fetch"
