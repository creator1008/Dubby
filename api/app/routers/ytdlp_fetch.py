"""Residential-IP YouTube fetch for Lightsail.

YouTube bot-gates AWS/Lightsail IPs. The production API can POST the page
URL here (on a home PC / residential host) and receive the downloaded file.
Never enable this without ``YTDLP_HOME_FETCH_TOKEN``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..remote_media import RemoteMediaError, ingest_remote_media

router = APIRouter(tags=["internal"])


class YtdlpFetchRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


def _expected_token() -> str:
    try:
        from ..config import get_settings

        return (get_settings().ytdlp_home_fetch_token or "").strip() or os.getenv(
            "YTDLP_HOME_FETCH_TOKEN", ""
        ).strip()
    except Exception:  # noqa: BLE001
        return os.getenv("YTDLP_HOME_FETCH_TOKEN", "").strip()


@router.post("/v1/internal/ytdlp-fetch")
async def ytdlp_fetch(
    body: YtdlpFetchRequest,
    authorization: str | None = Header(default=None),
) -> FileResponse:
    expected = _expected_token()
    if not expected:
        raise HTTPException(404, "not found")
    offered = (authorization or "").strip()
    if offered != f"Bearer {expected}":
        raise HTTPException(401, "unauthorized")
    dest = Path(tempfile.mkdtemp(prefix="dubby-ytdlp-fetch-"))
    try:
        path = await ingest_remote_media(
            body.url.strip(), dest, allow_home_fetch=False
        )
    except RemoteMediaError as exc:
        raise HTTPException(400, str(exc)) from exc
    suffix = path.suffix.lower() or ".mp4"
    media = "video/mp4" if suffix in {".mp4", ".m4v", ".mov"} else "application/octet-stream"
    return FileResponse(
        path,
        media_type=media,
        filename=f"source{suffix}",
    )
