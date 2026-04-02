#!/bin/bash
# build-template.sh — Prepare a Debian VM for use as a hyrule-cloud customer template
#
# Run this INSIDE the VM that will become the template.
# Customer VMs are IPv6-only using AS215932 public space (2a0c:b641:b51::/48).
#
# After running, shut down the VM and convert to template in XO or via xe CLI:
#   xe vm-shutdown uuid=<uuid>
#   xe vm-param-set uuid=<uuid> is-a-template=true
#   xe vm-param-set uuid=<uuid> name-label="hyrule-debian-13"

set -euo pipefail

echo "=== Hyrule Cloud — Customer VM Template Builder ==="
echo "OS: $(cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '"')"
echo ""

# --- Install required packages ---
echo "Installing packages..."
apt-get update
apt-get install -y --no-install-recommends \
    cloud-init \
    cloud-utils \
    curl \
    git \
    ufw \
    openssh-server \
    python3 \
    ca-certificates \
    sudo \
    wget

# --- Install xe-guest-utilities ---
echo ""
echo "Installing xe-guest-utilities..."
XE_GUEST_VERSION="8.4.0-1"
XE_GUEST_URL="https://github.com/xenserver/xe-guest-utilities/releases/download/v${XE_GUEST_VERSION}/xe-guest-utilities_${XE_GUEST_VERSION}_amd64.deb"

if ! dpkg -l | grep -q xe-guest-utilities; then
    wget -q "$XE_GUEST_URL" -O /tmp/xe-guest-utilities.deb || {
        echo "WARNING: Could not download xe-guest-utilities from GitHub."
        echo "Download manually from https://github.com/xenserver/xe-guest-utilities/releases"
        echo "and install with: dpkg -i xe-guest-utilities_*.deb"
    }
    if [ -f /tmp/xe-guest-utilities.deb ]; then
        dpkg -i /tmp/xe-guest-utilities.deb
        rm /tmp/xe-guest-utilities.deb
    fi
else
    echo "  Already installed."
fi
systemctl enable xe-linux-distribution 2>/dev/null || true

# --- Configure cloud-init ---
echo ""
echo "Configuring cloud-init..."

# Use ConfigDrive datasource (XO injects cloud-init config as ISO)
cat > /etc/cloud/cloud.cfg.d/99_hyrule.cfg << 'CLOUDCFG'
datasource_list: [ ConfigDrive, None ]
datasource:
  ConfigDrive:
    dsmode: local
CLOUDCFG

# Disable cloud-init network config (we manage networking ourselves)
cat > /etc/cloud/cloud.cfg.d/99_disable_network.cfg << 'CLOUDCFG'
network:
  config: disabled
CLOUDCFG

# --- Configure networking (IPv6-only via SLAAC) ---
echo ""
echo "Configuring network interfaces (IPv6-only)..."
cat > /etc/network/interfaces << 'NETCFG'
# Loopback
auto lo
iface lo inet loopback

# Primary interface — IPv6-only via SLAAC
# Customer VMs get a globally routable IPv6 from 2a0c:b641:b51::/48
auto eth0
iface eth0 inet6 auto
    accept_ra 1
NETCFG

# Ensure IPv6 SLAAC works properly
cat > /etc/sysctl.d/99-ipv6.conf << 'SYSCTL'
net.ipv6.conf.all.accept_ra=1
net.ipv6.conf.eth0.accept_ra=1
# Disable IPv4 (not needed — NAT64/DNS64 handles IPv4-only destinations)
net.ipv6.conf.all.disable_ipv6=0
SYSCTL

# --- Configure SSH ---
echo ""
echo "Configuring SSH..."
systemctl enable ssh

# Listen on IPv6 only
sed -i 's/^#\?ListenAddress.*//' /etc/ssh/sshd_config
echo "ListenAddress ::" >> /etc/ssh/sshd_config

sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config

# Remove any existing host keys (cloud-init will regenerate)
rm -f /etc/ssh/ssh_host_*

# --- Configure UFW defaults (IPv6) ---
echo ""
echo "Configuring UFW defaults..."

# Ensure UFW handles IPv6
sed -i 's/^IPV6=.*/IPV6=yes/' /etc/default/ufw

ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP
ufw allow 443/tcp   # HTTPS
# Block outbound SMTP (anti-spam)
ufw deny out 25/tcp
ufw deny out 465/tcp
ufw deny out 587/tcp
ufw --force enable

# --- Clean up for templating ---
echo ""
echo "Cleaning up for template conversion..."

# Clear cloud-init state
cloud-init clean --logs --seed

# Remove machine ID (regenerated at boot)
rm -f /etc/machine-id /var/lib/dbus/machine-id
touch /etc/machine-id

# Clear hostname (set by cloud-init)
truncate -s 0 /etc/hostname

# Clean apt cache
apt-get clean
apt-get autoremove -y
rm -rf /var/lib/apt/lists/*

# Clear logs
find /var/log -type f -exec truncate -s 0 {} \;
journalctl --rotate 2>/dev/null || true
journalctl --vacuum-time=1s 2>/dev/null || true

# Clear shell history
history -c
rm -f /root/.bash_history
rm -f /home/*/.bash_history

# Clear tmp
rm -rf /tmp/* /var/tmp/*

echo ""
echo "=== Template preparation complete ==="
echo ""
echo "Next steps:"
echo "  1. Shut down this VM"
echo "  2. In XO or xe CLI:"
echo "     xe vm-shutdown uuid=<this-vm-uuid>"
echo "     xe vm-param-set uuid=<this-vm-uuid> is-a-template=true"
echo "     xe vm-param-set uuid=<this-vm-uuid> name-label=\"hyrule-debian-13\""
echo "  3. Record the template UUID and add to XCPNG_TEMPLATES in .env"
