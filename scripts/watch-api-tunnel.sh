#!/usr/bin/env bash
# Watch the published Cloudflare quick tunnel. When /healthz fails, restart
# cloudflared and republish public/api-origin.json so mobile clients recover.
#
# SAFETY: does nothing unless you pass --run
#   bash scripts/watch-api-tunnel.sh --run
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ "${1:-}" != "--run" ]]; then
  echo "Refusing to start without --run (prevents accidental tunnel thrash)."
  echo "Usage: bash scripts/watch-api-tunnel.sh --run"
  exit 1
fi

INTERVAL="${DUBBY_TUNNEL_WATCH_INTERVAL:-120}"
FAILS_BEFORE_RESTART="${DUBBY_TUNNEL_FAILS_BEFORE_RESTART:-3}"
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
  body="$(curl --resolve "$host:443:104.16.230.132" -sS -m 12 "$url/healthz" 2>/dev/null || true)"
  echo "$body" | grep -q '"status"' && return 0
  body="$(curl -sS -m 12 "$url/healthz" 2>/dev/null || true)"
  echo "$body" | grep -q '"status"' && return 0
  return 1
}

current_url() {
  python - <<'PY'
import json
from pathlib import Path
p = Path("public/api-origin.json")
try:
    print(json.loads(p.read_text(encoding="utf-8")).get("api_origin", "").strip())
except Exception:
    print("")
PY
}

fails=0
while true; do
  url="$(current_url)"
  if [[ -z "$url" ]]; then
    echo "$(date -u +%H:%M:%S) no api-origin.json — starting tunnel once"
    bash "$ROOT/scripts/keep-api-tunnel.sh" || true
    fails=0
  elif tunnel_health "$url"; then
    echo "$(date -u +%H:%M:%S) ok $url"
    fails=0
  else
    fails=$((fails + 1))
    echo "$(date -u +%H:%M:%S) DEAD ($fails/$FAILS_BEFORE_RESTART) $url"
    if [[ "$fails" -ge "$FAILS_BEFORE_RESTART" ]]; then
      echo "$(date -u +%H:%M:%S) restarting tunnel"
      bash "$ROOT/scripts/keep-api-tunnel.sh" || true
      fails=0
      # Back off after a restart to avoid Cloudflare quick-tunnel rate limits.
      sleep 180
      continue
    fi
  fi
  sleep "$INTERVAL"
done
