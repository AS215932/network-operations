#!/bin/bash
# generate-tsig-key.sh — Generate a TSIG key for Knot DNS + hyrule-cloud
#
# The key name MUST be "hyrule-dns" — this is hardcoded in
# hyrule_cloud/providers/dns.py (line 36).
#
# Run this on any machine with tsig-keygen (from bind9-dnsutils or knot-dnsutils).
# Copy the output secret to:
#   - configs/knot.conf.j2 ({{ tsig_secret }})
#   - configs/Caddyfile.j2 ({{ tsig_secret }})
#   - configs/hyrule-cloud.env.j2 (HYRULE_DNS_TSIG_KEY)

set -euo pipefail

KEY_NAME="hyrule-dns"
ALGORITHM="hmac-sha256"

echo "=== Generating TSIG Key ==="
echo "Key name: $KEY_NAME"
echo "Algorithm: $ALGORITHM"
echo ""

# Method 1: Use tsig-keygen (from BIND)
if command -v tsig-keygen &>/dev/null; then
    echo "Using tsig-keygen (BIND)..."
    tsig-keygen -a "$ALGORITHM" "$KEY_NAME"
    echo ""
fi

# Method 2: Generate raw secret with openssl (universal)
SECRET=$(openssl rand -base64 32)
echo "--- Raw secret (for config files) ---"
echo ""
echo "Base64 secret: $SECRET"
echo ""
echo "--- Knot DNS config snippet ---"
echo "key:"
echo "  - id: $KEY_NAME"
echo "    algorithm: $ALGORITHM"
echo "    secret: \"$SECRET\""
echo ""
echo "--- hyrule-cloud .env ---"
echo "HYRULE_DNS_TSIG_KEY=$SECRET"
echo "HYRULE_DNS_TSIG_ALGO=$ALGORITHM"
echo ""
echo "--- Caddy rfc2136 snippet ---"
echo "key_name $KEY_NAME"
echo "key_alg ${ALGORITHM}."
echo "key $SECRET"
