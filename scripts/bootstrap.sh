#!/usr/bin/env bash
# Full unattended install on a fresh Oracle Ubuntu VM (used by cloud-init.yaml).
# Writes the dashboard URL + generated password to ~/DASHBOARD.txt.
set -euo pipefail
cd "$(dirname "$0")/.."
exec > >(tee -a "$HOME/bootstrap.log") 2>&1

bash scripts/oracle-setup.sh

if ! grep -q '^BASIC_AUTH_HASH=.\+' .env; then
  PW=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-16)
  HASH=$(sudo docker run --rm caddy:2 caddy hash-password --plaintext "$PW")
  ESCAPED=${HASH//\$/\$\$}
  sed -i "s|^BASIC_AUTH_HASH=.*|BASIC_AUTH_HASH=${ESCAPED//|/\\|}|" .env
  ADDR=$(grep '^SITE_ADDRESS=' .env | cut -d= -f2)
  umask 077
  printf 'URL:      https://%s\nUser:     %s\nPassword: %s\n' \
    "$ADDR" "$(grep '^BASIC_AUTH_USER=' .env | cut -d= -f2)" "$PW" > "$HOME/DASHBOARD.txt"
fi

sudo docker compose up -d --build
echo "==> bootstrap finished $(date -u)"
cat "$HOME/DASHBOARD.txt"
