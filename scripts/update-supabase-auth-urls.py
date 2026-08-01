#!/usr/bin/env python3
"""Set Supabase Auth Site URL + redirect allow list for GitHub Pages.

Requires SUPABASE_ACCESS_TOKEN (https://supabase.com/dashboard/account/tokens).

  set SUPABASE_ACCESS_TOKEN=sbp_...
  python scripts/update-supabase-auth-urls.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

PROJECT_REF = os.environ.get("SUPABASE_PROJECT_REF", "osgeahuvaazujjcwtxum")
SITE_URL = os.environ.get(
    "DUBBY_SITE_URL", "https://dubbyai.com"
).rstrip("/")
REDIRECTS = [
    f"{SITE_URL}/auth/callback/",
    f"{SITE_URL}/**",
    "https://creator1008.github.io/Dubby/auth/callback/",
    "https://creator1008.github.io/Dubby/**",
    "http://localhost:3000/auth/callback/",
    "http://localhost:3000/**",
    "http://127.0.0.1:3000/auth/callback/",
    "http://127.0.0.1:3000/**",
]


def main() -> int:
    token = (os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
    if not token:
        print(
            "SUPABASE_ACCESS_TOKEN is required.\n"
            "Create one at https://supabase.com/dashboard/account/tokens",
            file=sys.stderr,
        )
        return 1

    body = {
        "site_url": f"{SITE_URL}/",
        "uri_allow_list": ",".join(REDIRECTS),
    }
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{PROJECT_REF}/config/auth",
        data=json.dumps(body).encode("utf-8"),
        method="PATCH",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code}: {detail}", file=sys.stderr)
        return 1

    print("Updated auth URL config:")
    print("  site_url:", payload.get("site_url"))
    print("  uri_allow_list:", payload.get("uri_allow_list"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
