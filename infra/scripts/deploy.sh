#!/usr/bin/env bash
# Deploy/update the Dubby stack on the Lightsail host.
# Run from the repo root or infra/:  bash infra/scripts/deploy.sh
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "infra/.env missing — copy api/.env.example and fill in real values." >&2
  exit 1
fi

echo "==> Pulling latest code"
git pull --ff-only

echo "==> Building and rolling containers"
docker compose build --pull
docker compose up -d --remove-orphans

echo "==> Waiting for API health"
for _ in $(seq 1 30); do
  if docker compose exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" \
    2>/dev/null; then
    echo "API healthy."
    docker compose ps
    exit 0
  fi
  sleep 2
done

echo "API failed to become healthy; recent logs:" >&2
docker compose logs --tail 50 api >&2
exit 1
