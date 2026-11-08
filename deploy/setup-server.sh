#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 22.04/24.04 server (Oracle Cloud Always Free or any VPS).
#
#   git clone https://github.com/zainabawan994/Nikaasi.git && cd Nikaasi/deploy
#   sudo bash setup-server.sh nikaasi.duckdns.org you@example.org
#
# What it does: installs Docker, opens ports 80/443 in the host firewall, enables
# automatic security updates, creates deploy/.env with random secrets (only once),
# then builds and starts the stack. Safe to run again.
set -euo pipefail

DOMAIN="${1:-}"
ACME_EMAIL="${2:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"

if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo bash $0 <domain> <email>"; exit 1; fi
if [[ -z "$DOMAIN" || -z "$ACME_EMAIL" ]]; then
  echo "Usage: sudo bash $0 <domain> <email-for-certificate-notices>"; exit 1
fi

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

step "Installing Docker and security updates"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl openssl unattended-upgrades iptables-persistent
if ! command -v docker >/dev/null; then
  apt-get install -y docker.io
fi
if ! docker compose version >/dev/null 2>&1; then
  apt-get install -y docker-compose-v2 || apt-get install -y docker-compose-plugin
fi
systemctl enable --now docker
dpkg-reconfigure -f noninteractive unattended-upgrades
if [[ -n "${SUDO_USER:-}" ]]; then usermod -aG docker "$SUDO_USER"; fi

step "Opening ports 80 and 443 in the host firewall"
# Oracle's Ubuntu images ship iptables rules that REJECT everything except SSH.
# Insert ACCEPT rules before the first REJECT (or append if there is none).
for port in 80 443; do
  for proto in tcp udp; do
    [[ $port == 80 && $proto == udp ]] && continue
    if ! iptables -C INPUT -p "$proto" --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null; then
      pos=$(iptables -L INPUT --line-numbers -n | awk '/REJECT/ {print $1; exit}')
      if [[ -n "$pos" ]]; then
        iptables -I INPUT "$pos" -p "$proto" --dport "$port" -m state --state NEW -j ACCEPT
      else
        iptables -A INPUT -p "$proto" --dport "$port" -m state --state NEW -j ACCEPT
      fi
    fi
  done
done
netfilter-persistent save >/dev/null
echo "Host firewall: SSH, 80/tcp, 443/tcp, 443/udp open."
echo "Also open 80 and 443 in the cloud provider's firewall (Oracle: VCN security list) — see DEPLOY.md."

step "Creating deploy/.env (secrets)"
ENV="$HERE/.env"
if [[ -f "$ENV" ]]; then
  echo "$ENV already exists — keeping its secrets."
  sed -i "s|^DOMAIN=.*|DOMAIN=$DOMAIN|; s|^ACME_EMAIL=.*|ACME_EMAIL=$ACME_EMAIL|" "$ENV"
else
  umask 077
  cat > "$ENV" <<EOF
# Nikaasi production secrets — never commit this file.
DOMAIN=$DOMAIN
ACME_EMAIL=$ACME_EMAIL
POSTGRES_PASSWORD=$(openssl rand -base64 36 | tr -d '/+=' | cut -c1-40)
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.5-flash
BACKUP_KEEP_DAYS=14
EOF
  echo "Created $ENV with a random database password."
fi
chmod 600 "$ENV"
mkdir -p "$HERE/backups" && chmod 700 "$HERE/backups"

step "Building and starting Nikaasi"
cd "$HERE"
docker compose up -d --build

step "Waiting for the app to become healthy"
for i in $(seq 1 60); do
  status=$(docker inspect -f '{{.State.Health.Status}}' nikaasi-app-1 2>/dev/null || echo starting)
  [[ "$status" == healthy ]] && break
  sleep 3
done
docker compose ps

cat <<EOF

$(printf '\033[1;32m')Nikaasi is running.$(printf '\033[0m')
  URL:  https://$DOMAIN   (the certificate is issued on the first visit; give it a minute)

Next steps:
  1. Create your admin account (you will be asked for a password, input is hidden):
       cd $HERE && sudo docker compose exec app python -m app.cli create-user you@example.org --role admin
  2. Optional — add your Gemini key:  sudo nano $ENV   then   sudo docker compose up -d
  3. Backups are written daily to $HERE/backups — copy them off the server regularly.
EOF
