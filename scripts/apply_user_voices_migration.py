"""Apply user_voices migration using DATABASE_URL from api/.env (no secrets printed)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
SQL_PATH = ROOT / "supabase" / "migrations" / "20260806000000_user_voices.sql"


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


async def apply_via_postgres(url: str, sql: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(url, statement_cache_size=0)
    try:
        exists = await conn.fetchval(
            "select to_regclass('public.user_voices') is not null"
        )
        print("table_exists_before=", bool(exists))
        if not exists:
            await conn.execute(sql)
            print("migration_applied=True")
        else:
            print("migration_applied=False (already present)")
        exists_after = await conn.fetchval(
            "select to_regclass('public.user_voices') is not null"
        )
        print("table_exists_after=", bool(exists_after))
    finally:
        await conn.close()


async def probe_via_rest(supabase_url: str, service_key: str) -> str:
    import httpx

    base = supabase_url.rstrip("/") + "/rest/v1"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{base}/user_voices",
            params={"select": "id", "limit": "1"},
            headers={
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
            },
        )
    return f"status={resp.status_code} body={(resp.text or '')[:180]}"


async def main() -> int:
    # Prefer api/.env last so empty infra placeholders do not wipe secrets.
    env: dict[str, str] = {}
    env.update(load_env(ROOT / ".env"))
    env.update(load_env(ROOT / "infra" / ".env"))
    env.update(load_env(API / ".env"))

    url = (env.get("DATABASE_URL") or "").strip()
    supabase_url = (env.get("SUPABASE_URL") or "").strip()
    service_key = (env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    backend = (env.get("DB_BACKEND") or "").strip()
    sql = SQL_PATH.read_text(encoding="utf-8")

    print("backend=", backend or "(unset)")
    print("has_database_url=", bool(url))
    print("has_supabase_url=", bool(supabase_url))
    print("has_service_role=", bool(service_key))

    if supabase_url and service_key:
        probe = await probe_via_rest(supabase_url, service_key)
        print("rest_probe_before=", probe)
        if probe.startswith("status=200"):
            print("migration_applied=False (table already reachable via PostgREST)")
            return 0

    if url:
        await apply_via_postgres(url, sql)
        if supabase_url and service_key:
            print("rest_probe_after=", await probe_via_rest(supabase_url, service_key))
        return 0

    print(
        "NO_DATABASE_URL: open Supabase SQL Editor and run "
        "supabase/migrations/20260806000000_user_voices.sql"
    )
    return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
