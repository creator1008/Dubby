# Dubby infrastructure & deployment guide

The stack is intentionally small:

| Piece | Where | What |
| --- | --- | --- |
| Web UI | Cloudflare Pages | Next.js static export (`out/`, already wired via `wrangler`) |
| API | Lightsail (Docker) | FastAPI (`api/`, image target `api`) behind Caddy TLS |
| Worker | Lightsail (Docker) | Queue worker with ffmpeg + Demucs (image target `worker`) |
| Database & Auth | Supabase | Postgres + Auth; schema in `supabase/migrations/` |
| Object storage | Cloudflare R2 | Source videos and rendered outputs, presigned multipart uploads |

## 1. Supabase

1. Create a project at supabase.com.
2. Apply the migration:

   ```bash
   supabase link --project-ref <PROJECT_REF>
   supabase db push
   ```

   (or paste `supabase/migrations/20260717000000_init_dubby_core.sql` into the SQL editor.)
3. Collect for the API `.env`:
   - `SUPABASE_URL` — Settings > API > Project URL
   - `SUPABASE_JWT_SECRET` — Settings > API > JWT Settings (leave empty if the
     project uses the newer asymmetric keys; the API then verifies via JWKS)
   - `DATABASE_URL` — Settings > Database > Connection pooler, **session mode**
   - `SUPABASE_SERVICE_ROLE_KEY` — only needed when `DB_BACKEND=supabase_rest`

The migrations create `profiles`, `projects`, `segments`, `jobs`,
`credit_ledger`, `waitlist` with RLS, indexes, `updated_at` triggers, a
new-user trigger (profile + 10-minute signup credit), and the service RPCs
(`enqueue_job`, `claim_next_job`, `fail_stale_jobs`, `update_segment_texts`,
`credit_balance`, `replace_segments`).

## 2. Cloudflare R2

1. R2 > Create bucket (e.g. `dubby`). No public access needed.
2. Manage R2 API Tokens > create a token with **Object Read & Write** scoped
   to the bucket. Collect `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`,
   `R2_SECRET_ACCESS_KEY`.
3. Add a CORS policy to the bucket so the browser can PUT presigned parts:

   ```json
   [
     {
       "AllowedOrigins": ["https://<your-pages-domain>", "http://localhost:3000"],
       "AllowedMethods": ["PUT", "GET"],
       "AllowedHeaders": ["content-type"],
       "ExposeHeaders": ["etag"],
       "MaxAgeSeconds": 3600
     }
   ]
   ```

   `ExposeHeaders: etag` is required — the client must read each part's ETag
   to complete a multipart upload.

## 3. Lightsail

Recommended: Ubuntu 24.04, 4 GB RAM / 2 vCPU minimum (Demucs on CPU), static
IP, firewall open on 22/80/443. Point the API domain (e.g. `api.dubby.app`)
at the static IP.

```bash
# on the instance
curl -fsSLO https://raw.githubusercontent.com/<you>/<repo>/main/infra/scripts/bootstrap-lightsail.sh
bash bootstrap-lightsail.sh        # Docker + compose + 4G swap
# log out / in (docker group), then:
git clone <repo> dubby && cd dubby/infra
cp ../api/.env.example .env        # fill in real values
docker compose up -d --build
```

Subsequent deploys: `bash infra/scripts/deploy.sh` (pull, rebuild, roll,
health-gate).

Notes:

- `WORKER_CONCURRENCY=1` (default) is correct for a small instance — one
  Demucs run can use several GB of RAM. Raise it only on bigger hardware.
- Demucs model weights download on first dub job and persist in the
  `demucs-models` volume.
- The pipeline needs `OPENAI_API_KEY` (Whisper ASR + GPT translation) and
  `ELEVENLABS_API_KEY` (voice clone + TTS) in `infra/.env`. Set
  `ELEVENLABS_VOICE_ID` to skip per-project Instant Voice Clone and dub with
  one fixed voice. All pipeline knobs (limits, models, Demucs device, retry
  counts) are listed in `api/.env.example`.
- Multi-speaker mode is opt-in. Set `DIARIZATION_PROVIDER=pyannote` and
  `PYANNOTE_AUTH_TOKEN`; the worker image includes pyannote and Rubber Band.
  The default remains single-speaker and requires neither.
- Premium lip sync is a separately charged `lipsync` queue job. Set
  `LIPSYNC_PROVIDER=sync` and `SYNC_API_KEY` for Sync Labs. The worker creates
  idempotent jobs, polls to a bounded timeout, and stores results in R2.
  `disabled` returns `feature_unavailable`; `mock` supports secret-free tests.
- `PIPELINE_MODE` must stay `real` in production (the config layer rejects
  `mock` when `APP_ENV=production`). `mock` exists for development and CI:
  it replaces ffmpeg/Demucs/OpenAI/ElevenLabs with offline deterministic
  stand-ins.
- The worker drains gracefully on deploy (`stop_grace_period: 5m`); stale
  `running` jobs are auto-failed by the reaper after
  `WORKER_JOB_TIMEOUT_SECONDS`.
- Caddy answers `https://$DUBBY_API_DOMAIN` and proxies to the API container;
  certificates are automatic via Let's Encrypt (`ACME_EMAIL`).

## 4. Cloudflare Pages (UI)

Already configured (`npm run pages:deploy`). Set the UI's API base URL env to
`https://<DUBBY_API_DOMAIN>` and add the Pages domain to `CORS_ORIGINS` in
`infra/.env`.

## 5. Local development

```bash
cd api
python -m venv .venv && . .venv/Scripts/activate   # or bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                                # fill in dev values
uvicorn app.main:app --reload --port 8000
# worker (separate shell):
python -m app.worker.runner
# tests (no external services needed):
python -m pytest
```

`GET /healthz` never touches external services; `GET /readyz` verifies DB
connectivity and returns 503 when degraded.
