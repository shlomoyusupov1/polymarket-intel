#!/usr/bin/env bash
# One-time, owner-approved: run scripts/auto-update.sh every 10 minutes from the ubuntu user's crontab.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
chmod +x "$DIR/scripts/auto-update.sh"
# Ubuntu Minimal images ship without cron
command -v crontab >/dev/null || { sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq cron && sudo systemctl enable --now cron; }
LINE="*/10 * * * * $DIR/scripts/auto-update.sh >> \$HOME/pmintel-update.log 2>&1"
# no existing crontab is normal: never let that abort the install (set -e/pipefail)
{ crontab -l 2>/dev/null | grep -v 'scripts/auto-update.sh' || true; echo "$LINE"; } | crontab -
echo "installed:"; crontab -l | grep auto-update
