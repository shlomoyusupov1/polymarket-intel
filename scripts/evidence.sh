#!/usr/bin/env bash
# Production Evidence Report — run on the server:  bash scripts/evidence.sh
# Writes reports/evidence-<UTC timestamp>.md and prints it. Contains no secrets.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p reports
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="reports/evidence-$TS.md"
ADDR=$(grep '^SITE_ADDRESS=' .env | cut -d= -f2)
psql() { sudo docker compose exec -T db psql -U pmintel -d pmintel -AF ' | ' -c "$1" 2>&1; }

{
echo "# Production evidence — $TS"
echo
echo "- Commit: \`$(git rev-parse --short HEAD)\` ($(git log -1 --format=%cI))"
echo "- Host: $(uname -m), $(nproc) CPU, $(free -g | awk '/Mem:/{print $2}') GB RAM, disk free $(df -h / | awk 'NR==2{print $4}')"
echo
echo "## HTTPS (via the public address $ADDR)"
echo '```'
curl -s -o /dev/null -w "unauthenticated /api/health -> HTTP %{http_code}\n" "https://$ADDR/api/health"
curl -s -o /dev/null -w "unauthenticated / -> HTTP %{http_code}\n" "https://$ADDR/"
curl -s -o /dev/null -w "plain http -> HTTP %{http_code} (redirect expected)\n" "http://$ADDR/"
echo | openssl s_client -connect "$ADDR:443" -servername "$ADDR" 2>/dev/null \
  | openssl x509 -noout -issuer -enddate 2>/dev/null
echo '```'
echo
echo "## Containers"
echo '```'
sudo docker compose ps --format "table {{.Service}}\t{{.Status}}"
echo '```'
echo
echo "## API (inside the network, bypassing auth)"
echo '```'
sudo docker compose exec -T api python -c "import urllib.request,json; h=json.load(urllib.request.urlopen('http://localhost:8000/api/health')); print(json.dumps({k:h[k] for k in ('status','snapshot_lag_min','counts')}, default=str, indent=1))"
echo '```'
echo
echo "## Database"
echo '```'
psql "SELECT count(*) AS markets, count(*) FILTER (WHERE closed) AS closed, count(*) FILTER (WHERE active AND NOT closed) AS open_active, count(*) FILTER (WHERE active AND NOT closed AND accepting_orders) AS accepting FROM markets;"
psql "SELECT (SELECT count(*) FROM snapshots) AS snapshots, (SELECT count(*) FROM orderbooks) AS orderbooks, (SELECT count(*) FROM price_history) AS price_points, pg_size_pretty(pg_database_size('pmintel')) AS db_size;"
echo '```'
echo
echo "## Collector jobs (last 24h)"
echo '```'
psql "SELECT job, count(*) AS runs, count(*) FILTER (WHERE status='ok') AS ok, count(*) FILTER (WHERE status='error') AS errors, max(finished_at) FILTER (WHERE status='ok') AS last_ok, round(avg(extract(epoch FROM finished_at-started_at))) AS avg_sec FROM job_runs WHERE started_at > now() - interval '24 hours' GROUP BY job ORDER BY job;"
psql "SELECT job, started_at, left(error, 160) AS error FROM job_runs WHERE status='error' ORDER BY id DESC LIMIT 5;"
echo '```'
echo
echo "## Order books"
echo '```'
psql "SELECT count(*) AS books_24h, count(DISTINCT market_id) AS markets_24h, round(100.0*avg(valid::int),1) AS valid_pct, max(ts) AS last_book, round(extract(epoch FROM now()-max(ts))/60) AS age_min FROM orderbooks WHERE ts > now() - interval '24 hours';"
psql "SELECT date_trunc('hour', ts) AS hour, count(*) AS books, count(DISTINCT market_id) AS markets FROM orderbooks WHERE ts > now() - interval '6 hours' GROUP BY 1 ORDER BY 1;"
echo '```'
echo
echo "## Security & backup restore"
echo '```'
bash scripts/security-check.sh 2>&1
echo "exit=$?"
echo '```'
} | tee "$OUT"
echo
echo "Saved to $OUT"
