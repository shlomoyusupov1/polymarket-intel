#!/usr/bin/env bash
# Encrypted off-site backup to Oracle Object Storage (Always Free) through a pre-authenticated
# request URL (OFFSITE_PAR_URL in .env, ending in /o/). Encryption: AES-256 with BACKUP_KEY from .env.
# KEEP A COPY OF BACKUP_KEY OUTSIDE THE SERVER — without it the off-site copy cannot be restored.
set -euo pipefail
cd "$(dirname "$0")/.."
STATE="${PMINTEL_STATE:-$HOME/.pmintel}"; mkdir -p "$STATE"
OFFSITE_PAR_URL=$(grep -E '^OFFSITE_PAR_URL=' .env | cut -d= -f2- || true)
BACKUP_KEY=$(grep -E '^BACKUP_KEY=' .env | cut -d= -f2- || true)
[ -n "$OFFSITE_PAR_URL" ] || { echo "OFFSITE_PAR_URL is not set in .env"; exit 2; }
[ -n "$BACKUP_KEY" ] || { echo "BACKUP_KEY is not set in .env"; exit 2; }
export BACKUP_KEY
name="pmintel-$(date -u +%Y%m%dT%H%M%SZ).dump.enc"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
sudo -n docker compose exec -T db pg_dump -U pmintel -Fc pmintel > "$tmp/db.dump"
[ -s "$tmp/db.dump" ] || { echo "$(date -Is) BACKUP-FAILED empty dump" | tee -a "$STATE/offsite.log"; exit 1; }
openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_KEY -in "$tmp/db.dump" -out "$tmp/db.enc"
sha=$(sha256sum "$tmp/db.enc" | cut -d' ' -f1)
if curl -fsS -T "$tmp/db.enc" "${OFFSITE_PAR_URL%/}/$name" >/dev/null; then
  echo "$(date -Is) UPLOADED $name sha256=$sha bytes=$(stat -c %s "$tmp/db.enc")" | tee -a "$STATE/offsite.log"
  echo "$name $sha" > "$STATE/offsite-latest"
else
  echo "$(date -Is) UPLOAD-FAILED $name" | tee -a "$STATE/offsite.log"; exit 1
fi
