#!/usr/bin/env bash
# Route fetch.dubbyai.com → this PC's home-fetch process on :8003
# and ensure api/.env has YTDLP_HOME_FETCH_TOKEN.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TUNNEL_NAME="${DUBBY_TUNNEL_NAME:-dubby-api}"
FETCH_HOSTNAME="${DUBBY_FETCH_HOSTNAME:-fetch.dubbyai.com}"
CONFIG_FILE="$ROOT/infra/cloudflared/config.yml"
ENV_FILE="$ROOT/api/.env"
CF=""

find_cloudflared() {
  if [[ -x /tmp/cloudflared.exe ]]; then CF=/tmp/cloudflared.exe; return; fi
  if [[ -n "${LOCALAPPDATA:-}" && -x "$LOCALAPPDATA/Temp/cloudflared.exe" ]]; then
    CF="$LOCALAPPDATA/Temp/cloudflared.exe"
    return
  fi
  if command -v cloudflared >/dev/null 2>&1; then CF="$(command -v cloudflared)"; return; fi
  echo "cloudflared not found. Run bash scripts/setup-named-tunnel.sh first."
  exit 1
}

find_cloudflared

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Missing $CONFIG_FILE — run bash scripts/setup-named-tunnel.sh first."
  exit 1
fi

echo "1) Route DNS $FETCH_HOSTNAME → tunnel $TUNNEL_NAME …"
"$CF" tunnel route dns -f "$TUNNEL_NAME" "$FETCH_HOSTNAME" || \
  "$CF" tunnel route dns "$TUNNEL_NAME" "$FETCH_HOSTNAME" || true

python - "$CONFIG_FILE" "$FETCH_HOSTNAME" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
hostname = sys.argv[2]
text = path.read_text(encoding="utf-8")
if hostname in text:
    print(f"config already has {hostname}")
    raise SystemExit(0)
needle = "  - service: http_status:404"
block = (
    f"  - hostname: {hostname}\n"
    "    service: http://127.0.0.1:8003\n"
    "    originRequest:\n"
    "      connectTimeout: 30s\n"
    "      keepAliveTimeout: 90s\n"
    "      noHappyEyeballs: false\n"
)
if needle not in text:
    print("ERROR: could not find catch-all ingress in config.yml")
    raise SystemExit(1)
path.write_text(text.replace(needle, block + needle, 1), encoding="utf-8")
print(f"added {hostname} → 127.0.0.1:8003")
PY

python - "$ENV_FILE" <<'PY'
from pathlib import Path
import secrets
import sys
path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8") if path.exists() else ""
if "YTDLP_HOME_FETCH_TOKEN=" in text:
    print("api/.env already has YTDLP_HOME_FETCH_TOKEN")
    raise SystemExit(0)
token = secrets.token_urlsafe(32)
with path.open("a", encoding="utf-8") as handle:
    if text and not text.endswith("\n"):
        handle.write("\n")
    handle.write(
        "\n# Home YouTube fetch — copy this token to Lightsail infra/.env\n"
        f"YTDLP_HOME_FETCH_TOKEN={token}\n"
    )
print("WROTE_TOKEN")
print(token)
PY

echo
echo "Next:"
echo "  1) start-home-fetch.bat   (this PC, port 8003)"
echo "  2) start-tunnel.bat       (restart if it was already running)"
echo "  3) On Lightsail infra/.env:"
echo "       YTDLP_HOME_FETCH_URL=https://$FETCH_HOSTNAME/v1/internal/ytdlp-fetch"
echo "       YTDLP_HOME_FETCH_TOKEN=<same token as api/.env>"
echo "     then: cd infra && docker compose up -d"
echo "  4) Keep this PC awake while using YouTube links"
echo "  5) Cloudflare WAF: skip Bot Fight for $FETCH_HOSTNAME (same as api)"
