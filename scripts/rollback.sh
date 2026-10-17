#!/usr/bin/env bash
# Roll back to a known-good commit (default: the commit before the last auto-deploy) and pause
# auto-deploy so the bad commit is not redeployed. Resume with scripts/resume-auto-update.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
STATE="${PMINTEL_STATE:-$HOME/.pmintel}"
mkdir -p "$STATE"
target="${1:-$(cat "$STATE/previous" 2>/dev/null || true)}"
[ -n "$target" ] || { echo "usage: scripts/rollback.sh <sha> (no previous deploy recorded)"; exit 2; }
git cat-file -e "$target^{commit}" || { echo "unknown commit $target"; exit 2; }
exec 9>"$STATE/update.lock"; flock 9
cur=$(git rev-parse HEAD)
echo "manual rollback from $cur to $target at $(date -Is)" > "$STATE/paused"
git reset -q --hard "$target"
sudo -n docker compose up -d --build >> "$STATE/compose.log" 2>&1 || true
if scripts/healthcheck.sh; then r=ok; else r=UNHEALTHY; fi
echo "$(date -Is) MANUAL-ROLLBACK ${cur:0:7} -> ${target:0:7}: $r (auto-deploy paused)" | tee -a "$STATE/deploys.log"
[ "$r" = ok ]
