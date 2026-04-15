#!/bin/bash
# bootstrap-app.sh — One-time VM setup for hyrule-web or hyrule-cloud.
#
# Idempotent: safe to re-run to repair state or refresh .env.
#
# Usage:
#   ./bootstrap-app.sh <web|cloud> [github-org]
#     github-org defaults to AS215932
#
# Required env vars (read from secrets.local.sh if present — see below):
#   GITHUB_ORG            — owner of hyrule-web / hyrule-cloud repos (default: AS215932)
#   # For cloud only:
#   XCPNG_PASSWORD        — XCP-NG dom0 root password
#   XO_TOKEN              — Xen Orchestra API token
#   SR_UUID               — XCP-NG default storage repository UUID
#   VM_NETWORK_UUID       — XCP-NG customer VM network UUID
#   XCPNG_TEMPLATES       — JSON dict, e.g. {"debian-13":"<uuid>"}
#   OPENPROVIDER_USERNAME, OPENPROVIDER_PASSWORD
#   OPENPROVIDER_{OWNER,ADMIN,TECH,BILLING}_HANDLE
#   PAYMENT_WALLET        — receiver address for x402 payments
#   DEV_BYPASS_SECRET     — optional; empty string for production
#   TSIG_SECRET           — base64 TSIG secret for hyrule-dns (shared w/ Knot)
#   DB_PASSWORD           — Postgres password for the hyrule role
#
# Secrets file convention (gitignored):
#   hyrule-infra/secrets.local.sh — sourced if present. Put `export FOO=...` lines in it.

set -euo pipefail

APP="${1:?usage: bootstrap-app.sh <web|cloud> [github-org]}"
GITHUB_ORG="${2:-${GITHUB_ORG:-AS215932}}"

if [[ "$APP" != "web" && "$APP" != "cloud" ]]; then
  echo "error: app must be 'web' or 'cloud'" >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_FILE="$REPO_DIR/secrets.local.sh"
[[ -f "$SECRETS_FILE" ]] && source "$SECRETS_FILE"

case "$APP" in
  web)   VM_IP="2a0c:b641:b50:2::30"; SERVICE="hyrule-web";   REPO="hyrule-web"   ;;
  cloud) VM_IP="2a0c:b641:b50:2::20"; SERVICE="hyrule-cloud"; REPO="hyrule-cloud" ;;
esac

SSH="ssh -i ${HOME}/.ssh/id_servify -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
SCP="scp -i ${HOME}/.ssh/id_servify -o StrictHostKeyChecking=accept-new"

echo "=== Bootstrapping $SERVICE on [$VM_IP] ==="

# --- 1. Install system deps + create hyrule user + /opt dir ---
$SSH root@"$VM_IP" bash <<REMOTE
set -euo pipefail
apt-get update -qq
apt-get install -y -qq python3 python3-venv git ca-certificates curl
$( [[ "$APP" == "cloud" ]] && echo "apt-get install -y -qq postgresql-17 libpq-dev dnsutils" )

# uv: install to /usr/local/bin so PATH-agnostic. Skip if already present.
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh
fi

# hyrule user
id hyrule >/dev/null 2>&1 || useradd -m -s /bin/bash hyrule

# /opt dir
install -d -o hyrule -g hyrule /opt/$SERVICE

# SSH dir + config for per-repo deploy key
install -d -m 700 -o hyrule -g hyrule /home/hyrule/.ssh
REMOTE

# --- 2. Deploy key — generate on VM, print pubkey so operator can add to GitHub ---
echo ""
echo "--- Deploy key setup ---"
$SSH root@"$VM_IP" bash <<REMOTE
set -euo pipefail
KEY=/home/hyrule/.ssh/id_${REPO}
if [[ ! -f "\$KEY" ]]; then
  sudo -u hyrule ssh-keygen -t ed25519 -N "" -C "${SERVICE}@$(hostname -s)" -f "\$KEY"
fi

# SSH config — alias hosts so git URL can say github-$REPO
CFG=/home/hyrule/.ssh/config
if ! grep -q "Host github-${REPO}" "\$CFG" 2>/dev/null; then
  cat >> "\$CFG" <<EOF
Host github-${REPO}
  HostName github.com
  User git
  IdentityFile ~/.ssh/id_${REPO}
  IdentitiesOnly yes
EOF
  chown hyrule:hyrule "\$CFG"
  chmod 600 "\$CFG"
fi

# Accept github's host key once (ed25519 + rsa)
sudo -u hyrule ssh-keyscan -t ed25519,rsa github.com >> /home/hyrule/.ssh/known_hosts 2>/dev/null || true
sort -u /home/hyrule/.ssh/known_hosts -o /home/hyrule/.ssh/known_hosts
chown hyrule:hyrule /home/hyrule/.ssh/known_hosts
REMOTE

echo ""
echo "Add this deploy key (read-only) to https://github.com/${GITHUB_ORG}/${REPO}/settings/keys/new"
echo "--------------------------------------------------------------------------------"
$SSH root@"$VM_IP" "cat /home/hyrule/.ssh/id_${REPO}.pub"
echo "--------------------------------------------------------------------------------"
read -rp "Press ENTER once the deploy key is added on GitHub... " _

# --- 3. Clone the repo ---
$SSH root@"$VM_IP" bash <<REMOTE
set -euo pipefail
if [[ ! -d /opt/$SERVICE/.git ]]; then
  sudo -u hyrule git clone git@github-${REPO}:${GITHUB_ORG}/${REPO}.git /opt/$SERVICE
fi
REMOTE

# --- 4. Postgres (cloud only) ---
if [[ "$APP" == "cloud" ]]; then
  : "${DB_PASSWORD:?DB_PASSWORD must be set (add to secrets.local.sh)}"
  $SSH root@"$VM_IP" bash <<REMOTE
set -euo pipefail
systemctl enable --now postgresql
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='hyrule'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE ROLE hyrule LOGIN PASSWORD '$DB_PASSWORD'"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='hyrule'" | grep -q 1 \
  || sudo -u postgres createdb -O hyrule hyrule

# pg_hba: allow local scram from hyrule. Only add once.
PG_HBA=\$(ls /etc/postgresql/*/main/pg_hba.conf)
if ! grep -q "^local.*hyrule.*scram-sha-256" "\$PG_HBA"; then
  sed -i '0,/^local/{s|^local|local   all             hyrule                                  scram-sha-256\nlocal|}' "\$PG_HBA"
  systemctl reload postgresql
fi
REMOTE
fi

# --- 5. systemd unit + sudoers for deploy script ---
$SCP "$REPO_DIR/configs/${SERVICE}.service" "root@[$VM_IP]:/etc/systemd/system/${SERVICE}.service"
$SSH root@"$VM_IP" bash <<REMOTE
set -euo pipefail
systemctl daemon-reload

# Narrow sudoers so hyrule can restart only its own service from deploy-app.sh.
SUDOERS=/etc/sudoers.d/hyrule-${SERVICE}
cat > "\$SUDOERS" <<EOF
hyrule ALL=(root) NOPASSWD: /bin/systemctl restart ${SERVICE}, /bin/systemctl is-active ${SERVICE}, /bin/systemctl status ${SERVICE}
EOF
chmod 440 "\$SUDOERS"
visudo -c -f "\$SUDOERS" >/dev/null
REMOTE

# --- 6. Render and install .env ---
echo ""
echo "--- Rendering .env from configs/${SERVICE}.env.j2 ---"
TMPFILE=$(mktemp)
trap "rm -f $TMPFILE" EXIT

python3 - <<PY > "$TMPFILE"
import os, sys
from string import Template

# Minimal Jinja2-ish substitution using a tiny subset ({{ var | default('x') }})
# is not 1:1 with Jinja, so use jinja2 if available, else a simple regex fallback.
try:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
    env = Environment(loader=FileSystemLoader("$REPO_DIR/configs"),
                      undefined=StrictUndefined, keep_trailing_newline=True)
    tpl = env.get_template("${SERVICE}.env.j2")
    print(tpl.render(**{k.lower(): v for k, v in os.environ.items()}), end="")
except ImportError:
    print("jinja2 not installed. Run: pip install --user jinja2", file=sys.stderr)
    sys.exit(1)
PY

$SCP "$TMPFILE" "root@[$VM_IP]:/opt/${SERVICE}/.env"
$SSH root@"$VM_IP" "chown hyrule:hyrule /opt/${SERVICE}/.env && chmod 600 /opt/${SERVICE}/.env"

# --- 7. Initial uv sync + service enable (but don't start yet — operator should inspect) ---
$SSH root@"$VM_IP" bash <<REMOTE
set -euo pipefail
cd /opt/${SERVICE}
sudo -u hyrule /usr/local/bin/uv sync --frozen 2>&1 || sudo -u hyrule /usr/local/bin/uv sync

systemctl enable ${SERVICE}
REMOTE

echo ""
echo "=== Bootstrap complete for ${SERVICE} ==="
echo "Review \`journalctl -u ${SERVICE}\` output, then start with:"
echo "  ssh -i ~/.ssh/id_servify root@[${VM_IP}] systemctl start ${SERVICE}"
