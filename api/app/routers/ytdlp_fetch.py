"""Residential-IP YouTube fetch for Lightsail.

YouTube bot-gates AWS/Lightsail IPs. The production API starts a job here
(on a home PC / residential host), polls until yt-dlp finishes, then GETs
the file. Cloudflare's ~100s proxy limit cannot cover a blocking download,
so POST returns 202 immediately.

Never enable this without ``YTDLP_HOME_FETCH_TOKEN``.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..remote_media import RemoteMediaError, ingest_remote_media

router = APIRouter(tags=["internal"])

_JOB_ID_RE = re.compile(r"^[\w-]{8,64}$")


@dataclass
class _FetchJob:
    status: str
    path: Path | None = None
    error: str = ""
    dest: Path | None = None
    url: str = ""
    task: asyncio.Task | None = field(default=None, repr=False)


_JOBS: dict[str, _FetchJob] = {}


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


def _require_auth(authorization: str | None) -> None:
    expected = _expected_token()
    if not expected:
        raise HTTPException(404, "not found")
    if (authorization or "").strip() != f"Bearer {expected}":
        raise HTTPException(401, "unauthorized")


def _job_or_404(job_id: str) -> _FetchJob:
    if not _JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(404, "not found")
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "not found")
    return job


async def _run_job(job_id: str, url: str) -> None:
    job = _JOBS[job_id]
    dest = Path(tempfile.mkdtemp(prefix="dubby-ytdlp-fetch-"))
    job.dest = dest
    try:
        path = await ingest_remote_media(url, dest, allow_home_fetch=False)
        job.path = path
        job.status = "done"
    except RemoteMediaError as exc:
        job.status = "error"
        job.error = str(exc)
    except Exception as exc:  # noqa: BLE001
        job.status = "error"
        job.error = str(exc)[:400]


@router.post("/v1/internal/ytdlp-fetch", status_code=202)
async def ytdlp_fetch_start(
    body: YtdlpFetchRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_auth(authorization)
    job_id = secrets.token_urlsafe(16)
    job = _FetchJob(status="running", url=body.url.strip())
    _JOBS[job_id] = job
    job.task = asyncio.create_task(_run_job(job_id, job.url))
    return {"job_id": job_id, "status": "running"}


@router.get("/v1/internal/ytdlp-fetch/{job_id}")
async def ytdlp_fetch_status(
    job_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_auth(authorization)
    job = _job_or_404(job_id)
    payload = {"job_id": job_id, "status": job.status}
    if job.status == "error" and job.error:
        payload["error"] = job.error
    return payload


@router.get("/v1/internal/ytdlp-fetch/{job_id}/file")
async def ytdlp_fetch_file(
    job_id: str,
    authorization: str | None = Header(default=None),
) -> FileResponse:
    _require_auth(authorization)
    job = _job_or_404(job_id)
    if job.status == "running":
        raise HTTPException(409, "not ready")
    if job.status == "error" or job.path is None or not job.path.is_file():
        raise HTTPException(400, job.error or "download failed")
    suffix = job.path.suffix.lower() or ".mp4"
    media = "video/mp4" if suffix in {".mp4", ".m4v", ".mov"} else "application/octet-stream"
    return FileResponse(
        job.path,
        media_type=media,
        filename=f"source{suffix}",
    )
