#!/bin/bash
# render-env.sh — Re-render /opt/<service>/.env from configs/<service>.env.j2
# and restart the service. Assumes bootstrap-app.sh has already run on the VM.
#
# Usage: ./render-env.sh <web|cloud>

set -euo pipefail

APP="${1:?usage: render-env.sh <web|cloud>}"

case "$APP" in
  web)   VM_IP="2a0c:b641:b50:2::30"; SERVICE="hyrule-web"   ;;
  cloud) VM_IP="2a0c:b641:b50:2::20"; SERVICE="hyrule-cloud" ;;
  *) echo "error: app must be 'web' or 'cloud'" >&2; exit 1 ;;
esac

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_FILE="$REPO_DIR/secrets.local.sh"
[[ -f "$SECRETS_FILE" ]] && source "$SECRETS_FILE"

SSH="ssh -i ${HOME}/.ssh/id_servify -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
SCP="scp -i ${HOME}/.ssh/id_servify -o StrictHostKeyChecking=accept-new"

echo "=== Rendering .env for ${SERVICE} and deploying to [${VM_IP}] ==="

TMPFILE=$(mktemp)
trap "rm -f $TMPFILE" EXIT

python3 - <<PY > "$TMPFILE"
import os, sys
try:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
except ImportError:
    print("jinja2 not installed. Run: pip install --user jinja2", file=sys.stderr)
    sys.exit(1)

env = Environment(
    loader=FileSystemLoader("$REPO_DIR/configs"),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)
tpl = env.get_template("${SERVICE}.env.j2")
print(tpl.render(**{k.lower(): v for k, v in os.environ.items()}), end="")
PY

$SCP "$TMPFILE" "root@[$VM_IP]:/opt/${SERVICE}/.env"
$SSH root@"$VM_IP" "chown hyrule:hyrule /opt/${SERVICE}/.env \
    && chmod 600 /opt/${SERVICE}/.env \
    && systemctl restart ${SERVICE} \
    && systemctl is-active ${SERVICE}"

echo ""
echo "=== ${SERVICE} .env refreshed and service restarted ==="
