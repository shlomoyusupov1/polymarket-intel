#!/usr/bin/env bash
# One-time, owner-approved: run scripts/auto-update.sh every 10 minutes from the ubuntu user's crontab.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
chmod +x "$DIR/scripts/auto-update.sh"
LINE="*/10 * * * * $DIR/scripts/auto-update.sh >> \$HOME/pmintel-update.log 2>&1"
( crontab -l 2>/dev/null | grep -v 'scripts/auto-update.sh' ; echo "$LINE" ) | crontab -
echo "installed:"; crontab -l | grep auto-update
