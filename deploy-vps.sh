#!/usr/bin/env bash
set -euo pipefail
if [[ "$(id -u)" -ne 0 ]]; then echo "Run as root: sudo bash deploy-vps.sh"; exit 1; fi
apt-get update
apt-get install -y ca-certificates curl git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
if [[ ! -d /opt/btc-signal-pro-elite/.git ]]; then
  git clone https://github.com/arslanjaved22244-gif/btc-signal-pro-elite.git /opt/btc-signal-pro-elite
else
  git -C /opt/btc-signal-pro-elite pull --ff-only
fi
cd /opt/btc-signal-pro-elite
docker compose up -d --build
if command -v ufw >/dev/null 2>&1; then ufw allow 8080/tcp || true; fi
echo "Dashboard: http://$(curl -4 -fsS https://api.ipify.org):8080/"
echo "Service status:"
docker compose ps
