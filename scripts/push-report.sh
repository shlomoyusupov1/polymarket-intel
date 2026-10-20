#!/usr/bin/env bash
# One-way status report: the server writes its evidence report to a PRIVATE GitHub repo every hour,
# so the owner (and his assistant) can read the server's state without logging in.
# Nothing is ever read back from that repo or executed: this only uploads text.
#
#   .env:  REPORT_REPO=owner/pmintel-reports      (a private repo used only for these reports)
#          REPORT_TOKEN=github_pat_...            (fine-grained token: that one repo, Contents read/write)
#   install the hourly run:  bash scripts/install-report-push.sh
set -uo pipefail
cd "$(dirname "$0")/.."
REPO=$(grep '^REPORT_REPO=' .env | cut -d= -f2-)
TOKEN=$(grep '^REPORT_TOKEN=' .env | cut -d= -f2-)
[ -n "$REPO" ] && [ -n "$TOKEN" ] || { echo "REPORT_REPO / REPORT_TOKEN not set in .env"; exit 2; }
STATE="${PMINTEL_STATE:-$HOME/.pmintel}"; mkdir -p "$STATE"
OUT=$(mktemp)
trap 'rm -f "$OUT" "$OUT.json"' EXIT
bash scripts/evidence.sh > "$OUT" 2>&1
# never upload anything that looks like a secret from .env
while IFS='=' read -r k v; do
  [ -n "$v" ] && [ ${#v} -ge 8 ] && case "$k" in *TOKEN*|*PASSWORD*|*HASH*|*KEY*|*SECRET*|TG_API_*|OFFSITE_PAR_URL)
    python3 - "$OUT" "$v" <<'PY'
import sys
p, v = sys.argv[1], sys.argv[2]
s = open(p, encoding="utf-8", errors="replace").read().replace(v, "<redacted>")
open(p, "w", encoding="utf-8").write(s)
PY
  esac
done < <(grep -E '^[A-Z_]+=' .env)
put() {  # path, message
  local sha
  sha=$(curl -fsS -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/$REPO/contents/$1" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("sha",""))' 2>/dev/null)
  python3 - "$OUT" "$2" "$sha" > "$OUT.json" <<'PY'
import base64, json, sys
body = {"message": sys.argv[2], "content": base64.b64encode(open(sys.argv[1], "rb").read()).decode()}
if sys.argv[3]:
    body["sha"] = sys.argv[3]
print(json.dumps(body))
PY
  curl -fsS -o /dev/null -X PUT -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" \
       "https://api.github.com/repos/$REPO/contents/$1" --data-binary "@$OUT.json"
}
TS=$(date -u +%Y-%m-%dT%H:%MZ)
if put "latest.md" "report $TS"; then
  # one dated copy per day, for history
  [ "$(date -u +%H)" = "06" ] && put "daily/$(date -u +%F).md" "daily report $TS" || true
  echo "$(date -Is) PUSHED $TS" >> "$STATE/report-push.log"
else
  echo "$(date -Is) PUSH-FAILED $TS" >> "$STATE/report-push.log"; exit 1
fi
