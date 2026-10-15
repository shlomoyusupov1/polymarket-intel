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
pub=$(sudo docker compose port db 5432 2>/dev/null | grep -v ':0$' || true)
if [ -n "$pub" ]; then bad "db port 5432 is published on $pub"; else pass "db port 5432 not published"; fi
if echo "$public" | tr ' ' '\n' | grep -qx 111; then
  echo "INFO  port 111 is rpcbind from the Oracle image; the VCN security list does not expose it. Disable: sudo systemctl disable --now rpcbind rpcbind.socket"
fi

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

# 4. A fresh dump (same command as the backup service) restores into a scratch database
latest="backups/restore-check.dump"
if ! sudo docker compose exec -T db pg_dump -U pmintel -Fc pmintel > "$latest"; then
  bad "pg_dump failed"
else
  sudo docker compose exec -T db psql -U pmintel -d postgres -qc "DROP DATABASE IF EXISTS restore_test" >/dev/null
  sudo docker compose exec -T db psql -U pmintel -d postgres -qc "CREATE DATABASE restore_test" >/dev/null
  # TimescaleDB requires pre/post restore hooks around pg_restore
  sudo docker compose exec -T db psql -U pmintel -d restore_test -qc "CREATE EXTENSION IF NOT EXISTS timescaledb; SELECT timescaledb_pre_restore();" >/dev/null
  sudo docker compose exec -T db pg_restore -U pmintel -d restore_test --no-owner < "$latest" > backups/restore-check.log 2>&1
  echo "INFO  pg_restore messages: $(grep -c . backups/restore-check.log) lines (backups/restore-check.log)"
  sudo docker compose exec -T db psql -U pmintel -d restore_test -qc "SELECT timescaledb_post_restore();" >/dev/null
  n=$(sudo docker compose exec -T db psql -U pmintel -d restore_test -tAc "SELECT count(*) FROM markets" 2>/dev/null | tr -d '[:space:]')
  orig=$(sudo docker compose exec -T db psql -U pmintel -d pmintel -tAc "SELECT count(*) FROM markets" | tr -d '[:space:]')
  sudo docker compose exec -T db psql -U pmintel -d postgres -qc "DROP DATABASE restore_test" >/dev/null
  rm -f "$latest"
  if [ -n "$n" ] && [ "$n" -gt 0 ] && [ "$n" -ge $(( orig * 95 / 100 )) ]; then
    pass "fresh dump restored: $n markets (live: $orig)"
  else
    bad "restore check: restored=$n live=$orig — see backups/restore-check.log"
  fi
fi
newest=$(ls -1t backups/pmintel-*.dump 2>/dev/null | head -1)
[ -n "$newest" ] && echo "INFO  newest scheduled backup: $newest ($(du -h "$newest" | cut -f1))"

exit $fail
