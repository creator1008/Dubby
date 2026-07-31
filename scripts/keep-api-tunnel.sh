#!/usr/bin/env bash
# Keep a Cloudflare quick tunnel pointed at local uvicorn (:8000) and publish
# the live URL to public/api-origin.json (+ GitHub Actions secret) so mobile
# clients can refresh without a manual ?api= link.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${DUBBY_API_PORT:-8000}"
LOG="${DUBBY_TUNNEL_LOG:-/tmp/dubby-cf-tunnel.log}"
PID_FILE="${DUBBY_TUNNEL_PID:-/tmp/dubby-cf-tunnel.pid}"
ORIGIN_FILE="$ROOT/public/api-origin.json"
CF=""

find_cloudflared() {
  if [[ -x /tmp/cloudflared.exe ]]; then CF=/tmp/cloudflared.exe; return; fi
  if [[ -n "${LOCALAPPDATA:-}" && -x "$LOCALAPPDATA/Temp/cloudflared.exe" ]]; then
    CF="$LOCALAPPDATA/Temp/cloudflared.exe"
    return
  fi
  if command -v cloudflared >/dev/null 2>&1; then CF="$(command -v cloudflared)"; return; fi
  echo "Downloading cloudflared…"
  curl -sL -o /tmp/cloudflared.exe \
    https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
  CF=/tmp/cloudflared.exe
}

ensure_api() {
  if curl -sS -m 3 "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
    return 0
  fi
  echo "ERROR: nothing healthy on :${PORT}. Start uvicorn first:"
  echo "  cd api && .venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"
  exit 1
}

stop_tunnel() {
  if [[ -f "$PID_FILE" ]]; then
    local old
    old="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "$old" ]]; then
      kill "$old" 2>/dev/null || taskkill //PID "$old" //F 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
  fi
  taskkill //F //IM cloudflared.exe 2>/dev/null || true
  sleep 1
}

start_tunnel() {
  rm -f "$LOG"
  "$CF" tunnel --protocol http2 --url "http://127.0.0.1:${PORT}" >"$LOG" 2>&1 &
  echo $! >"$PID_FILE"
  local url=""
  for _ in $(seq 1 30); do
    sleep 1
    if command -v rg >/dev/null 2>&1; then
      url="$(rg -a -o 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" 2>/dev/null | head -1 || true)"
    else
      url="$(grep -aoE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" 2>/dev/null | head -1 || true)"
    fi
    [[ -n "$url" ]] && break
  done
  if [[ -z "$url" ]]; then
    echo "ERROR: tunnel URL not found"
    tail -n 40 "$LOG" || true
    exit 1
  fi
  local code=000
  local host="${url#https://}"
  for _ in $(seq 1 25); do
    # Local resolvers (ISP/router) often lag or omit A records for new
    # trycloudflare hostnames — probe via public DNS + --resolve.
    local a4=""
    a4="$(nslookup "$host" 1.1.1.1 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | grep -vE '^(1\.1\.1\.1|8\.8\.8\.8)$' | head -1 || true)"
    if [[ -n "$a4" ]]; then
      code="$(curl --resolve "$host:443:$a4" -sS -m 12 -o /tmp/dubby-hz.json -w '%{http_code}' "$url/healthz" 2>/dev/null || echo 000)"
      if [[ "$code" == "200" ]] && grep -q '"status"' /tmp/dubby-hz.json 2>/dev/null; then
        break
      fi
    fi
    code="$(curl -sS -m 12 -o /tmp/dubby-hz.json -w '%{http_code}' "$url/healthz" 2>/dev/null || echo 000)"
    if [[ "$code" == "200" ]] && grep -q '"status"' /tmp/dubby-hz.json 2>/dev/null; then
      break
    fi
    code="$(curl -6 -sS -m 12 -o /tmp/dubby-hz.json -w '%{http_code}' "$url/healthz" 2>/dev/null || echo 000)"
    if [[ "$code" == "200" ]] && grep -q '"status"' /tmp/dubby-hz.json 2>/dev/null; then
      break
    fi
    sleep 2
  done
  if [[ "$code" != "200" ]] || ! grep -q '"status"' /tmp/dubby-hz.json 2>/dev/null; then
    echo "ERROR: tunnel health failed ($code)"
    tail -n 40 "$LOG" || true
    exit 1
  fi
  echo "$url"
}

write_origin_file() {
  local url="$1"
  local now
  now="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  cat >"$ORIGIN_FILE" <<EOF
{
  "api_origin": "$url",
  "updated_at": "$now"
}
EOF
  # Keep local frontend env in sync for developers (relative path — Windows Python
  # cannot open Git-Bash style /d/Coding/... absolute paths).
  if [[ -f .env ]]; then
    python - <<PY
from pathlib import Path
import re
url = "$url"
p = Path(".env")
text = p.read_text(encoding="utf-8")
text2, n = re.subn(r"(?m)^NEXT_PUBLIC_API_ORIGIN=.*$", f"NEXT_PUBLIC_API_ORIGIN={url}", text, count=1)
if n == 0:
    text2 = text.rstrip() + f"\nNEXT_PUBLIC_API_ORIGIN={url}\n"
p.write_text(text2, encoding="utf-8")
PY
  fi
}

update_github_secret_and_pages() {
  local url="$1"
  python - <<'PY' "$url"
import base64, json, subprocess, sys, urllib.request
tunnel = sys.argv[1]
proc = subprocess.run(
    ["git", "credential", "fill"],
    input="protocol=https\nhost=github.com\n\n",
    text=True,
    capture_output=True,
)
creds = {}
for line in proc.stdout.splitlines():
    if "=" in line:
        k, v = line.split("=", 1)
        creds[k] = v
token = creds.get("password")
if not token:
    print("WARN: no GitHub token; skip secret/dispatch")
    raise SystemExit(0)
headers = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {token}",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "dubby-tunnel",
    "Content-Type": "application/json",
}
repo = "creator1008/Dubby"
req = urllib.request.Request(
    f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
    headers=headers,
)
with urllib.request.urlopen(req) as r:
    pk = json.load(r)
try:
    from nacl import encoding, public
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pynacl", "-q"])
    from nacl import encoding, public
pubkey = public.PublicKey(pk["key"].encode("utf-8"), encoding.Base64Encoder())
encrypted = base64.b64encode(
    public.SealedBox(pubkey).encrypt(tunnel.encode("utf-8"))
).decode("utf-8")
body = json.dumps({"encrypted_value": encrypted, "key_id": pk["key_id"]}).encode()
req = urllib.request.Request(
    f"https://api.github.com/repos/{repo}/actions/secrets/NEXT_PUBLIC_API_ORIGIN",
    data=body,
    headers=headers,
    method="PUT",
)
with urllib.request.urlopen(req) as r:
    print("secret_status", r.status)
PY
}

commit_and_push_origin_file() {
  cd "$ROOT"
  git add public/api-origin.json
  if git diff --cached --quiet; then
    echo "api-origin.json unchanged"
    return
  fi
  git commit -m "$(cat <<'EOF'
Publish current API tunnel origin for mobile clients.

EOF
)" || true
  git push origin HEAD || echo "WARN: push failed; Pages may be stale"
}

PUBLISH_SECRET=0
PUSH_ORIGIN=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --secret) PUBLISH_SECRET=1; shift ;;
    --no-push) PUSH_ORIGIN=0; shift ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

find_cloudflared
ensure_api
stop_tunnel
URL="$(start_tunnel)"
echo "TUNNEL=$URL"
write_origin_file "$URL"
if [[ "$PUBLISH_SECRET" == "1" ]]; then
  update_github_secret_and_pages "$URL"
fi
if [[ "$PUSH_ORIGIN" == "1" ]]; then
  commit_and_push_origin_file
fi
echo "DONE $URL"
echo "Mobile can open: https://creator1008.github.io/Dubby/app/new/?api=$URL"
