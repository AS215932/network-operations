#!/bin/bash
# create-log-vm.sh — Provision the centralized logging VM on XCP-NG.
#
# Run from XOA (10.0.0.10) where xo-cli is configured. SSH to XOA via
# IPv6 overlay (2a0c:b641:b50:2::70) or jump via dom0 (193.70.32.138).
#
# What this builds:
#   - VM named "log" on the local SR
#   - 2 vCPU, 2 GiB RAM, 20 GiB root disk (resized from template)
#   - Second 50 GiB disk attached as xvdb, mounted at /var/lib/loki
#   - enX0 on infra (2a0c:b641:b50:2::b0/64)
#   - enX1 on mgmt (10.0.0.60/24)  — so dom0 can ship logs over mgmt v4
#   - cloud-init: hostname, SSH keys, networkd, fs_setup for xvdb
#
# After this script: SSH into log over v6 and run base Ansible roles
# (firewall, monitoring) followed by the logs role. See docs/ansible.md.

set -euo pipefail

NAME=log
DESC="Centralized log aggregation (Vector aggregator + Loki)"
VCPU=2
MEM=$((2 * 1024 * 1024 * 1024))           # 2 GiB
ROOT_DISK=$((20 * 1024 * 1024 * 1024))    # 20 GiB
DATA_DISK=$((50 * 1024 * 1024 * 1024))    # 50 GiB on /var/lib/loki

# UUIDs — discovered via `xo-cli list-objects` on 2026-05-07.
TEMPLATE=8937845a-c13e-f6c0-678e-441f2eb07418   # debian-13
SR=2581856f-cc58-3c8d-017b-35a121d75d70         # Local storage
INFRA=8a4fbd9c-f56e-d6d1-5bae-83452d88b089      # xapi2  (2a0c:b641:b50:2::/64)
MGMT=90aefc6a-eff2-cadc-5002-2af725b17bca       # xapi0  (10.0.0.0/24 + 2a0c:b641:b50:0::/64)

IPV6=2a0c:b641:b50:2::b0
IPV4_MGMT=10.0.0.60

SSH_KEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIqhNk5JGwvgtQQgt+bs5t9zOz0XX1sVUZV8NeYdM2IE svag@Z"

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
chpasswd:
  users:
    - name: root
      password: debug
      type: text
  expire: false
ssh_pwauth: true
bootcmd:
  - |
    cat > /etc/systemd/network/10-enX0.network <<'NETEOF'
    [Match]
    Name=enX0
    [Network]
    DHCP=no
    IPv6AcceptRA=no
    DNS=2a0c:b641:b50:2::1
    Domains=as215932.net
    Address=${IPV6}/64
    [Route]
    Destination=::/0
    Gateway=2a0c:b641:b50:2::1
    NETEOF
  - |
    cat > /etc/systemd/network/10-enX1.network <<'NETEOF'
    [Match]
    Name=enX1
    [Network]
    DHCP=no
    IPv6AcceptRA=no
    Address=${IPV4_MGMT}/24
    NETEOF
  - \"printf 'nameserver 2a0c:b641:b50:2::1\\\\nsearch as215932.net\\\\n' > /etc/resolv.conf\"
  - \"sed -i 's/^hosts:.*/hosts:          files dns/' /etc/nsswitch.conf\"
  - systemctl restart systemd-networkd
disk_setup:
  /dev/xvdb:
    table_type: gpt
    layout: true
    overwrite: false
fs_setup:
  - device: /dev/xvdb
    partition: 1
    filesystem: ext4
    label: loki-data
    overwrite: false
mounts:
  - [/dev/xvdb1, /var/lib/loki, ext4, \"defaults,nofail\", \"0\", \"2\"]
package_update: true
package_upgrade: true"

NETCFG="version: 2
ethernets:
  enX0:
    addresses:
      - ${IPV6}/64
    routes:
      - to: ::/0
        via: 2a0c:b641:b50:2::1
  enX1:
    addresses:
      - ${IPV4_MGMT}/24"

echo "Creating $NAME..."

VM_ID=$(xo-cli vm.create \
  name_label="$NAME" \
  name_description="$DESC" \
  template=$TEMPLATE \
  VIFs="json:[{\"network\":\"$INFRA\"},{\"network\":\"$MGMT\"}]" \
  CPUs.number=$VCPU \
  memory=$MEM \
  bootAfterCreate=false \
  destroyCloudConfigVdiAfterBoot=true \
  cloudConfig="$CLOUD" \
  networkConfig="$NETCFG")

echo "Created VM: $VM_ID"

# Resize the template's root VDI to 20 GiB.
ROOT_VDI=$(xo-cli list-objects type=VBD VM="$VM_ID" is_cd_drive=false \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['VDI'] if d else '')")
if [ -n "$ROOT_VDI" ]; then
  xo-cli vdi.set id="$ROOT_VDI" size="$ROOT_DISK"
  echo "Root disk resized to $((ROOT_DISK / 1024 / 1024 / 1024)) GiB"
fi

# Attach a second VDI for /var/lib/loki (cloud-init formats and mounts it).
DATA_VDI=$(xo-cli vdi.create \
  name_label="${NAME}-loki-data" \
  name_description="Loki chunks + tsdb index" \
  size=$DATA_DISK \
  sr="$SR")
xo-cli vbd.create \
  vm="$VM_ID" \
  vdi="$DATA_VDI" \
  bootable=false \
  type=Disk \
  position=1
echo "Attached $((DATA_DISK / 1024 / 1024 / 1024)) GiB data disk: $DATA_VDI"

# Boot order: disk only.
xo-cli vm.setBootOrder vm="$VM_ID" order=c

xo-cli vm.start id="$VM_ID"
echo "$NAME started: $VM_ID"
echo
echo "Next steps once cloud-init has finished:"
echo "  ssh -i ~/.ssh/id_servify svag@${IPV6}"
echo "  cd ~/Dev/hyrule-infra/ansible"
echo "  ansible-playbook playbooks/firewall.yml --tags apply --limit log -e firewall_apply=true"
echo "  ansible-playbook playbooks/monitoring.yml --tags apply --limit log -e monitoring_apply=true"
echo "  ansible-playbook playbooks/logs.yml --tags apply --limit log -e logs_apply=true"
