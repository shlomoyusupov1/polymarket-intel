#!/usr/bin/env bash
# Set the dashboard Basic Auth password in .env (hash only; the password is never stored).
set -euo pipefail
cd "$(dirname "$0")/.."
read -rsp "Dashboard password: " PW; echo
HASH=$(docker run --rm caddy:2 caddy hash-password --plaintext "$PW")
ESCAPED=${HASH//\$/\$\$}   # compose .env needs every $ doubled
sed -i "s|^BASIC_AUTH_HASH=.*|BASIC_AUTH_HASH=${ESCAPED//|/\\|}|" .env
echo "Saved. Restart the web container:  docker compose up -d web"
