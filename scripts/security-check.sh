#!/usr/bin/env bash
# Verifies the security baseline on the server. Run:  bash scripts/security-check.sh
# Exit code 0 = all checks passed.
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0
pass() { echo "PASS  $1"; }
bad()  { echo "FAIL  $1"; fail=1; }

# 1. Only SSH/HTTP/HTTPS listen on public interfaces (Postgres must not)
public=$(sudo ss -tlnH | awk '{print $4}' | grep -vE '^(127\.|\[::1\]|\[::ffff:127)' | sed 's/.*://' | sort -un | tr '\n' ' ')
if [ -z "$(echo "$public" | tr ' ' '\n' | grep -vE '^(22|80|443|)$')" ]; then
  pass "public listening ports: $public"
else
  bad "unexpected public ports: $public"
fi
if sudo docker compose port db 5432 >/dev/null 2>&1; then bad "db port 5432 is published"; else pass "db port 5432 not published"; fi

# 2. No secrets tracked in git
if git ls-files --error-unmatch .env >/dev/null 2>&1; then bad ".env is tracked by git"; else pass ".env not tracked"; fi
if git grep -nE '(BEGIN [A-Z ]*PRIVATE KEY|POSTGRES_PASSWORD=[^c$]|BASIC_AUTH_HASH=\$\$2)' -- ':!*.example' ':!scripts/*' >/dev/null; then
  bad "possible secret committed (git grep)"
else
  pass "no secrets found in tracked files"
fi
[ "$(stat -c %a .env)" = "600" ] && pass ".env permissions 600" || bad ".env permissions are $(stat -c %a .env)"

# 3. Dashboard requires auth
ADDR=$(grep '^SITE_ADDRESS=' .env | cut -d= -f2)
code=$(curl -s -o /dev/null -w '%{http_code}' "https://$ADDR/api/health")
[ "$code" = "401" ] && pass "unauthenticated request rejected (401)" || bad "unauthenticated request returned $code"

# 4. Latest backup restores into a scratch database
latest=$(ls -1t backups/pmintel-*.dump 2>/dev/null | head -1)
if [ -z "$latest" ]; then
  bad "no backup file yet in ./backups"
else
  sudo docker compose exec -T db psql -U pmintel -d postgres -qc "DROP DATABASE IF EXISTS restore_test" >/dev/null
  sudo docker compose exec -T db psql -U pmintel -d postgres -qc "CREATE DATABASE restore_test" >/dev/null
  # TimescaleDB requires pre/post restore hooks around pg_restore
  sudo docker compose exec -T db psql -U pmintel -d restore_test -qc "CREATE EXTENSION IF NOT EXISTS timescaledb; SELECT timescaledb_pre_restore();" >/dev/null
  sudo docker compose exec -T db pg_restore -U pmintel -d restore_test --no-owner < "$latest" >/dev/null 2>&1
  sudo docker compose exec -T db psql -U pmintel -d restore_test -qc "SELECT timescaledb_post_restore();" >/dev/null
  n=$(sudo docker compose exec -T db psql -U pmintel -d restore_test -tAc "SELECT count(*) FROM markets" 2>/dev/null | tr -d '[:space:]')
  orig=$(sudo docker compose exec -T db psql -U pmintel -d pmintel -tAc "SELECT count(*) FROM markets" | tr -d '[:space:]')
  sudo docker compose exec -T db psql -U pmintel -d postgres -qc "DROP DATABASE restore_test" >/dev/null
  if [ -n "$n" ] && [ "$n" -gt 0 ]; then pass "backup $latest restored: $n markets (live: $orig)"; else bad "restore of $latest failed"; fi
fi

exit $fail
