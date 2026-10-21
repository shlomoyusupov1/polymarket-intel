#!/usr/bin/env bash
# Hourly one-way report upload (scripts/push-report.sh). Needs REPORT_REPO and REPORT_TOKEN in .env.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
chmod +x "$DIR/scripts/push-report.sh"
grep -q '^REPORT_REPO=.\+' "$DIR/.env" && grep -q '^REPORT_TOKEN=.\+' "$DIR/.env" || { echo "set REPORT_REPO and REPORT_TOKEN in .env first"; exit 2; }
LINE="7 * * * * $DIR/scripts/push-report.sh >> \$HOME/.pmintel-cron.log 2>&1"
DAILY="10 4 * * * mkdir -p \$HOME/.pmintel && bash $DIR/scripts/security-check.sh > \$HOME/.pmintel/security-latest.txt 2>&1"
{ crontab -l 2>/dev/null | grep -v -e 'scripts/push-report.sh' -e 'scripts/security-check.sh' || true; echo "$LINE"; echo "$DAILY"; } | crontab -
echo "installed:"; crontab -l | grep -e push-report -e security-check
echo "first upload now…"; bash "$DIR/scripts/push-report.sh" && echo "OK: report uploaded"
