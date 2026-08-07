"""Apply project dub_voice_ids migration using DATABASE_URL from env files."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
SQL_PATH = ROOT / "supabase" / "migrations" / "20260807000000_project_dub_voice_ids.sql"


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
            "select exists ("
            "  select 1 from information_schema.columns"
            "  where table_schema = 'public'"
            "    and table_name = 'projects'"
            "    and column_name = 'dub_voice_ids'"
            ")"
        )
        print("column_exists_before=", bool(exists))
        if not exists:
            await conn.execute(sql)
            print("migration_applied=True")
        else:
            print("migration_applied=False (already present)")
        exists_after = await conn.fetchval(
            "select exists ("
            "  select 1 from information_schema.columns"
            "  where table_schema = 'public'"
            "    and table_name = 'projects'"
            "    and column_name = 'dub_voice_ids'"
            ")"
        )
        print("column_exists_after=", bool(exists_after))
    finally:
        await conn.close()


async def main() -> int:
    env: dict[str, str] = {}
    env.update(load_env(ROOT / ".env"))
    env.update(load_env(ROOT / "infra" / ".env"))
    env.update(load_env(API / ".env"))

    url = (env.get("DATABASE_URL") or "").strip()
    if not url:
        print("DATABASE_URL missing", file=sys.stderr)
        return 1
    sql = SQL_PATH.read_text(encoding="utf-8")
    await apply_via_postgres(url, sql)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
