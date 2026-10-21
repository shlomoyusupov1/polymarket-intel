#!/usr/bin/env bash
# Production Evidence Report — run on the server:
#   bash scripts/evidence.sh             # state of the system (a few seconds)
#   bash scripts/evidence.sh --research  # also runs the stock lab, crypto lab and Polymarket research first (~10-30 min)
#   bash scripts/evidence.sh --light     # hourly health report: database reads only — no extra containers, no
#                                        # backup/restore, no raw log lines (used by scripts/push-report.sh)
# Writes reports/evidence-<UTC timestamp>.md and prints it. Contains no secrets.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p reports
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="reports/evidence-$TS.md"
ADDR=$(grep '^SITE_ADDRESS=' .env | cut -d= -f2)
psql() { sudo docker compose exec -T db psql -U pmintel -d pmintel -AF ' | ' -c "$1" 2>&1; }
LIGHT=0; [ "${1:-}" = "--light" ] && LIGHT=1

if [ "${1:-}" = "--research" ]; then
  echo "running research (stocks, crypto, Polymarket) — this takes a while…"
  for job in stocklab cryptolab; do
    sudo docker compose run --rm "$job" > "reports/$job-$TS.log" 2>&1 && echo "$job: done" || echo "$job: FAILED (see reports/$job-$TS.log)"
  done
  sudo docker compose run --rm research > "reports/research-$TS.log" 2>&1 && echo "research: done" || echo "research: FAILED (see reports/research-$TS.log)"
fi

{
echo "# Production evidence — $TS"
echo
git fetch -q origin main 2>/dev/null
echo "- Running commit: \`$(git rev-parse --short HEAD)\` ($(git log -1 --format=%cI))"
echo "- Latest main on GitHub: \`$(git rev-parse --short origin/main 2>/dev/null)\` — $( [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main 2>/dev/null)" ] && echo 'UP TO DATE' || echo "BEHIND by $(git rev-list --count HEAD..origin/main 2>/dev/null) commits")"
echo "- Auto-deploy: $( [ -f "$HOME/.pmintel/paused" ] && echo "PAUSED ($(cat "$HOME/.pmintel/paused"))" || (crontab -l 2>/dev/null | grep -q auto-update && echo on || echo 'not installed'))"
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
echo "## Deploy log (last 8)"
echo '```'
tail -n 8 "$HOME/.pmintel/deploys.log" 2>/dev/null || echo "no deploy log"
echo '```'
echo
echo "## Jobs (last 72h) — every job, last success, failures"
echo '```'
psql "SELECT job, count(*) AS runs, count(*) FILTER (WHERE status='ok') AS ok, count(*) FILTER (WHERE status='partial') AS partial, count(*) FILTER (WHERE status IN ('error','interrupted')) AS failed, count(*) FILTER (WHERE status='running' AND started_at < now() - interval '2 hours') AS stuck, max(finished_at) FILTER (WHERE status IN ('ok','partial')) AS last_ok, round(avg(extract(epoch FROM finished_at-started_at))) AS avg_sec FROM job_runs WHERE started_at > now() - interval '72 hours' GROUP BY job ORDER BY job;"
psql "SELECT job, max(finished_at) FILTER (WHERE status IN ('ok','partial')) AS last_ok_ever FROM job_runs GROUP BY job ORDER BY job;"
echo
echo "errors in the last 72h (newest 15):"
psql "SELECT job, started_at, left(error, 160) AS error FROM job_runs WHERE status IN ('error','interrupted') AND started_at > now() - interval '72 hours' ORDER BY id DESC LIMIT 15;"
echo
if [ $LIGHT = 1 ]; then
  echo "error lines per service, last hour (counts only — no log text leaves the server):"
  for svc in $(sudo docker compose config --services 2>/dev/null); do
    n=$(sudo docker compose logs --since 1h --no-color "$svc" 2>/dev/null | grep -cE "ERROR|Traceback|Exception" || true)
    [ "${n:-0}" != "0" ] && echo "$svc: $n"
  done
  echo "(end)"
else
  echo "service logs, last 72h, lines with ERROR/Traceback (newest 20):"
  sudo docker compose logs --since 72h --no-color 2>/dev/null | grep -E "ERROR|Traceback|Exception" | tail -n 20 || true
fi
echo '```'
echo
echo "## Order books"
echo '```'
psql "SELECT count(*) AS books_24h, count(DISTINCT market_id) AS markets_24h, round(100.0*avg(valid::int),1) AS valid_pct, max(ts) AS last_book, round(extract(epoch FROM now()-max(ts))/60) AS age_min FROM orderbooks WHERE ts > now() - interval '24 hours';"
psql "SELECT date_trunc('hour', ts) AS hour, count(*) AS books, count(DISTINCT market_id) AS markets FROM orderbooks WHERE ts > now() - interval '6 hours' GROUP BY 1 ORDER BY 1;"
echo '```'
echo
echo "## Off-site backup"
echo '```'
tail -n 5 "$HOME/.pmintel/offsite.log" 2>/dev/null || echo "NOT SET UP: no off-site backup has run (scripts/install-offsite-backup.sh)"
ls -la backups/ 2>/dev/null | tail -n 5
echo '```'
echo
echo "## Research evidence (from the database)"
echo '```'
sudo docker compose exec -T api python -m pmintel.research_summary 2>&1
echo '```'
echo
if [ $LIGHT = 1 ]; then
  echo "## Live market feeds"
  echo '```'
  psql "SELECT feed, connected, subscribed, round(extract(epoch FROM now()-last_msg_at)) AS last_msg_s, round(lag_mean::numeric,2) AS lag_mean_s, round(lag_p95::numeric,2) AS lag_p95_s, error FROM live_feeds ORDER BY feed;"
  echo '```'
  echo
  echo "## Daily security & restore check (runs once a day, not here)"
  echo '```'
  if [ -f "$HOME/.pmintel/security-latest.txt" ]; then
    echo "last run: $(date -r "$HOME/.pmintel/security-latest.txt" -Is)"; grep -E "^(PASS|FAIL)" "$HOME/.pmintel/security-latest.txt"
  else
    echo "not run yet (installed by scripts/install-report-push.sh)"
  fi
  echo '```'
else
  echo "## Collection cadence, gaps, host (72h)"
  echo '```'
  sudo docker compose run --rm --entrypoint python research -m pmintel.prod_report --hours 72 2>&1 | tail -n 60
  echo '```'
  echo
  echo "## Security & backup restore"
  echo '```'
  bash scripts/security-check.sh 2>&1
  echo "exit=$?"
  echo '```'
fi
} | tee "$OUT"
echo
echo "Saved to $OUT"
