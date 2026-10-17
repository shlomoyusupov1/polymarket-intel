#!/usr/bin/env bash
# Prove the off-site copy is usable: download it from Object Storage, check its SHA-256, decrypt,
# restore into a scratch database and compare row counts with production. Default: latest upload.
set -euo pipefail
cd "$(dirname "$0")/.."
STATE="${PMINTEL_STATE:-$HOME/.pmintel}"; mkdir -p "$STATE"
OFFSITE_PAR_URL=$(grep -E '^OFFSITE_PAR_URL=' .env | cut -d= -f2- || true)
BACKUP_KEY=$(grep -E '^BACKUP_KEY=' .env | cut -d= -f2- || true)
[ -n "$OFFSITE_PAR_URL" ] && [ -n "$BACKUP_KEY" ] || { echo "OFFSITE_PAR_URL / BACKUP_KEY missing in .env"; exit 2; }
export BACKUP_KEY
read -r name sha < <(if [ -n "${1:-}" ]; then grep " UPLOADED $1 " "$STATE/offsite.log" | tail -1 | sed -E 's/.* UPLOADED ([^ ]+) sha256=([0-9a-f]+).*/\1 \2/'; else cat "$STATE/offsite-latest"; fi)
[ -n "${name:-}" ] || { echo "no off-site upload recorded"; exit 2; }
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
fail() { echo "$(date -Is) RESTORE-FAILED $name: $*" | tee -a "$STATE/offsite.log"; exit 1; }
curl -fsS -o "$tmp/db.enc" "${OFFSITE_PAR_URL%/}/$name" || fail "download"
[ "$(sha256sum "$tmp/db.enc" | cut -d' ' -f1)" = "$sha" ] || fail "sha256 mismatch"
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_KEY -in "$tmp/db.enc" -out "$tmp/db.dump" || fail "decrypt"
psql() { sudo -n docker compose exec -T db psql -U pmintel -v ON_ERROR_STOP=1 "$@"; }
psql -d postgres -qc "DROP DATABASE IF EXISTS restore_offsite" >/dev/null
psql -d postgres -qc "CREATE DATABASE restore_offsite" >/dev/null
psql -d restore_offsite -qc "CREATE EXTENSION IF NOT EXISTS timescaledb; SELECT timescaledb_pre_restore();" >/dev/null
sudo -n docker compose exec -T db pg_restore -U pmintel -d restore_offsite --no-owner < "$tmp/db.dump" > "$tmp/restore.log" 2>&1 || true
psql -d restore_offsite -qc "SELECT timescaledb_post_restore();" >/dev/null
report=""; ok=1
for t in markets snapshots orderbooks price_history tips job_runs; do
  r=$(psql -d restore_offsite -tAc "SELECT count(*) FROM $t" 2>/dev/null | tr -d '[:space:]'); r=${r:-0}
  l=$(psql -d pmintel -tAc "SELECT count(*) FROM $t" 2>/dev/null | tr -d '[:space:]'); l=${l:-0}
  report="$report $t=$r/$l"
  [ "$t" = markets ] && { [ "$r" -gt 0 ] && [ "$r" -ge $(( l * 95 / 100 )) ] || ok=0; }
done
psql -d postgres -qc "DROP DATABASE restore_offsite" >/dev/null
[ $ok = 1 ] || fail "restored rows too low (restored/live):$report"
echo "$(date -Is) RESTORE-OK $name (restored/live):$report" | tee -a "$STATE/offsite.log"
