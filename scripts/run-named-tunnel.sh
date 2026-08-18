#!/usr/bin/env bash
# Run the named Cloudflare tunnel (api.dubbyai.com → local :8000).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CONFIG="${DUBBY_TUNNEL_CONFIG:-$ROOT/infra/cloudflared/config.yml}"
CF=""

if [[ ! -f "$CONFIG" ]]; then
  echo "Missing $CONFIG"
  echo "Run: bash scripts/setup-named-tunnel.sh"
  exit 1
fi

if [[ -x /tmp/cloudflared.exe ]]; then CF=/tmp/cloudflared.exe
elif [[ -n "${LOCALAPPDATA:-}" && -x "$LOCALAPPDATA/Temp/cloudflared.exe" ]]; then
  CF="$LOCALAPPDATA/Temp/cloudflared.exe"
elif command -v cloudflared >/dev/null 2>&1; then CF="$(command -v cloudflared)"
else
  echo "cloudflared not found"; exit 1
fi

if ! curl -sS -m 3 "http://127.0.0.1:8000/healthz" >/dev/null 2>&1; then
  echo "WARN: nothing healthy on :8000 — start uvicorn first"
fi

echo "Starting named tunnel with $CONFIG (http2, ipv4)"
exec "$CF" tunnel --config "$CONFIG" --protocol http2 --edge-ip-version 4 run
