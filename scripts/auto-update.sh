#!/usr/bin/env bash
# CI-gated auto-deploy (cron every 10 min, installed by scripts/install-auto-update.sh).
#
#  - deploys origin/main only if every GitHub check run on that exact commit concluded "success"
#    (pending -> try again later; failed/missing -> never deployed)
#  - fast-forward only; a hand-edited server copy is never overwritten
#  - after deploy: health check (API answers, no container exited); on failure it rolls back to the
#    previous commit automatically and pauses auto-deploy until a human resumes it
#  - every decision is appended to ~/.pmintel/deploys.log
#
# Manual:  scripts/rollback.sh [sha]      scripts/resume-auto-update.sh
set -euo pipefail
cd "$(dirname "$0")/.."
STATE="${PMINTEL_STATE:-$HOME/.pmintel}"
mkdir -p "$STATE"
LOG="$STATE/deploys.log"
log() { echo "$(date -Is) $*" >> "$LOG"; echo "$*"; }

exec 9>"$STATE/update.lock"
flock -n 9 || exit 0
if [ -f "$STATE/paused" ]; then exit 0; fi

git fetch -q origin main
cur=$(git rev-parse HEAD)
target=$(git rev-parse origin/main)
[ "$cur" = "$target" ] && exit 0

if ! git merge-base --is-ancestor "$cur" "$target"; then
  log "SKIP $target: not a fast-forward of $cur (server copy diverged; fix by hand)"
  exit 0
fi

# ---- CI gate ----
repo=$(git remote get-url origin | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')
auth=()
[ -n "${GITHUB_TOKEN:-}" ] && auth=(-H "Authorization: Bearer $GITHUB_TOKEN")
if ! runs=$(curl -fsS "${auth[@]}" -H "Accept: application/vnd.github+json" \
      "${GITHUB_API:-https://api.github.com}/repos/$repo/commits/$target/check-runs?per_page=100"); then
  log "WAIT $target: GitHub API unreachable (not deploying without CI evidence)"
  exit 0
fi
verdict=$(printf '%s' "$runs" | python3 -c '
import json, sys
runs = json.load(sys.stdin).get("check_runs", [])
if not runs: print("missing")
elif any(r.get("status") != "completed" for r in runs): print("pending")
elif all(r.get("conclusion") in ("success", "skipped", "neutral") for r in runs): print("success")
else: print("failed")')
case "$verdict" in
  success) ;;
  pending|missing) exit 0 ;;  # CI not finished (or not started yet): try again next run
  *)
    marker="$STATE/skipped-$target"
    [ -f "$marker" ] || { log "SKIP $target: CI $verdict"; touch "$marker"; }
    exit 0 ;;
esac

# ---- deploy ----
echo "$cur" > "$STATE/previous"
git merge --ff-only -q "$target"
sudo -n docker compose up -d --build >> "$STATE/compose.log" 2>&1 || true

if scripts/healthcheck.sh; then
  log "DEPLOYED ${cur:0:7} -> ${target:0:7} (CI success, health ok)"
else
  log "ROLLBACK ${target:0:7} -> ${cur:0:7}: health check failed after deploy; auto-deploy paused"
  git reset -q --hard "$cur"
  sudo -n docker compose up -d --build >> "$STATE/compose.log" 2>&1 || true
  echo "health check failed after deploying $target at $(date -Is)" > "$STATE/paused"
  if scripts/healthcheck.sh; then log "ROLLBACK ok: running ${cur:0:7}"; else log "ROLLBACK unhealthy too — needs a human"; fi
  exit 1
fi
