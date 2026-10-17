#!/usr/bin/env bash
# One-time: generate BACKUP_KEY if missing, then schedule a daily encrypted off-site backup (03:40)
# and a weekly restore test from the off-site copy (Sunday 05:10). Output: ~/.pmintel/offsite.log
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
grep -q '^BACKUP_KEY=.' "$DIR/.env" || { echo "BACKUP_KEY=$(openssl rand -hex 32)" >> "$DIR/.env"; echo "generated BACKUP_KEY in .env — copy it somewhere safe outside the server"; }
grep -q '^OFFSITE_PAR_URL=.' "$DIR/.env" || echo "note: add OFFSITE_PAR_URL=<pre-authenticated request URL ending in /o/> to .env"
{ crontab -l 2>/dev/null | grep -v -e 'offsite-backup.sh' -e 'offsite-restore-test.sh' || true
  echo "40 3 * * * $DIR/scripts/offsite-backup.sh >> \$HOME/.pmintel-cron.log 2>&1"
  echo "10 5 * * 0 $DIR/scripts/offsite-restore-test.sh >> \$HOME/.pmintel-cron.log 2>&1"; } | crontab -
crontab -l | grep offsite
