#!/usr/bin/env bash
# Re-enable auto-deploy after a rollback (the next cron run deploys origin/main if its CI passed).
STATE="${PMINTEL_STATE:-$HOME/.pmintel}"
if [ -f "$STATE/paused" ]; then echo "was paused: $(cat "$STATE/paused")"; rm -f "$STATE/paused"; fi
echo "$(date -Is) RESUMED" >> "$STATE/deploys.log"; echo "auto-deploy resumed"
