#!/usr/bin/env bash
# One-time setup for a fresh Ubuntu 24.04 Lightsail instance.
# Usage (as the default ubuntu user):  bash bootstrap-lightsail.sh
set -euo pipefail

echo "==> Installing Docker Engine + compose plugin"
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg |
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" |
  sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker "$USER"

echo "==> Adding 4G swap (Demucs is memory hungry on small instances)"
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 4G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

echo "==> Done. Log out/in for docker group membership, then:"
echo "    git clone <repo> dubby && cd dubby/infra"
echo "    cp ../api/.env.example .env   # fill in secrets"
echo "    docker compose up -d --build"
