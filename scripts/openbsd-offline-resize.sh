#!/usr/bin/env bash
# Grow an OpenBSD VM root disk offline using a dedicated OpenBSD builder VM.
#
# This is the infrastructure-side equivalent of hyrule-cloud's OpenBSD
# provisioning hook. Use it after cloning/resizing a target VM's root VDI and
# before first boot.

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  openbsd-offline-resize.sh --target-vm <uuid> --builder-vm <uuid> --builder-host <host> [options]

Options:
  --target-vdi <uuid>       Root VDI to prep. If omitted, detected from target VM.
  --builder-user <user>     SSH user on the builder VM (default: svag).
  --builder-key <path>      SSH key for the builder VM.
  --builder-disk <dev>      Device name for attached target disk on builder (default: sd1).
  --attach-position <n>     VBD position on builder (default: 1).

Required tools: xo-cli, ssh, python3.
EOF
}

TARGET_VM=""
TARGET_VDI=""
BUILDER_VM=""
BUILDER_HOST=""
BUILDER_USER="svag"
BUILDER_KEY=""
BUILDER_DISK="sd1"
ATTACH_POSITION="1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target-vm) TARGET_VM="$2"; shift 2 ;;
    --target-vdi) TARGET_VDI="$2"; shift 2 ;;
    --builder-vm) BUILDER_VM="$2"; shift 2 ;;
    --builder-host) BUILDER_HOST="$2"; shift 2 ;;
    --builder-user) BUILDER_USER="$2"; shift 2 ;;
    --builder-key) BUILDER_KEY="$2"; shift 2 ;;
    --builder-disk) BUILDER_DISK="$2"; shift 2 ;;
    --attach-position) ATTACH_POSITION="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$TARGET_VM" || -z "$BUILDER_VM" || -z "$BUILDER_HOST" ]]; then
  usage
  exit 2
fi

json_first_id() {
  python3 -c 'import json,sys; d=json.load(sys.stdin); print(next(iter(d), {}).get("id", ""))'
}

json_first_vdi() {
  python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((x.get("VDI") for x in d if x.get("VDI")), ""))'
}

if [[ -z "$TARGET_VDI" ]]; then
  TARGET_VDI=$(
    xo-cli list-objects type=VBD VM="$TARGET_VM" is_cd_drive=false |
      json_first_vdi
  )
fi

if [[ -z "$TARGET_VDI" ]]; then
  echo "Could not detect target root VDI for $TARGET_VM" >&2
  exit 1
fi

SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes)
if [[ -n "$BUILDER_KEY" ]]; then
  SSH_OPTS+=(-i "$BUILDER_KEY")
fi

ssh_builder() {
  ssh "${SSH_OPTS[@]}" "${BUILDER_USER}@${BUILDER_HOST}" "$@"
}

cleanup_vbd() {
  local vbd="$1"
  if [[ -n "$vbd" ]]; then
    xo-cli vbd.disconnect id="$vbd" >/dev/null 2>&1 || true
    xo-cli vbd.delete id="$vbd" >/dev/null 2>&1 || true
  fi
}

echo "Stopping builder $BUILDER_VM..."
xo-cli vm.stop id="$BUILDER_VM" force=true >/dev/null 2>&1 || true

echo "Attaching target VDI $TARGET_VDI to builder..."
xo-cli vm.attachDisk \
  vm="$BUILDER_VM" \
  vdi="$TARGET_VDI" \
  position="$ATTACH_POSITION" \
  mode=RW \
  bootable=false

ATTACHED_VBD=$(
  xo-cli list-objects type=VBD VM="$BUILDER_VM" VDI="$TARGET_VDI" |
    json_first_id
)
trap 'xo-cli vm.stop id="$BUILDER_VM" force=true >/dev/null 2>&1 || true; cleanup_vbd "$ATTACHED_VBD"' EXIT

echo "Starting builder..."
xo-cli vm.start id="$BUILDER_VM"

echo "Waiting for builder SSH..."
for _ in $(seq 1 24); do
  if ssh_builder true >/dev/null 2>&1; then
    break
  fi
  sleep 5
done
ssh_builder true

echo "Growing OpenBSD root filesystem on $BUILDER_DISK..."
RESIZE_CMD=(sh -s -- "$BUILDER_DISK")
if [[ "$BUILDER_USER" != "root" ]]; then
  RESIZE_CMD=(doas "${RESIZE_CMD[@]}")
fi
ssh_builder "${RESIZE_CMD[@]}" <<'OPENBSD_RESIZE'
set -eu
disk="$1"

case "$disk" in
  sd[0-9]|wd[0-9]) ;;
  *) echo "unsupported OpenBSD disk device: $disk" >&2; exit 64 ;;
esac

cd /dev
sh MAKEDEV "$disk" >/dev/null 2>&1 || true
cd /

if mount | grep -Eq "/dev/${disk}[a-p][[:space:]]"; then
  echo "refusing to resize mounted disk ${disk}" >&2
  mount >&2
  exit 65
fi

printf 'edit 3\n\n\n\n*\nwrite\nquit\n' | fdisk -e "$disk"
printf 'b\n\n*\nm a\n\n*\n\n\n\n\nw\nq\n' | disklabel -E "$disk"
growfs -y "/dev/r${disk}a"
fsck_ffs -fy "/dev/r${disk}a"
disklabel "$disk"
OPENBSD_RESIZE

echo "Stopping builder and detaching VDI..."
xo-cli vm.stop id="$BUILDER_VM" force=true
cleanup_vbd "$ATTACHED_VBD"
trap - EXIT

echo "OpenBSD root resize complete for VDI $TARGET_VDI."
