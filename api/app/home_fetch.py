"""Minimal residential YouTube fetch process for a home PC.

Lightsail POSTs page URLs here when the cloud IP is bot-gated. Do not set
``YTDLP_HOME_FETCH_URL`` on this host (that would loop). Bind localhost and
expose only via the named Cloudflare tunnel (``fetch.dubbyai.com``).

    uvicorn app.home_fetch:app --host 127.0.0.1 --port 8003
"""

from __future__ import annotations

from fastapi import FastAPI

from . import __version__
from .routers.ytdlp_fetch import router

app = FastAPI(title="Dubby home YouTube fetch", version=__version__, docs_url=None)
app.include_router(router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "role": "ytdlp-home-fetch", "version": __version__}
