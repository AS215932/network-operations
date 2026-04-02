#!/bin/bash
# bootstrap-dom0.sh — Initial XCP-NG dom0 configuration
# Run this on the XCP-NG host after fresh install via IPMI/KVM.
#
# Creates internal networks (bridges) and configures dom0 IPv6 on the
# management network using AS215932 public space (2a0c:b641:b50::/44).
# The default xenbr0 (bridged to physical NIC) is created by XCP-NG installer.

set -euo pipefail

# --- AS215932 addressing ---
# 2a0c:b641:b50:0::/64  mgmt     (dom0 ::1, fw ::2, xoa ::10)
# 2a0c:b641:b50:1::/64  transit  (fw ::1, rtr ::2)
# 2a0c:b641:b50:2::/64  infra    (rtr ::1, dns ::10, api ::20, web ::30)
# 2a0c:b641:b51::/48    customer VMs

DOM0_MGMT_ADDR="2a0c:b641:b50::1/64"

echo "=== Hyrule Cloud — dom0 Bootstrap ==="

# --- Validate we're on XCP-NG ---
if ! command -v xe &>/dev/null; then
    echo "ERROR: xe command not found. This script must run on XCP-NG dom0."
    exit 1
fi

HOST_UUID=$(xe host-list --minimal)
echo "Host UUID: $HOST_UUID"

# --- Set dom0 memory to 4GB ---
echo "Setting dom0 memory to 4096 MB..."
/opt/xensource/libexec/xen-cmdline --set-xen dom0_mem=4096M,max:4096M
echo "  (Requires reboot to take effect)"

# --- Create internal networks ---
create_network() {
    local name="$1"
    local desc="$2"

    existing=$(xe network-list name-label="$name" --minimal 2>/dev/null || true)
    if [ -n "$existing" ]; then
        echo "Network '$name' already exists: $existing"
        return
    fi

    uuid=$(xe network-create name-label="$name" name-description="$desc")
    echo "Created network '$name': $uuid"
}

echo ""
echo "Creating internal networks..."
create_network "xenbr-mgmt"    "Management (2a0c:b641:b50:0::/64) — XO, dom0"
create_network "xenbr-transit" "Transit (2a0c:b641:b50:1::/64) — fw <-> rtr"
create_network "xenbr-infra"   "Infrastructure (2a0c:b641:b50:2::/64) — api, web, dns"
create_network "xenbr-vm"      "Customer VMs (2a0c:b641:b51::/48) — tenant VMs"

# --- Assign dom0 IPv6 on management network ---
echo ""
echo "Configuring dom0 management interface..."
MGMT_NET=$(xe network-list name-label="xenbr-mgmt" --minimal)
MGMT_BRIDGE=$(xe network-param-get uuid="$MGMT_NET" param-name=bridge)

if ! ip -6 addr show "$MGMT_BRIDGE" 2>/dev/null | grep -q "2a0c:b641:b50::1"; then
    ip -6 addr add "$DOM0_MGMT_ADDR" dev "$MGMT_BRIDGE"
    ip link set "$MGMT_BRIDGE" up
    echo "  Assigned $DOM0_MGMT_ADDR to $MGMT_BRIDGE"
else
    echo "  $DOM0_MGMT_ADDR already assigned to $MGMT_BRIDGE"
fi

# Make persistent across reboots
cat > /etc/sysconfig/network-scripts/ifcfg-"$MGMT_BRIDGE" << EOF
DEVICE=$MGMT_BRIDGE
BOOTPROTO=static
IPV6INIT=yes
IPV6ADDR=2a0c:b641:b50::1/64
ONBOOT=yes
EOF

# --- Print summary ---
echo ""
echo "=== Network Summary ==="
xe network-list params=uuid,name-label,bridge | grep -E "uuid|name-label|bridge"

echo ""
echo "=== IPv6 Addressing Plan ==="
echo "  mgmt:     2a0c:b641:b50:0::/64  (dom0 ::1, fw ::2, xoa ::10)"
echo "  transit:  2a0c:b641:b50:1::/64  (fw ::1, rtr ::2)"
echo "  infra:    2a0c:b641:b50:2::/64  (rtr ::1, dns ::10, api ::20, web ::30)"
echo "  customer: 2a0c:b641:b51::/48    (one /64 per VM)"

echo ""
echo "=== Next Steps ==="
echo "1. Reboot to apply dom0 memory limit"
echo "2. Upload Debian 12 and OpenBSD ISOs to default SR"
echo "3. Create xoa VM on xenbr-mgmt (2a0c:b641:b50::10)"
echo "4. Create fw VM on xenbr0 + xenbr-mgmt + xenbr-transit"
echo ""
echo "Network UUIDs (save these for .env configs):"
for net in xenbr-mgmt xenbr-transit xenbr-infra xenbr-vm; do
    uuid=$(xe network-list name-label="$net" --minimal 2>/dev/null || echo "NOT FOUND")
    echo "  $net: $uuid"
done

echo ""
echo "Default SR UUID:"
xe sr-list type=ext --minimal 2>/dev/null || xe sr-list type=lvm --minimal 2>/dev/null || echo "  (check with: xe sr-list)"
