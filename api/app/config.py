"""Application settings loaded from environment variables.

Every deployment-specific value lives here. Nothing in this module (or the
rest of the codebase) contains real credentials; see `api/.env.example`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Runtime -----------------------------------------------------------
    app_env: Literal["local", "staging", "production"] = "local"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    # Comma-separated list of allowed browser origins (Cloudflare Pages UI).
    cors_origins: str = "http://localhost:3000"

    # --- Supabase ----------------------------------------------------------
    supabase_url: str = ""  # e.g. https://<project-ref>.supabase.co
    # Legacy symmetric JWT secret (Settings > API > JWT secret). When set,
    # HS256 verification is used. Leave empty to verify against the project's
    # JWKS endpoint (new asymmetric signing keys).
    supabase_jwt_secret: str = ""
    # Service-role key; only required for the "supabase_rest" DB backend.
    supabase_service_role_key: str = ""
    supabase_jwt_audience: str = "authenticated"

    # --- Database ----------------------------------------------------------
    # "postgres" -> direct asyncpg pool (recommended on Lightsail; use the
    #               Supabase connection pooler URI in session mode).
    # "supabase_rest" -> PostgREST over HTTPS with the service-role key
    #               (no direct DB connectivity required).
    db_backend: Literal["postgres", "supabase_rest"] = "postgres"
    database_url: str = ""  # postgresql://user:pass@host:5432/postgres
    db_pool_min_size: int = 1
    db_pool_max_size: int = 5

    # --- Cloudflare R2 (S3-compatible) --------------------------------------
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "dubby"
    # Override for testing against MinIO etc. Defaults to the Cloudflare
    # endpoint derived from the account id.
    r2_endpoint_url: str = ""
    r2_region: str = "auto"
    presign_expires_seconds: int = 3600
    download_expires_seconds: int = Field(default=300, ge=60, le=3600)
    # Uploads above this size must use multipart (S3 minimum part is 5 MiB).
    multipart_part_size_bytes: int = 64 * 1024 * 1024
    max_upload_bytes: int = 4 * 1024 * 1024 * 1024  # 4 GiB safety cap

    # --- Worker -------------------------------------------------------------
    # Number of jobs a single worker process runs at once. Demucs is memory
    # heavy, so this stays 1 on a small Lightsail instance.
    worker_concurrency: int = Field(default=1, ge=1, le=8)
    worker_poll_interval_seconds: float = 3.0
    # Mark running jobs as failed if they have not heartbeat within this time.
    worker_job_timeout_seconds: int = 3600

    # --- Pipeline -------------------------------------------------------------
    # "real" runs ffmpeg/Demucs/OpenAI/ElevenLabs. "mock" replaces every
    # external dependency with deterministic local stand-ins and exists ONLY
    # for development and tests; production refuses to boot with it.
    # ``auto`` is accepted for the local provider-enabled workflow and uses
    # the real engine; production still requires the explicit ``real`` value.
    pipeline_mode: Literal["real", "mock", "auto"] = "real"
    # Scratch parent directory for per-job temp dirs (empty -> system temp).
    scratch_dir: str = ""
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    # Source validation limits (MVP): <= 10 minutes, <= 500 MB.
    max_source_duration_seconds: float = 600.0
    max_source_bytes: int = 500 * 1024 * 1024
    # ffprobe format names accepted for source videos (comma separated).
    allowed_source_containers: str = "mp4,mov,m4a,3gp,3g2,mj2,matroska,webm"
    # Transient-step retries (network APIs, R2 transfers).
    pipeline_step_retries: int = Field(default=2, ge=0, le=10)
    pipeline_retry_backoff_seconds: float = 2.0
    # Re-touch the job heartbeat during long subprocess steps.
    pipeline_heartbeat_seconds: float = 20.0

    # --- Demucs stem split -----------------------------------------------------
    demucs_model: str = "htdemucs_ft"
    demucs_device: str = "cpu"  # cpu | cuda
    demucs_jobs: int = Field(default=1, ge=1, le=16)

    # --- OpenAI (Whisper ASR + GPT translation) --------------------------------
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    whisper_model: str = "whisper-1"
    translation_model: str = "gpt-4o-mini"
    # Segments per translation request; keeps prompts well under limits.
    translation_batch_size: int = Field(default=40, ge=1, le=200)
    translation_timing_tolerance: float = Field(default=0.15, ge=0.0, le=1.0)
    speech_segment_max_seconds: float = Field(default=6.0, ge=1.0, le=30.0)

    # --- ElevenLabs (voice clone + TTS) ----------------------------------------
    elevenlabs_api_key: str = ""
    elevenlabs_base_url: str = "https://api.elevenlabs.io"
    elevenlabs_tts_model: str = "eleven_multilingual_v2"
    # When set, skip Instant Voice Clone and always use this voice.
    elevenlabs_voice_id: str = ""
    # Seconds of the vocals stem sent as the IVC reference sample.
    voice_clone_sample_seconds: float = 60.0
    # Tempo policy. Rubber Band is preferred outside atempo's high-quality range.
    tts_max_speedup: float = Field(default=1.6, ge=1.0, le=4.0)
    tts_min_tempo: float = Field(default=0.85, ge=0.5, le=1.0)
    tts_atempo_max: float = Field(default=1.5, ge=1.0, le=2.0)
    rubberband_path: str = ""

    # --- Speaker diarization -------------------------------------------------
    diarization_provider: Literal["disabled", "mock", "openai", "pyannote"] = "openai"
    diarization_model: str = "gpt-4o-transcribe-diarize"
    pyannote_auth_token: str = ""
    pyannote_model: str = "pyannote/speaker-diarization-3.1"
    speaker_sample_seconds: float = Field(default=30.0, ge=3.0, le=120.0)

    # --- Sync Labs premium lip sync ------------------------------------------
    lipsync_provider: Literal["disabled", "mock", "sync"] = "disabled"
    sync_api_key: str = ""
    sync_base_url: str = "https://api.sync.so"
    sync_model: str = "lipsync-2"
    sync_timeout_seconds: float = Field(default=900.0, ge=30.0, le=3600.0)
    sync_poll_interval_seconds: float = Field(default=5.0, ge=0.1, le=60.0)
    lipsync_cogs_minutes_multiplier: float = Field(default=2.0, gt=0)

    # --- Product rules ------------------------------------------------------
    signup_credit_minutes: int = 10
    dub_cogs_minutes_multiplier: float = Field(default=1.0, gt=0)

    # --- Stripe -------------------------------------------------------------
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_subscription_price_id: str = ""
    stripe_credit_pack_price_id: str = ""
    stripe_subscription_minutes: int = Field(default=60, gt=0)
    stripe_credit_pack_minutes: int = Field(default=30, gt=0)
    checkout_success_url: str = "http://localhost:3000/app/billing/?checkout=success"
    checkout_cancel_url: str = "http://localhost:3000/app/billing/?checkout=cancelled"

    # --- RevenueCat ---------------------------------------------------------
    # Exact Authorization header configured in the RevenueCat webhook.
    revenuecat_webhook_auth_header: str = ""
    # Comma-separated product/entitlement mappings, e.g. starter_monthly=60.
    revenuecat_product_credit_minutes: str = ""
    revenuecat_entitlement_credit_minutes: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def r2_endpoint(self) -> str:
        if self.r2_endpoint_url:
            return self.r2_endpoint_url
        return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"

    @property
    def supabase_jwks_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"

    @property
    def allowed_source_container_set(self) -> frozenset[str]:
        return frozenset(
            c.strip().lower()
            for c in self.allowed_source_containers.split(",")
            if c.strip()
        )

    @model_validator(mode="after")
    def _check_production_requirements(self) -> "Settings":
        """Fail fast on misconfigured production deployments.

        Local/dev processes may boot without external services (health and
        import tests), but production must be fully configured.
        """
        if self.app_env == "production":
            missing: list[str] = []
            if not self.supabase_url:
                missing.append("SUPABASE_URL")
            if self.db_backend == "postgres" and not self.database_url:
                missing.append("DATABASE_URL")
            if self.db_backend == "supabase_rest" and not self.supabase_service_role_key:
                missing.append("SUPABASE_SERVICE_ROLE_KEY")
            if not (self.r2_account_id and self.r2_access_key_id and self.r2_secret_access_key):
                missing.append("R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY")
            if not (
                self.stripe_secret_key
                and self.stripe_webhook_secret
                and self.stripe_subscription_price_id
                and self.stripe_credit_pack_price_id
            ):
                missing.append(
                    "STRIPE_SECRET_KEY/STRIPE_WEBHOOK_SECRET/STRIPE_*_PRICE_ID"
                )
            if self.diarization_provider == "pyannote" and not self.pyannote_auth_token:
                missing.append("PYANNOTE_AUTH_TOKEN")
            if self.lipsync_provider == "sync" and not self.sync_api_key:
                missing.append("SYNC_API_KEY")
            if self.pipeline_mode != "real":
                raise ValueError("APP_ENV=production requires PIPELINE_MODE=real")
            if missing:
                raise ValueError(
                    "APP_ENV=production requires: " + ", ".join(missing)
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
