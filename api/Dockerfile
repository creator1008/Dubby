# Dubby API + worker images (multi-stage, multi-target).
#
#   docker build --target api    -t dubby-api    api/
#   docker build --target worker -t dubby-worker api/
#
# The worker target adds ffmpeg and the CPU builds of torch/Demucs, which is
# why it is a separate (much larger) image.

# --- shared base -------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --system dubby && useradd --system --gid dubby --create-home dubby

WORKDIR /srv/dubby

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app ./app

# --- API server ----------------------------------------------------------------
FROM base AS api

USER dubby
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

# --- Pipeline worker (ffmpeg + Demucs) --------------------------------------------
FROM base AS worker

# ffmpeg (with libass) for extraction/mix/mux; Noto fonts so burned
# subtitles render Korean/Vietnamese correctly.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg rubberband-cli fonts-noto-core fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-worker.txt ./
# CPU wheels: Lightsail instances have no GPU.
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu \
    -r requirements-worker.txt

# Demucs downloads model weights on first run; keep them on a volume.
ENV TORCH_HOME=/srv/dubby/models \
    WORKER_HEARTBEAT_FILE=/tmp/dubby-worker-heartbeat
RUN mkdir -p /srv/dubby/models && chown -R dubby:dubby /srv/dubby

USER dubby

# Healthy while the heartbeat file is younger than 60 seconds.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,sys,time; p=os.environ['WORKER_HEARTBEAT_FILE']; \
sys.exit(0 if os.path.exists(p) and time.time() - os.path.getmtime(p) < 60 else 1)"

CMD ["python", "-m", "app.worker.runner"]
