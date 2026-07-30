#!/usr/bin/env python3
"""Apply credit_ledger FK-null exception so project DELETE works.

Requires SUPABASE_ACCESS_TOKEN (https://supabase.com/dashboard/account/tokens).

  set SUPABASE_ACCESS_TOKEN=sbp_...
  python scripts/apply-credit-ledger-delete-fix.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_REF = os.environ.get("SUPABASE_PROJECT_REF", "osgeahuvaazujjcwtxum")
SQL_PATH = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "migrations"
    / "20260730000000_credit_ledger_allow_fk_null.sql"
)


def main() -> int:
    token = os.environ.get("SUPABASE_ACCESS_TOKEN", "").strip()
    if not token:
        print("Set SUPABASE_ACCESS_TOKEN first.", file=sys.stderr)
        return 1
    sql = SQL_PATH.read_text(encoding="utf-8")
    url = f"https://api.supabase.com/v1/projects/{PROJECT_REF}/database/query"
    req = urllib.request.Request(
        url,
        data=json.dumps({"query": sql}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "dubby-migrate",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", "replace")
            print("ok", resp.status, body[:500])
    except urllib.error.HTTPError as err:
        print(err.read().decode("utf-8", "replace"), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
