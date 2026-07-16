#!/bin/bash
# smoke-test.sh — End-to-end verification for Hyrule Cloud deployment
#
# Run from any machine with IPv6 connectivity (or dual-stack).
# Usage: ./smoke-test.sh <domain> [dev-bypass-secret]

set -euo pipefail

CUSTOMER_DOMAIN="${1:-hyrule.host}"
CUSTOMER_API_DOMAIN="cloud.${CUSTOMER_DOMAIN}"
CUSTOMER_DEPLOY_DOMAIN="deploy.${CUSTOMER_DOMAIN}"
INFRA_DOMAIN="servify.network"
GRAFANA_DOMAIN="grafana.${INFRA_DOMAIN}"
MON_DOMAIN="mon.${INFRA_DOMAIN}"
DEV_BYPASS="${2:-}"
PASS=0
FAIL=0
SMOKE_TMP=""

cleanup() {
    if [ -n "$SMOKE_TMP" ] && [ -d "$SMOKE_TMP" ]; then
        rm -rf "$SMOKE_TMP"
    fi
}
trap cleanup EXIT

green() { echo -e "\033[32m✓ $1\033[0m"; }
red()   { echo -e "\033[31m✗ $1\033[0m"; }

check() {
    local desc="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        green "$desc"
        PASS=$((PASS + 1))
    else
        red "$desc"
        FAIL=$((FAIL + 1))
    fi
}

check_shell() {
    local desc="$1"
    local cmd="$2"
    if bash -c "$cmd" >/dev/null 2>&1; then
        green "$desc"
        PASS=$((PASS + 1))
    else
        red "$desc"
        FAIL=$((FAIL + 1))
    fi
}

json_get() {
    local key="$1"
    python3 -c "import sys,json; print((json.load(sys.stdin) or {}).get('$key') or '')" 2>/dev/null
}

echo "=== Hyrule Cloud Smoke Tests ==="
echo "Customer domain: $CUSTOMER_DOMAIN"
echo "Customer API:    $CUSTOMER_API_DOMAIN"
echo "Infra domain:    $INFRA_DOMAIN"
echo ""

# --- DNS ---
echo "--- DNS ---"
check_shell "$CUSTOMER_DOMAIN AAAA record resolves" "dig +short '$CUSTOMER_DOMAIN' AAAA | grep -q '2a0c:b641:b5'"
check_shell "$CUSTOMER_API_DOMAIN AAAA record resolves" "dig +short '$CUSTOMER_API_DOMAIN' AAAA | grep -q '2a0c:b641:b5'"
check_shell "$CUSTOMER_DEPLOY_DOMAIN NS record exists" "dig +short '$CUSTOMER_DEPLOY_DOMAIN' NS | grep -q ."
check_shell "$CUSTOMER_DEPLOY_DOMAIN SOA record exists" "dig +short '$CUSTOMER_DEPLOY_DOMAIN' SOA | grep -q ."
# Also check A records for dual-stack
check_shell "$CUSTOMER_DOMAIN A record resolves (dual-stack)" "dig +short '$CUSTOMER_DOMAIN' A | grep -q ."

# --- HTTPS (prefer IPv6) ---
echo ""
echo "--- HTTPS ---"
check "https://$CUSTOMER_DOMAIN returns 200 (IPv6)" curl -6 -sf "https://$CUSTOMER_DOMAIN/" -o /dev/null
check "https://$CUSTOMER_API_DOMAIN/health returns 200 (IPv6)" curl -6 -sf "https://$CUSTOMER_API_DOMAIN/health" -o /dev/null
check "https://$CUSTOMER_DOMAIN returns 200 (IPv4 fallback)" curl -4 -sf "https://$CUSTOMER_DOMAIN/" -o /dev/null

# --- API Endpoints ---
echo ""
echo "--- API Endpoints ---"
check_shell "GET /v1/pricing returns JSON" "curl -6 -sf 'https://$CUSTOMER_API_DOMAIN/v1/pricing' | python3 -m json.tool"
check_shell "GET /v1/os/list returns JSON" "curl -6 -sf 'https://$CUSTOMER_API_DOMAIN/v1/os/list' | python3 -m json.tool"
check_shell "GET /v1/payments/networks returns JSON" "curl -6 -sf 'https://$CUSTOMER_API_DOMAIN/v1/payments/networks' | python3 -m json.tool"
check_shell "GET /v1/payments/networks advertises BTC/XMR" "curl -6 -sf 'https://$CUSTOMER_API_DOMAIN/v1/payments/networks' | python3 -c 'import json,sys; native=json.load(sys.stdin).get(\"native\", []); raise SystemExit(0 if {\"BTC\",\"XMR\"}.issubset(set(native)) else 1)'"
check_shell "x402 manifest at /.well-known/x402.json" "curl -6 -sf 'https://$CUSTOMER_API_DOMAIN/.well-known/x402.json' | python3 -m json.tool"
check_shell "x402 manifest advertises VM/network resources" "curl -6 -sf 'https://$CUSTOMER_API_DOMAIN/.well-known/x402.json' | python3 -c 'import json,sys; paths={r.get(\"path\") for r in json.load(sys.stdin).get(\"resources\", [])}; raise SystemExit(0 if {\"/v1/vm/create\",\"/v1/network/request\"}.issubset(paths) else 1)'"
check_shell "GET /v1/domains/check returns JSON" "curl -6 -sf 'https://$CUSTOMER_API_DOMAIN/v1/domains/check?domain=example.com' | python3 -m json.tool"
check_shell "managed-domain OpenAPI document is published" "curl -6 -sf 'https://$CUSTOMER_API_DOMAIN/v1/domains/openapi.json' | python3 -m json.tool"
check_shell "POST /v1/network/request requires payment" "test \"\$(curl -6 -s -o /dev/null -w '%{http_code}' -X POST 'https://$CUSTOMER_API_DOMAIN/v1/network/request' -H 'Content-Type: application/json' -d '{\"url\":\"https://example.com\",\"proxy_mode\":\"direct\"}')\" = '402'"

# --- Web Frontend ---
echo ""
echo "--- Web Frontend ---"
check_shell "Homepage contains Hyrule" "curl -6 -sf 'https://$CUSTOMER_DOMAIN/' | grep -qi 'hyrule'"
check_shell "Order page loads" "curl -6 -sf 'https://$CUSTOMER_DOMAIN/order' | grep -qi 'deploy'"
check_shell "llms.txt contains x402 manifest" "curl -6 -sf 'https://$CUSTOMER_DOMAIN/llms.txt' | grep -q 'cloud.hyrule.host/.well-known/x402.json'"
check_shell "API proxy works (/api/v1/pricing)" "curl -6 -sf 'https://$CUSTOMER_DOMAIN/api/v1/pricing' | python3 -m json.tool"
check "Static assets load (htmx)" curl -6 -sf "https://$CUSTOMER_DOMAIN/static/htmx.min.js" -o /dev/null

# --- Monitoring ---
echo ""
echo "--- Monitoring ---"
# Monitoring UIs remain under the infrastructure domain by policy; the
# customer-domain argument above intentionally does not alter these checks.
check "$GRAFANA_DOMAIN returns 200 (IPv6)" curl -6 -sf "https://$GRAFANA_DOMAIN/api/health" -o /dev/null
check "$MON_DOMAIN returns 200 (IPv6)" curl -6 -sf "https://$MON_DOMAIN/" -o /dev/null
check "Prometheus targets reachable" curl -6 -sf "http://[2a0c:b641:b50:2::50]:9090/api/v1/targets" -o /dev/null

# --- VM Provisioning (requires dev bypass) ---
if [ -n "$DEV_BYPASS" ]; then
    echo ""
    echo "--- VM Provisioning ---"
    SMOKE_TMP="$(mktemp -d)"
    ssh-keygen -q -t ed25519 -N "" -C "smoketest@hyrule" -f "$SMOKE_TMP/id_ed25519"
    SSH_PUB="$(cat "$SMOKE_TMP/id_ed25519.pub")"
    CREATE_BODY="$(python3 - "$SSH_PUB" <<'PY'
import json
import sys

print(json.dumps({
    "duration_days": 1,
    "size": "xs",
    "os": "debian-13",
    "ssh_pubkey": sys.argv[1],
    "open_ports": [22, 80, 443],
}))
PY
)"

    CREATE_RESP=$(curl -6 -sf -X POST "https://$CUSTOMER_API_DOMAIN/v1/vm/create" \
        -H "Content-Type: application/json" \
        -H "X-DEV-BYPASS: $DEV_BYPASS" \
        -d "$CREATE_BODY" 2>/dev/null || echo "FAILED")

    if echo "$CREATE_RESP" | python3 -m json.tool >/dev/null 2>&1; then
        VM_ID=$(echo "$CREATE_RESP" | json_get "vm_id" || true)
        MGMT_TOKEN=$(echo "$CREATE_RESP" | json_get "management_token" || true)
        STATUS_URL=$(echo "$CREATE_RESP" | json_get "status_url" || true)
        if [ -n "$VM_ID" ]; then
            green "VM creation accepted (vm_id: $VM_ID)"
            PASS=$((PASS + 1))
            if [ -n "$STATUS_URL" ] && echo "$STATUS_URL" | grep -q "/v1/vm/$VM_ID/status$"; then
                green "VM create response includes public status_url"
                PASS=$((PASS + 1))
            else
                red "VM create response missing public status_url"
                FAIL=$((FAIL + 1))
            fi
            if [ -n "$MGMT_TOKEN" ]; then
                green "VM create response includes management token"
                PASS=$((PASS + 1))
            else
                red "VM create response missing management token"
                FAIL=$((FAIL + 1))
            fi

            # Poll for ready status (max 120s)
            echo "  Waiting for VM to become ready (max 120s)..."
            for i in $(seq 1 24); do
                STATUS_RESP=$(curl -6 -sf "https://$CUSTOMER_API_DOMAIN/v1/vm/$VM_ID/status" 2>/dev/null || true)
                STATUS=$(echo "$STATUS_RESP" | json_get "status" || true)
                if [ "$STATUS" = "ready" ]; then
                    break
                fi
                sleep 5
            done

            if [ "$STATUS" = "ready" ]; then
                green "VM reached ready status"
                PASS=$((PASS + 1))

                # Check DNS AAAA record
                HOSTNAME=$(echo "$STATUS_RESP" | json_get "hostname" || true)
                if [ -n "$HOSTNAME" ]; then
                    check_shell "DNS AAAA record for $HOSTNAME" "dig +short '$HOSTNAME' AAAA | grep -q '2a0c:b641:b5'"
                fi

                # Check IPv6 is from our prefix
                VM_IP=$(echo "$STATUS_RESP" | json_get "ipv6" || true)
                if [ -n "$VM_IP" ]; then
                    check_shell "VM IPv6 is in AS215932 space" "echo '$VM_IP' | grep -q '2a0c:b641:b5'"
                    check_shell "SSH works over IPv6 from outside AS215932" \
                        "ssh -6 -i '$SMOKE_TMP/id_ed25519' -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15 root@'$VM_IP' true"
                fi

                if [ -n "$MGMT_TOKEN" ]; then
                    check_shell "Full VM read without management token is denied" \
                        "test \"\$(curl -6 -s -o /dev/null -w '%{http_code}' 'https://$CUSTOMER_API_DOMAIN/v1/vm/$VM_ID')\" = '404'"
                    check_shell "Full VM read requires management token and succeeds with it" "curl -6 -sf 'https://$CUSTOMER_API_DOMAIN/v1/vm/$VM_ID?token=$MGMT_TOKEN' | python3 -m json.tool"
                fi
            else
                red "VM did not reach ready status (current: $STATUS)"
                FAIL=$((FAIL + 1))
            fi

            # Cleanup
            echo "  Cleaning up test VM..."
            if [ -n "$MGMT_TOKEN" ]; then
                curl -6 -sf -X DELETE "https://$CUSTOMER_API_DOMAIN/v1/vm/$VM_ID?token=$MGMT_TOKEN" >/dev/null 2>&1 || true
            else
                curl -6 -sf -X DELETE "https://$CUSTOMER_API_DOMAIN/v1/vm/$VM_ID" \
                    -H "X-DEV-BYPASS: $DEV_BYPASS" >/dev/null 2>&1 || true
            fi
            green "Test VM cleanup requested"
            PASS=$((PASS + 1))
        else
            red "VM creation returned no ID"
            FAIL=$((FAIL + 1))
        fi
    else
        red "VM creation failed: $CREATE_RESP"
        FAIL=$((FAIL + 1))
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
