from pathlib import Path

import httpx
import pytest

from app.remote_media import (
    RemoteMediaError,
    _filename_from_response,
    _validate_public_url,
)


def test_remote_filename_uses_content_disposition() -> None:
    response = httpx.Response(
        200,
        headers={
            "content-type": "video/mp4",
            "content-disposition": 'attachment; filename="lesson final.mp4"',
        },
        request=httpx.Request("GET", "https://media.example/video"),
    )
    assert (
        _filename_from_response(response, "https://media.example/video")
        == "lesson_final.mp4"
    )


def test_remote_filename_infers_suffix_from_content_type() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "video/webm"},
        request=httpx.Request("GET", "https://media.example/watch?id=1"),
    )
    assert (
        Path(_filename_from_response(response, "https://media.example/watch?id=1")).suffix
        == ".webm"
    )


@pytest.mark.anyio
async def test_remote_url_rejects_localhost() -> None:
    with pytest.raises(RemoteMediaError, match="로컬"):
        await _validate_public_url("http://localhost/video.mp4")
