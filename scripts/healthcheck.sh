#!/usr/bin/env bash
# Exit 0 when the stack is healthy: the API answers /api/health from inside its container and no
# long-running service container has exited. Retries for up to HEALTH_TIMEOUT seconds (default 150).
set -uo pipefail
cd "$(dirname "$0")/.."
deadline=$(( $(date +%s) + ${HEALTH_TIMEOUT:-150} ))
while :; do
  ok=1
  sudo -n docker compose exec -T api python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=5).status == 200 else 1)" >/dev/null 2>&1 || ok=0
  for svc in db api web collector etf tips backup; do
    state=$(sudo -n docker compose ps --format '{{.State}}' "$svc" 2>/dev/null | head -1)
    [ "$state" = "running" ] || ok=0
  done
  [ $ok = 1 ] && exit 0
  [ "$(date +%s)" -ge "$deadline" ] && exit 1
  sleep "${HEALTH_INTERVAL:-10}"
done
