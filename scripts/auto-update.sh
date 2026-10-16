#!/usr/bin/env bash
# Pull new commits from GitHub main and redeploy (run by cron every 10 minutes).
# Install once:  bash scripts/install-auto-update.sh      Log: ~/pmintel-update.log
# Remove:        crontab -l | grep -v auto-update.sh | crontab -
set -euo pipefail
cd "$(dirname "$0")/.."
exec 9>/tmp/pmintel-update.lock
flock -n 9 || exit 0                         # never two deploys at once
git fetch -q origin main
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] && exit 0
old=$(git rev-parse --short HEAD)
git merge --ff-only -q origin/main           # refuses if the server copy was edited by hand
sudo -n docker compose up -d --build
echo "$(date -Is) deployed $old -> $(git rev-parse --short HEAD)"
