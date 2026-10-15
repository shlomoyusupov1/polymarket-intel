#!/usr/bin/env bash
# One-time setup for an Oracle Cloud Always Free Ubuntu (ARM) VM.
# Run as the default 'ubuntu' user:  bash scripts/oracle-setup.sh
set -euo pipefail

echo "==> Installing Docker"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl git netfilter-persistent iptables-persistent
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"
sudo systemctl enable --now docker

echo "==> Opening ports 80/443 in the VM firewall"
# Oracle's Ubuntu images ship iptables rules that REJECT everything except SSH.
# The VCN Security List must ALSO allow 80/443 (done in the Oracle console, see README).
REJECT_LINE=$(sudo iptables -L INPUT --line-numbers -n | awk '$2=="REJECT"{print $1; exit}')
for port in 80 443; do
  if ! sudo iptables -C INPUT -p tcp -m state --state NEW --dport "$port" -j ACCEPT 2>/dev/null; then
    if [ -n "$REJECT_LINE" ]; then
      sudo iptables -I INPUT "$REJECT_LINE" -p tcp -m state --state NEW --dport "$port" -j ACCEPT
    else
      sudo iptables -A INPUT -p tcp -m state --state NEW --dport "$port" -j ACCEPT
    fi
  fi
done
sudo netfilter-persistent save

echo "==> Preparing .env"
cd "$(dirname "$0")/.."
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 24)|" .env
  IP=$(curl -fsS https://ifconfig.me || true)
  if [ -n "$IP" ]; then
    sed -i "s|^SITE_ADDRESS=.*|SITE_ADDRESS=${IP//./-}.sslip.io|" .env
  fi
fi

echo
echo "Done. Next steps:"
echo "  1. Log out and back in (so the docker group applies)."
echo "  2. Set the dashboard password:  bash scripts/set-password.sh"
echo "  3. Start:                       docker compose up -d --build"
grep '^SITE_ADDRESS=' .env | sed 's/SITE_ADDRESS=/  Dashboard will be at: https:\/\//'
