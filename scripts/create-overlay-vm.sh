#!/bin/bash
# scripts/create-overlay-vm.sh — create the `overlay` VM (Tor non-exit relay,
# i2pd, Yggdrasil) on xcp01, via plain `xe` on dom0 (default) or xo-cli.
#
# Separate from scripts/create-vms.sh on purpose: that script (re)creates the
# whole original fleet and seeds svag + ROOT keys. overlay gets ONLY the
# dedicated `kim` operator user (same shape as roles/kim_ops_key: /bin/sh,
# NOPASSWD sudo), root login disabled, no root authorized_keys.
#
# The Debian 13 template (TEMPLATE below) has a `svag` user + key baked into
# its disk image (created when the template was built). cloud-init's runcmd
# below removes it on first boot, so the VM ends up kim-only.
#
# Usage (run on xcp01 dom0 as kim; xe calls go through sudo):
#   KIM_PUBKEY_FILE=~/id_kim.pub scripts/create-overlay-vm.sh                    # dry run: print only
#   KIM_PUBKEY_FILE=~/id_kim.pub scripts/create-overlay-vm.sh --apply            # create + start (xe)
#   KIM_PUBKEY_FILE=~/id_kim.pub BACKEND=xo scripts/create-overlay-vm.sh --apply # via xo-cli instead
#
# BACKEND=auto (default) uses xe when it is on PATH (dom0) and xo-cli otherwise.
# The xe path is what built overlay on 2026-10-19: vm-install from TEMPLATE on
# SR, 2 GiB / 2 vCPU, root VDI resized to 20 GiB, template VIFs replaced by one
# infra VIF with a fresh random locally-administered MAC (checked unique
# across every VIF on the host), and a NoCloud seed VDI (vfat, label CIDATA)
# carrying user-data / meta-data / network-config.
#
# After boot: playbooks/ci-runner-key.yml + firewall.yml + overlay.yml +
# monitoring.yml + logs.yml with --limit overlay (docs/runbooks/overlay-build.md).
set -euo pipefail

TEMPLATE=8937845a-c13e-f6c0-678e-441f2eb07418
INFRA=8a4fbd9c-f56e-d6d1-5bae-83452d88b089
SR=2581856f-cc58-3c8d-017b-35a121d75d70   # local SR on xcp01 (xe path)

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

BACKEND="${BACKEND:-auto}"
if [ "$BACKEND" = "auto" ]; then
  if command -v xe >/dev/null 2>&1; then BACKEND=xe; else BACKEND=xo; fi
fi
case "$BACKEND" in xe|xo) ;; *) echo "BACKEND must be auto, xe or xo" >&2; exit 2 ;; esac

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
package_upgrade: true
runcmd:
  - [sh, -c, 'if id svag >/dev/null 2>&1; then pkill -u svag || true; userdel -r svag; fi; if [ -f /etc/sudoers.d/90-cloud-init-users ]; then sed -i -e \"/^# User rules for svag/d\" -e \"/^svag /d\" /etc/sudoers.d/90-cloud-init-users; fi']"

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
  echo "DRY RUN (backend ${BACKEND}) — would create ${NAME} (${VCPU} vCPU, ${MEM} B RAM, ${DISK} B disk) at ${IPV6} / ${IPV4}"
  echo "--- cloud-config ---"; echo "$CLOUD"
  echo "--- network-config ---"; echo "$NETCFG"
  echo "Re-run with --apply to create and start the VM."
  exit 0
fi

if [ "$BACKEND" = "xe" ]; then
  X="sudo xe"
  if [ -n "$($X vm-list name-label="$NAME" --minimal)" ]; then
    echo "${NAME} already exists; refusing to create a second one" >&2; exit 1
  fi
  for bin in mkfs.vfat python3; do
    command -v "$bin" >/dev/null 2>&1 || { echo "missing $bin on this host" >&2; exit 2; }
  done

  # Fresh locally-administered unicast MAC, unique across all VIFs on the host
  # (the 2026-10-19 hand build let xe pick and got 0e:0c:0c:0c:0c:0c,
  # which is also rtr-bsd's vm-network VIF MAC).
  existing_macs="$($X vif-list params=MAC --minimal | tr ',' '\n' | tr 'A-F' 'a-f')"
  MAC=""
  for _ in 1 2 3 4 5; do
    cand="$(python3 -c 'import os; b=bytearray(os.urandom(6)); b[0]=(b[0]|2)&0xfe; print(":".join("%02x"%x for x in b))')"
    if ! printf '%s\n' "$existing_macs" | grep -qx "$cand"; then MAC="$cand"; break; fi
  done
  [ -n "$MAC" ] || { echo "could not pick a unique MAC" >&2; exit 1; }

  VM_ID=$($X vm-install template=$TEMPLATE new-name-label="$NAME" sr-uuid=$SR)
  $X vm-param-set uuid="$VM_ID" name-description="$DESC"
  $X vm-memory-limits-set uuid="$VM_ID" static-min=$MEM dynamic-min=$MEM dynamic-max=$MEM static-max=$MEM
  $X vm-param-set uuid="$VM_ID" VCPUs-max=$VCPU
  $X vm-param-set uuid="$VM_ID" VCPUs-at-startup=$VCPU
  ROOTVDI=$($X vbd-list vm-uuid="$VM_ID" userdevice=0 params=vdi-uuid --minimal)
  $X vdi-param-set uuid="$ROOTVDI" name-label="${NAME}-root"
  $X vdi-resize uuid="$ROOTVDI" disk-size=$DISK
  for v in $($X vif-list vm-uuid="$VM_ID" --minimal | tr ',' ' '); do $X vif-destroy uuid="$v"; done
  $X vif-create vm-uuid="$VM_ID" network-uuid=$INFRA device=0 mac="$MAC" >/dev/null

  # NoCloud seed: vfat image labelled CIDATA, imported as a small RO disk.
  T=$(mktemp -d); IMG=$T/seed.img; M=$T/mnt; mkdir "$M"
  trap 'sudo umount "$M" 2>/dev/null || true; rm -rf "$T"' EXIT
  dd if=/dev/zero of="$IMG" bs=1M count=4 status=none
  mkfs.vfat -n CIDATA "$IMG" >/dev/null
  sudo mount -o loop "$IMG" "$M"
  printf 'instance-id: %s-%s\nlocal-hostname: %s\n' "$NAME" "$(date +%Y%m%d%H%M)" "$NAME" | sudo tee "$M/meta-data" >/dev/null
  printf '%s\n' "$CLOUD" | sudo tee "$M/user-data" >/dev/null
  printf '%s\n' "$NETCFG" | sudo tee "$M/network-config" >/dev/null
  sudo umount "$M"
  SEED=$($X vdi-create sr-uuid=$SR name-label="${NAME}-cidata" type=user virtual-size=4194304)
  $X vdi-import uuid="$SEED" filename="$IMG" format=raw
  $X vbd-create vm-uuid="$VM_ID" vdi-uuid="$SEED" device=1 bootable=false type=Disk mode=RO >/dev/null

  $X vm-start uuid="$VM_ID"
  echo "${NAME} created and started: ${VM_ID} (MAC ${MAC}, seed VDI ${SEED}; detach/destroy the seed after first boot)"
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
