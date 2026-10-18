"""Create YTDLP_HOME_FETCH_TOKEN in api/.env when missing. Do not commit .env."""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "api" / ".env"


def main() -> int:
    text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    if "YTDLP_HOME_FETCH_TOKEN=" in text:
        print("TOKEN_OK")
        return 0
    token = secrets.token_urlsafe(32)
    with ENV_FILE.open("a", encoding="utf-8") as handle:
        if text and not text.endswith("\n"):
            handle.write("\n")
        handle.write(
            "\n# Home YouTube fetch — copy this token to Lightsail infra/.env\n"
            f"YTDLP_HOME_FETCH_TOKEN={token}\n"
        )
    print("WROTE_TOKEN")
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
