"""Local-only YouTube saver used by the PC에 저장 button."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.local_youtube_save import _JOBS, app
from app.remote_media import RemoteMediaError


@pytest.fixture(autouse=True)
def _clear_jobs() -> None:
    _JOBS.clear()
    yield
    _JOBS.clear()


def _wait(client: TestClient, job_id: str) -> dict:
    for _ in range(40):
        resp = client.get(f"/v1/local/youtube-save/{job_id}")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError("local youtube save stayed running")


def test_local_save_healthz() -> None:
    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["role"] == "local-youtube-save"


def test_local_save_rejects_non_youtube() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/v1/local/youtube-save",
            json={"url": "https://cdn.example.com/clip.mp4"},
        )
        assert resp.status_code == 400


def test_local_save_job_then_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"ok-local" * 16)

    def fake_download(url: str, dest: Path, **_k):
        assert "youtube.com" in url or "youtu.be" in url
        return video

    monkeypatch.setattr("app.local_youtube_save.download_with_ytdlp", fake_download)
    with TestClient(app) as client:
        resp = client.post(
            "/v1/local/youtube-save",
            json={"url": "https://youtu.be/9zOq6_MK40A"},
        )
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]
        assert _wait(client, job_id)["status"] == "done"
        file_resp = client.get(f"/v1/local/youtube-save/{job_id}/file")
        assert file_resp.status_code == 200
        assert file_resp.content.startswith(b"ok-local")


def test_local_save_maps_remote_media_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a, **_k):
        raise RemoteMediaError("로컬에서도 실패")

    monkeypatch.setattr("app.local_youtube_save.download_with_ytdlp", boom)
    with TestClient(app) as client:
        resp = client.post(
            "/v1/local/youtube-save",
            json={"url": "https://www.youtube.com/watch?v=abc"},
        )
        assert resp.status_code == 200
        body = _wait(client, resp.json()["job_id"])
        assert body["status"] == "error"
        assert "로컬에서도 실패" in body["error"]
