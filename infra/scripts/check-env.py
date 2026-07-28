#!/usr/bin/env python3
"""Validate Dubby environment files without printing secrets."""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def load_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, rest = line.partition("=")
        value = rest.split("#", 1)[0].strip().strip('"').strip("'")
        out[key.strip()] = value
    return out


def status(name: str, ok: bool, detail: str) -> None:
    mark = "OK" if ok else "FAIL"
    print(f"[{mark}] {name}: {detail}")


def main() -> int:
    root = load_env(REPO / ".env")
    api = load_env(REPO / "api" / ".env")
    infra = load_env(REPO / "infra" / ".env")
    merged = {**infra, **api, **root}

    print("=== Files ===")
    status("root .env", (REPO / ".env").is_file(), "found" if (REPO / ".env").is_file() else "missing")
    status("api/.env", (REPO / "api" / ".env").is_file(), "found" if (REPO / "api" / ".env").is_file() else "missing")
    status("infra/.env", (REPO / "infra" / ".env").is_file(), "found" if (REPO / "infra" / ".env").is_file() else "missing")

    print("\n=== Frontend ===")
    local = (root.get("NEXT_PUBLIC_LOCAL_PIPELINE") or "").lower() in {"1", "true", "yes"}
    status("Supabase public URL", bool(root.get("NEXT_PUBLIC_SUPABASE_URL")), "set" if root.get("NEXT_PUBLIC_SUPABASE_URL") else "empty")
    status("Supabase anon key", bool(root.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")), "set" if root.get("NEXT_PUBLIC_SUPABASE_ANON_KEY") else "empty")
    print(f"     mode: {'local pipeline (8002)' if local else 'SaaS API'}")
    print(f"     NEXT_PUBLIC_API_ORIGIN: {root.get('NEXT_PUBLIC_API_ORIGIN') or '(empty)'}")

    print("\n=== API / worker ===")
    try:
        os.chdir(REPO / "api")
        sys.path.insert(0, str(REPO / "api"))
        for key, value in api.items():
            os.environ[key] = value
        from app.config import Settings

        settings = Settings()
        status("Settings parse", True, f"app_env={settings.app_env}, pipeline={settings.pipeline_mode}")
    except Exception as exc:
        status("Settings parse", False, str(exc))
        settings = None

    if settings:
        status("SUPABASE_URL", bool(settings.supabase_url), "set" if settings.supabase_url else "empty")
        db_backend = settings.db_backend
        if db_backend == "postgres":
            status("DATABASE_URL", bool(settings.database_url), "set" if settings.database_url else "empty (required)")
        else:
            status(
                "SUPABASE_SERVICE_ROLE_KEY",
                bool(settings.supabase_service_role_key),
                "set" if settings.supabase_service_role_key else "empty (required for supabase_rest)",
            )
        status("R2 credentials", bool(settings.r2_account_id and settings.r2_access_key_id and settings.r2_secret_access_key), "set" if settings.r2_account_id else "empty")
        status("OpenAI key", bool(settings.openai_api_key), "set" if settings.openai_api_key else "empty")
        status("ElevenLabs key", bool(settings.elevenlabs_api_key), "set" if settings.elevenlabs_api_key else "empty")

    sb = merged.get("NEXT_PUBLIC_SUPABASE_URL") or merged.get("SUPABASE_URL", "")
    if sb:
        try:
            req = urllib.request.Request(
                sb.rstrip("/") + "/auth/v1/health",
                headers={"User-Agent": "DubbyEnvCheck/1.0"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                status("Supabase Auth reachability", True, str(resp.status))
        except urllib.error.HTTPError as exc:
            status("Supabase Auth reachability", True, str(exc.code))
        except Exception as exc:
            status("Supabase Auth reachability", False, type(exc).__name__)

    print("\n=== Next steps if anything failed ===")
    if not merged.get("DATABASE_URL") and merged.get("DB_BACKEND", "postgres") == "postgres":
        print("- Supabase Dashboard > Project Settings > Database > Connection string")
        print("  Choose URI + Session pooler, paste into api/.env as DATABASE_URL=")
    if not merged.get("SUPABASE_SERVICE_ROLE_KEY") and merged.get("DB_BACKEND") == "supabase_rest":
        print("- Supabase Dashboard > Project Settings > API > service_role key")
        print("  Paste into api/.env as SUPABASE_SERVICE_ROLE_KEY=")
    if local and not merged.get("NEXT_PUBLIC_API_ORIGIN"):
        print("- SaaS test: set NEXT_PUBLIC_API_ORIGIN=http://localhost:8000 and unset LOCAL_PIPELINE")
    if not merged.get("DUBBY_API_DOMAIN"):
        print("- Deploy: set DUBBY_API_DOMAIN and ACME_EMAIL in infra/.env before docker compose")

    failed = not (REPO / "api" / ".env").is_file()
    if settings:
        if settings.db_backend == "postgres" and not settings.database_url:
            failed = True
        if settings.db_backend == "supabase_rest" and not settings.supabase_service_role_key:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
