#!/bin/bash
# smoke-test.sh — End-to-end verification for Hyrule Cloud deployment
#
# Run from any machine with IPv6 connectivity (or dual-stack).
# Usage: ./smoke-test.sh <domain> [dev-bypass-secret]

set -euo pipefail

DOMAIN="${1:-servify.network}"
API_DOMAIN="api.${DOMAIN}"
DEV_BYPASS="${2:-}"
PASS=0
FAIL=0

green() { echo -e "\033[32m✓ $1\033[0m"; }
red()   { echo -e "\033[31m✗ $1\033[0m"; }

check() {
    local desc="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        green "$desc"
        ((PASS++))
    else
        red "$desc"
        ((FAIL++))
    fi
}

echo "=== Hyrule Cloud Smoke Tests ==="
echo "Domain: $DOMAIN"
echo "API:    $API_DOMAIN"
echo ""

# --- DNS ---
echo "--- DNS ---"
check "servify.network AAAA record resolves" dig +short "$DOMAIN" AAAA | grep -q "2a0c:b641:b5"
check "api.servify.network AAAA record resolves" dig +short "$API_DOMAIN" AAAA | grep -q "2a0c:b641:b5"
check "deploy.servify.network NS record exists" dig +short "deploy.$DOMAIN" NS | grep -q .
# Also check A records for dual-stack
check "servify.network A record resolves (dual-stack)" dig +short "$DOMAIN" A | grep -q .

# --- HTTPS (prefer IPv6) ---
echo ""
echo "--- HTTPS ---"
check "https://$DOMAIN returns 200 (IPv6)" curl -6 -sf "https://$DOMAIN/" -o /dev/null
check "https://$API_DOMAIN/health returns 200 (IPv6)" curl -6 -sf "https://$API_DOMAIN/health" -o /dev/null
check "https://$DOMAIN returns 200 (IPv4 fallback)" curl -4 -sf "https://$DOMAIN/" -o /dev/null

# --- API Endpoints ---
echo ""
echo "--- API Endpoints ---"
check "GET /v1/pricing returns JSON" curl -6 -sf "https://$API_DOMAIN/v1/pricing" | python3 -m json.tool
check "GET /v1/os/list returns JSON" curl -6 -sf "https://$API_DOMAIN/v1/os/list" | python3 -m json.tool
check "x402 manifest at /.well-known/x402.json" curl -6 -sf "https://$API_DOMAIN/.well-known/x402.json" | python3 -m json.tool

# --- Web Frontend ---
echo ""
echo "--- Web Frontend ---"
check "Homepage contains Hyrule" curl -6 -sf "https://$DOMAIN/" | grep -qi "hyrule"
check "API proxy works (/api/v1/pricing)" curl -6 -sf "https://$DOMAIN/api/v1/pricing" | python3 -m json.tool
check "Static assets load (htmx)" curl -6 -sf "https://$DOMAIN/static/htmx.min.js" -o /dev/null

# --- VM Provisioning (requires dev bypass) ---
if [ -n "$DEV_BYPASS" ]; then
    echo ""
    echo "--- VM Provisioning ---"

    CREATE_RESP=$(curl -6 -sf -X POST "https://$API_DOMAIN/v1/vm/create" \
        -H "Content-Type: application/json" \
        -H "X-DEV-BYPASS: $DEV_BYPASS" \
        -d '{
            "duration_days": 1,
            "size": "xs",
            "os": "debian-13",
            "ssh_pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest smoketest@hyrule",
            "open_ports": [22, 80, 443]
        }' 2>/dev/null || echo "FAILED")

    if echo "$CREATE_RESP" | python3 -m json.tool >/dev/null 2>&1; then
        VM_ID=$(echo "$CREATE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || true)
        if [ -n "$VM_ID" ]; then
            green "VM creation accepted (id: $VM_ID)"
            ((PASS++))

            # Poll for ready status (max 120s)
            echo "  Waiting for VM to become ready (max 120s)..."
            for i in $(seq 1 24); do
                STATUS=$(curl -6 -sf "https://$API_DOMAIN/v1/vm/$VM_ID" | \
                    python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || true)
                if [ "$STATUS" = "ready" ]; then
                    break
                fi
                sleep 5
            done

            if [ "$STATUS" = "ready" ]; then
                green "VM reached ready status"
                ((PASS++))

                # Check DNS AAAA record
                HOSTNAME=$(curl -6 -sf "https://$API_DOMAIN/v1/vm/$VM_ID" | \
                    python3 -c "import sys,json; print(json.load(sys.stdin).get('hostname',''))" 2>/dev/null || true)
                if [ -n "$HOSTNAME" ]; then
                    check "DNS AAAA record for $HOSTNAME" dig +short "$HOSTNAME" AAAA | grep -q "2a0c:b641:b5"
                fi

                # Check IPv6 is from our prefix
                VM_IP=$(curl -6 -sf "https://$API_DOMAIN/v1/vm/$VM_ID" | \
                    python3 -c "import sys,json; print(json.load(sys.stdin).get('ipv6',''))" 2>/dev/null || true)
                if [ -n "$VM_IP" ]; then
                    check "VM IPv6 is in AS215932 space" echo "$VM_IP" | grep -q "2a0c:b641:b5"
                fi
            else
                red "VM did not reach ready status (current: $STATUS)"
                ((FAIL++))
            fi

            # Cleanup
            echo "  Cleaning up test VM..."
            curl -6 -sf -X DELETE "https://$API_DOMAIN/v1/vm/$VM_ID" \
                -H "X-DEV-BYPASS: $DEV_BYPASS" >/dev/null 2>&1 || true
            green "Test VM cleanup requested"
            ((PASS++))
        else
            red "VM creation returned no ID"
            ((FAIL++))
        fi
    else
        red "VM creation failed: $CREATE_RESP"
        ((FAIL++))
    fi
else
    echo ""
    echo "--- VM Provisioning ---"
    echo "  (skipped — pass dev bypass secret as second argument to test)"
fi

# --- Summary ---
echo ""
echo "=== Results ==="
echo "Passed: $PASS"
echo "Failed: $FAIL"
echo ""

if [ "$FAIL" -gt 0 ]; then
    red "Some tests failed!"
    exit 1
else
    green "All tests passed!"
    exit 0
fi
