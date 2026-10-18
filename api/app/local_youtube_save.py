"""Local-only YouTube saver for the user's own PC.

The cloud API does not download YouTube. This process binds 127.0.0.1:8003
and saves the file through the browser (PC에 저장). It is not a tunnel target.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import __version__
from .remote_media import RemoteMediaError, download_with_ytdlp, is_youtube_media_url

app = FastAPI(title="Dubby local YouTube save", version=__version__, docs_url=None)

_JOBS: dict[str, "_SaveJob"] = {}


@dataclass
class _SaveJob:
    status: str
    path: Path | None = None
    error: str = ""
    task: asyncio.Task | None = field(default=None, repr=False)


@app.middleware("http")
async def _reject_non_local(request, call_next):  # noqa: ANN001
    client = request.client.host if request.client else ""
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded and forwarded not in {"127.0.0.1", "::1"}:
        return JSONResponse({"detail": "localhost only"}, status_code=403)
    if client not in {"127.0.0.1", "::1", "localhost", "testclient", ""}:
        return JSONResponse({"detail": "localhost only"}, status_code=403)
    return await call_next(request)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "role": "local-youtube-save", "version": __version__}


@app.get("/save", response_class=HTMLResponse)
def save_page(url: str = "") -> str:
    payload = json.dumps(url.strip())
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <title>Dubby · PC에 저장</title>
  <style>
    body {{ font-family: sans-serif; max-width: 36rem; margin: 2rem auto; padding: 0 1rem; }}
    button {{ font-size: 1rem; padding: .6rem 1rem; }}
    .err {{ color: #b00020; }}
    .ok {{ color: #0b6; }}
  </style>
</head>
<body>
  <h1>유튜브 PC 저장</h1>
  <p>이 창은 이 PC에서만 영상을 받습니다. 저장이 끝나면 Dubby 탭에서 그 파일을 올리세요.</p>
  <p id="status">준비 중…</p>
  <p id="error" class="err"></p>
  <script>
    const url = {payload};
    const status = document.getElementById("status");
    const error = document.getElementById("error");
    async function run() {{
      if (!url) {{
        status.textContent = "유튜브 링크가 없습니다. Dubby에서 다시 PC에 저장을 누르세요.";
        return;
      }}
      status.textContent = "다운로드 중… 창을 닫지 마세요.";
      const started = await fetch("/v1/local/youtube-save", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ url }}),
      }});
      const body = await started.json();
      if (!started.ok) {{
        error.textContent = body.detail || "시작할 수 없습니다.";
        return;
      }}
      const jobId = body.job_id;
      for (;;) {{
        const st = await fetch("/v1/local/youtube-save/" + jobId);
        const info = await st.json();
        if (info.status === "done") {{
          status.textContent = "저장 대화 상자를 확인하세요. 받은 파일을 Dubby에 업로드하세요.";
          status.className = "ok";
          window.location.href = "/v1/local/youtube-save/" + jobId + "/file";
          return;
        }}
        if (info.status === "error") {{
          error.textContent = info.error || "다운로드에 실패했습니다.";
          return;
        }}
        await new Promise((r) => setTimeout(r, 1500));
      }}
    }}
    run().catch((err) => {{ error.textContent = String(err); }});
  </script>
</body>
</html>
"""


@app.post("/v1/local/youtube-save")
async def start_save(payload: dict) -> dict:
    url = str((payload or {}).get("url") or "").strip()
    if not is_youtube_media_url(url):
        raise HTTPException(400, "유튜브 링크만 저장할 수 있습니다.")
    job_id = secrets.token_urlsafe(16)
    job = _SaveJob(status="running")
    _JOBS[job_id] = job

    async def _run() -> None:
        dest = Path(tempfile.mkdtemp(prefix="dubby-yt-save-"))
        try:
            path = await asyncio.to_thread(download_with_ytdlp, url, dest)
            job.path = path
            job.status = "done"
        except RemoteMediaError as exc:
            job.status = "error"
            job.error = str(exc)
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = str(exc)[:400]

    job.task = asyncio.create_task(_run())
    return {"job_id": job_id, "status": "running"}


@app.get("/v1/local/youtube-save/{job_id}")
async def save_status(job_id: str) -> dict:
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "not found")
    payload = {"job_id": job_id, "status": job.status}
    if job.status == "error" and job.error:
        payload["error"] = job.error
    return payload


@app.get("/v1/local/youtube-save/{job_id}/file")
async def save_file(job_id: str) -> FileResponse:
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "not found")
    if job.status == "running":
        raise HTTPException(409, "not ready")
    if job.status == "error" or job.path is None or not job.path.is_file():
        raise HTTPException(400, job.error or "download failed")
    suffix = job.path.suffix.lower() or ".mp4"
    return FileResponse(
        job.path,
        media_type="video/mp4" if suffix == ".mp4" else "application/octet-stream",
        filename=job.path.name,
    )
