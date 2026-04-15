#!/bin/bash
# deploy-app.sh — Pull latest code from GitHub on the target VM, sync the
# venv, run migrations (cloud only), and restart the systemd service.
#
# Usage:
#   ./deploy-app.sh <web|cloud> [git-ref]
#     git-ref defaults to origin/main.
#
# Requires: bootstrap-app.sh has already been run on the target VM.

set -euo pipefail

APP="${1:?usage: deploy-app.sh <web|cloud> [git-ref]}"
REF="${2:-origin/main}"

case "$APP" in
  web)   VM_IP="2a0c:b641:b50:2::30"; SERVICE="hyrule-web";   IS_CLOUD=0 ;;
  cloud) VM_IP="2a0c:b641:b50:2::20"; SERVICE="hyrule-cloud"; IS_CLOUD=1 ;;
  *) echo "error: app must be 'web' or 'cloud'" >&2; exit 1 ;;
esac

SSH="ssh -i ${HOME}/.ssh/id_servify -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"

echo "=== Deploying ${SERVICE}@${REF} to [${VM_IP}] ==="

$SSH root@"$VM_IP" sudo -u hyrule bash <<REMOTE
set -euo pipefail
cd /opt/${SERVICE}

echo "--- git fetch ---"
git fetch --prune origin

echo "--- git checkout ${REF} ---"
# Detached HEAD when REF is origin/main is expected; the service doesn't
# care about branch state.
git checkout "${REF}"

echo "--- uv sync ---"
if [[ -f uv.lock ]]; then
  /usr/local/bin/uv sync --frozen
else
  /usr/local/bin/uv sync
fi

$( if [[ "$IS_CLOUD" == "1" ]]; then echo "
echo '--- alembic upgrade head ---'
/usr/local/bin/uv run alembic upgrade head
"; fi )

echo "--- systemctl restart ${SERVICE} ---"
sudo /bin/systemctl restart ${SERVICE}
sudo /bin/systemctl is-active ${SERVICE}
REMOTE

echo ""
echo "=== ${SERVICE} deployed ==="
