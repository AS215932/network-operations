#!/bin/bash
# scripts/create-overlay-vm.sh — create the `overlay` VM (Tor non-exit relay,
# i2pd, Yggdrasil) on xcp01 via xo-cli.
#
# Separate from scripts/create-vms.sh on purpose: that script (re)creates the
# whole original fleet and seeds svag + ROOT keys. overlay gets ONLY the
# dedicated `kim` operator user (same shape as roles/kim_ops_key: /bin/sh,
# NOPASSWD sudo), root login disabled, no root authorized_keys.
#
# Usage:
#   KIM_PUBKEY_FILE=~/.ssh/id_kim.pub scripts/create-overlay-vm.sh           # dry run: print only
#   KIM_PUBKEY_FILE=~/.ssh/id_kim.pub scripts/create-overlay-vm.sh --apply   # create + start
#
# After boot: playbooks/ci-runner-key.yml + firewall.yml + overlay.yml +
# monitoring.yml + logs.yml with --limit overlay (docs/runbooks/overlay-build.md).
set -euo pipefail

TEMPLATE=8937845a-c13e-f6c0-678e-441f2eb07418
INFRA=8a4fbd9c-f56e-d6d1-5bae-83452d88b089

NAME=overlay
DESC="Tor non-exit relay / i2pd / Yggdrasil"
VCPU=2
MEM=2147483648       # 2 GiB
DISK=21474836480     # 20 GiB
IPV6=2a0c:b641:b50:2::71
GW6=2a0c:b641:b50:2::1
IPV4=10.0.2.71
GW4=10.0.2.1

apply=false
[ "${1:-}" = "--apply" ] && apply=true

: "${KIM_PUBKEY_FILE:?set KIM_PUBKEY_FILE to the kim operator public key}"
KIM_KEY="$(cat "$KIM_PUBKEY_FILE")"
case "$KIM_KEY" in
  ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-*) ;;
  *) echo "KIM_PUBKEY_FILE does not look like an OpenSSH public key" >&2; exit 2 ;;
esac

CLOUD="#cloud-config
hostname: ${NAME}
fqdn: ${NAME}.as215932.net
manage_etc_hosts: true
users:
  - name: kim
    shell: /bin/sh
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - ${KIM_KEY}
disable_root: true
ssh_pwauth: false
package_update: true
package_upgrade: true"

NETCFG="version: 2
ethernets:
  enX0:
    addresses:
      - ${IPV6}/64
      - ${IPV4}/24
    nameservers:
      addresses:
        - ${GW6}
      search:
        - as215932.net
    routes:
      - to: ::/0
        via: ${GW6}
      - to: 0.0.0.0/0
        via: ${GW4}"

if ! $apply; then
  echo "DRY RUN — would create ${NAME} (${VCPU} vCPU, ${MEM} B RAM, ${DISK} B disk) at ${IPV6} / ${IPV4}"
  echo "--- cloud-config ---"; echo "$CLOUD"
  echo "--- network-config ---"; echo "$NETCFG"
  echo "Re-run with --apply to create and start the VM."
  exit 0
fi

VM_ID=$(xo-cli vm.create \
  name_label="$NAME" \
  name_description="$DESC" \
  template=$TEMPLATE \
  VIFs="json:[{\"network\":\"$INFRA\"}]" \
  CPUs.number=$VCPU \
  memory=$MEM \
  bootAfterCreate=false \
  destroyCloudConfigVdiAfterBoot=true \
  cloudConfig="$CLOUD" \
  networkConfig="$NETCFG")

VDI_ID=$(xo-cli --list-objects type=VBD VM="$VM_ID" is_cd_drive=false | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['VDI'] if d else '')")
if [ -n "$VDI_ID" ]; then
  xo-cli vdi.set id="$VDI_ID" size="$DISK"
fi

xo-cli vm.setBootOrder vm="$VM_ID" order=c
xo-cli vm.start id="$VM_ID"
echo "${NAME} created and started: ${VM_ID}"
