#!/bin/bash
set -e

TEMPLATE=8937845a-c13e-f6c0-678e-441f2eb07418
SR=2581856f-cc58-3c8d-017b-35a121d75d70
INFRA=8a4fbd9c-f56e-d6d1-5bae-83452d88b089
SSH_KEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIqhNk5JGwvgtQQgt+bs5t9zOz0XX1sVUZV8NeYdM2IE svag@Z"

create_vm() {
  local NAME=$1 DESC="$2" VCPU=$3 MEM=$4 DISK=$5 IPV6=$6
  local DATA_DISK="${7:-}"
  local DATA_VBD_POSITION="${8:-1}"
  # Network + gateway default to infra; ci-pr overrides them to land on the
  # customer-isolated vm bridge (xenbr-vm) with the rtr enX3 gateway.
  local NETWORK="${9:-$INFRA}"
  local GATEWAY="${10:-2a0c:b641:b50:2::1}"

  echo "Creating $NAME..."

  CLOUD="#cloud-config
hostname: $NAME
fqdn: ${NAME}.as215932.net
manage_etc_hosts: true
users:
  - name: svag
    groups: sudo
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - $SSH_KEY
  - name: root
    ssh_authorized_keys:
      - $SSH_KEY
ssh_pwauth: false
disable_root: false
package_update: true
package_upgrade: true"

  NETCFG="version: 2
ethernets:
  enX0:
    addresses:
      - ${IPV6}/64
    nameservers:
      addresses:
        - ${GATEWAY}
      search:
        - as215932.net
    routes:
      - to: ::/0
        via: ${GATEWAY}"

  VM_ID=$(xo-cli vm.create \
    name_label="$NAME" \
    name_description="$DESC" \
    template=$TEMPLATE \
    VIFs="json:[{\"network\":\"$NETWORK\"}]" \
    CPUs.number=$VCPU \
    memory=$MEM \
    bootAfterCreate=false \
    destroyCloudConfigVdiAfterBoot=true \
    cloudConfig="$CLOUD" \
    networkConfig="$NETCFG")

  # Find the VM's disk VDI (non-CD VBD) and resize it
  VDI_ID=$(xo-cli --list-objects type=VBD VM="$VM_ID" is_cd_drive=false | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['VDI'] if d else '')")
  if [ -n "$VDI_ID" ]; then
    xo-cli vdi.set id="$VDI_ID" size="$DISK"
    echo "$NAME disk resized to $DISK"
  fi

  if [ -n "$DATA_DISK" ]; then
    DATA_VDI=$(xo-cli vdi.create \
      name_label="${NAME}-data" \
      name_description="${NAME} runner data disk" \
      size="$DATA_DISK" \
      sr="$SR")
    xo-cli vbd.create \
      vm="$VM_ID" \
      vdi="$DATA_VDI" \
      bootable=false \
      type=Disk \
      position="$DATA_VBD_POSITION"
    echo "$NAME attached extra data disk at VBD position $DATA_VBD_POSITION: $DATA_VDI"
  fi

  # Set UEFI boot order: disk only (no PXE)
  xo-cli vm.setBootOrder vm="$VM_ID" order=c

  xo-cli vm.start id="$VM_ID"
  echo "$NAME created and started: $VM_ID"
}

create_vm dns "Authoritative DNS (Knot)" 1 1073741824 10737418240 "2a0c:b641:b50:2::10"
create_vm api "hyrule-cloud API + Postgres" 2 4294967296 42949672960 "2a0c:b641:b50:2::20"
create_vm web "hyrule-web frontend" 1 2147483648 21474836480 "2a0c:b641:b50:2::30"
create_vm proxy "TLS reverse proxy (Caddy)" 1 1073741824 10737418240 "2a0c:b641:b50:2::40"
create_vm mon "Monitoring (Icinga2 + Prometheus + Grafana)" 2 4294967296 42949672960 "2a0c:b641:b50:2::50"
create_vm vpn "WireGuard VPN" 1 1073741824 10737418240 "2a0c:b641:b50:2::60"
create_vm irc "Soju IRC bouncer" 1 1073741824 10737418240 "2a0c:b641:b50:2::80"
create_vm vault "Vault secret plane" 1 2147483648 21474836480 "2a0c:b641:b50:2::c0"
create_vm ci "GitHub Actions self-hosted runner" 4 8589934592 21474836480 "2a0c:b641:b50:2::d0" 53687091200 8
create_vm netproxy "Hyrule Network Proxy sidecar" 1 1073741824 21474836480 "2a0c:b641:b50:2::e0"

# ci-pr — UNPRIVILEGED PR runner on the CUSTOMER-isolated vm bridge (xenbr-vm),
# NOT infra. Resolve the vm-bridge network UUID and export VM_NET before running:
#   xo-cli --list-objects type=network \
#     | python3 -c 'import sys,json; [print(n["uuid"],n["name_label"]) for n in json.load(sys.stdin)]'
#   export VM_NET=<uuid of the xenbr-vm / "vm" network>
# Args 7,8 (data disk/VBD) are empty; args 9,10 are NETWORK + GATEWAY.
VM_NET="${VM_NET:-}"
if [ -n "$VM_NET" ]; then
  create_vm ci-pr "Unprivileged PR runner (PR-Agent/Semgrep/PR CI)" \
    4 8589934592 21474836480 "2a0c:b641:b51::c1" "" "" "$VM_NET" "2a0c:b641:b51::1"
else
  echo "SKIP ci-pr: export VM_NET=<xenbr-vm network UUID> to create it (see docs/ci/provision-ci-pr.md)."
fi

# mon needs a second NIC on xenbr-mgmt to scrape dom0/XOA (underlay-only hosts).
# After create_vm, add it manually:
#   xo-cli vm.createInterface vm=<MON_VM_ID> network=<MGMT_NETWORK_UUID>
# Then deploy configs/mon/10-enX1.network for the mgmt interface.

echo "All VMs created."
