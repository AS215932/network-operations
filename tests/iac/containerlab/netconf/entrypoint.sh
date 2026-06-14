#!/usr/bin/env bash
# Lab-only startup for FRR + Netopeer2/sysrepo.

set -euo pipefail

mkdir -p /run/frr /var/log/frr /etc/frr
chown -R frr:frr /run/frr /var/log/frr /etc/frr
chmod 775 /run/frr

# netopeer2/libssh needs host keys for the SSH transport.
ssh-keygen -A >/dev/null 2>&1 || true

# Re-assert module ownership in case the sysrepo datastore was mounted fresh.
/usr/local/sbin/install-frr-yang-modules.sh >/var/log/frr/sysrepo-yang-install.log 2>&1 || {
  cat /var/log/frr/sysrepo-yang-install.log >&2
  exit 1
}

# Start FRR daemons directly so the sysrepo northbound module is definitely
# loaded. Keep VTY on loopback; NETCONF is provided by Netopeer2 on TCP/830.
/usr/lib/frr/zebra -d -f /etc/frr/frr.conf -A 127.0.0.1 -M sysrepo
/usr/lib/frr/staticd -d -f /etc/frr/frr.conf -A 127.0.0.1 -M sysrepo
/usr/lib/frr/bgpd -d -f /etc/frr/frr.conf -A 127.0.0.1 -M sysrepo
/usr/lib/frr/ospf6d -d -f /etc/frr/frr.conf -A ::1 -M sysrepo || true

# Start Netopeer2 in the background. The Debian package configures the base
# NETCONF server modules; FRR modules were installed above.
netopeer2-server -d -v 2 >/var/log/frr/netopeer2.log 2>&1 &

sleep infinity
