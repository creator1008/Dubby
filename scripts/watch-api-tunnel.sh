#!/usr/bin/env bash
# Watch the published Cloudflare quick tunnel. When /healthz fails, restart
# cloudflared and republish public/api-origin.json so mobile clients recover.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

INTERVAL="${DUBBY_TUNNEL_WATCH_INTERVAL:-30}"
ORIGIN_FILE="$ROOT/public/api-origin.json"

echo "Watching API tunnel every ${INTERVAL}s (Ctrl+C to stop)"

tunnel_health() {
  local url="$1"
  local host="${url#https://}"
  local a4=""
  a4="$(nslookup "$host" 1.1.1.1 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | grep -vE '^(1\.1\.1\.1|8\.8\.8\.8)$' | head -1 || true)"
  local body=""
  if [[ -n "$a4" ]]; then
    body="$(curl --resolve "$host:443:$a4" -sS -m 12 "$url/healthz" 2>/dev/null || true)"
    echo "$body" | grep -q '"status"' && return 0
  fi
  body="$(curl -sS -m 12 "$url/healthz" 2>/dev/null || true)"
  echo "$body" | grep -q '"status"' && return 0
  body="$(curl -6 -sS -m 12 "$url/healthz" 2>/dev/null || true)"
  echo "$body" | grep -q '"status"' && return 0
  return 1
}

current_url() {
  python - <<PY
import json
from pathlib import Path
p = Path(r"$ORIGIN_FILE")
try:
    print(json.loads(p.read_text(encoding="utf-8")).get("api_origin","").strip())
except Exception:
    print("")
PY
}

while true; do
  url="$(current_url)"
  if [[ -z "$url" ]]; then
    echo "$(date -u +%H:%M:%S) no api-origin.json — starting tunnel"
    bash "$ROOT/scripts/keep-api-tunnel.sh" || true
  elif tunnel_health "$url"; then
    echo "$(date -u +%H:%M:%S) ok $url"
  else
    echo "$(date -u +%H:%M:%S) DEAD $url — restarting tunnel"
    bash "$ROOT/scripts/keep-api-tunnel.sh" || true
  fi
  sleep "$INTERVAL"
done
