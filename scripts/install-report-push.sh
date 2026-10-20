#!/usr/bin/env bash
# Hourly one-way report upload (scripts/push-report.sh). Needs REPORT_REPO and REPORT_TOKEN in .env.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
chmod +x "$DIR/scripts/push-report.sh"
grep -q '^REPORT_REPO=.\+' "$DIR/.env" && grep -q '^REPORT_TOKEN=.\+' "$DIR/.env" || { echo "set REPORT_REPO and REPORT_TOKEN in .env first"; exit 2; }
LINE="7 * * * * $DIR/scripts/push-report.sh >> \$HOME/.pmintel-cron.log 2>&1"
{ crontab -l 2>/dev/null | grep -v 'scripts/push-report.sh' || true; echo "$LINE"; } | crontab -
echo "installed:"; crontab -l | grep push-report
echo "first upload now…"; bash "$DIR/scripts/push-report.sh" && echo "OK: report uploaded"
