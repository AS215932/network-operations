#!/bin/bash
# deploy-exporters.sh — Install Prometheus exporters on all AS215932 infrastructure hosts.
# Run from any machine with SSH access to all targets (e.g., mon VM or your workstation).
#
# Targets:
#   Debian VMs:     node_exporter (all), postgres_exporter (api only)
#   FreeBSD routers: node_exporter, frr_exporter
#   Debian router:  node_exporter, frr_exporter
#   dom0 (XCP-NG): node_exporter

set -euo pipefail

SSH="ssh -i ~/.ssh/id_servify -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"

MON_IP="2a0c:b641:b50:2::50"

# --- Debian infrastructure VMs ---
DEBIAN_VMS=(
  "2a0c:b641:b50:2::10"  # dns
  "2a0c:b641:b50:2::20"  # api
  "2a0c:b641:b50:2::30"  # web
  "2a0c:b641:b50:2::40"  # proxy
  "2a0c:b641:b50:2::60"  # vpn
)

echo "=== Deploying node_exporter to Debian infrastructure VMs ==="
for HOST in "${DEBIAN_VMS[@]}"; do
  echo "--- $HOST ---"
  $SSH root@"$HOST" bash <<REMOTE
    apt-get update -qq
    apt-get install -y -qq prometheus-node-exporter
    # Restrict to IPv6 and listen on all interfaces
    mkdir -p /etc/default
    echo 'ARGS="--web.listen-address=[::]:9100"' > /etc/default/prometheus-node-exporter
    systemctl enable --now prometheus-node-exporter
    systemctl restart prometheus-node-exporter
    # Firewall: allow mon VM to scrape
    if command -v ufw &>/dev/null; then
      ufw allow from $MON_IP to any port 9100 proto tcp
    fi
REMOTE
  echo "$HOST: node_exporter deployed"
done

# --- postgres_exporter on api VM ---
echo ""
echo "=== Deploying postgres_exporter to api VM ==="
$SSH root@"2a0c:b641:b50:2::20" bash <<REMOTE
  apt-get install -y -qq prometheus-postgres-exporter

  # Dedicated read-only monitoring role; peer auth from the prometheus OS user
  # keeps the credential pair off disk (no password to rotate or leak).
  sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='prometheus'" | grep -q 1 \
    || sudo -u postgres psql -c "CREATE USER prometheus"
  sudo -u postgres psql -c "GRANT pg_monitor TO prometheus"

  PG_HBA=\$(ls /etc/postgresql/*/main/pg_hba.conf)
  grep -q '^local.*prometheus.*peer' \$PG_HBA \
    || sed -i '0,/^local/{s|^local|local   all             prometheus                              peer\nlocal|}' \$PG_HBA
  systemctl reload postgresql

  cat > /etc/default/prometheus-postgres-exporter <<'EOF'
DATA_SOURCE_NAME="postgresql:///postgres?host=/run/postgresql&user=prometheus&sslmode=disable"
ARGS="--web.listen-address=[::]:9187"
EOF
  systemctl enable --now prometheus-postgres-exporter
  systemctl restart prometheus-postgres-exporter
  if command -v ufw &>/dev/null; then
    ufw allow from $MON_IP to any port 9187 proto tcp
  fi
REMOTE
echo "api: postgres_exporter deployed"

# --- Debian router (rtr) ---
echo ""
echo "=== Deploying node_exporter + frr_exporter to rtr ==="
# rtr SSH is only accessible via dom0 jump host (underlay address on same L2).
RTR_SSH="$SSH -J root@193.70.32.138 root@2001:41d0:303:48a::2"
$RTR_SSH bash <<'REMOTE'
  apt-get update -qq
  apt-get install -y -qq prometheus-node-exporter

  echo 'ARGS="--web.listen-address=[::]:9100"' > /etc/default/prometheus-node-exporter
  systemctl enable --now prometheus-node-exporter
  systemctl restart prometheus-node-exporter

  # Exporters run in default VRF but must accept connections from overlay VRF clients.
  # l3mdev_accept allows sockets bound to [::] to accept cross-VRF connections.
  sysctl -w net.ipv4.tcp_l3mdev_accept=1
  sysctl -w net.ipv4.udp_l3mdev_accept=1
  grep -q tcp_l3mdev_accept /etc/sysctl.conf || {
    echo 'net.ipv4.tcp_l3mdev_accept=1' >> /etc/sysctl.conf
    echo 'net.ipv4.udp_l3mdev_accept=1' >> /etc/sysctl.conf
  }

  # frr_exporter — install from GitHub release if not present
  if ! command -v frr_exporter &>/dev/null && [ ! -f /usr/local/bin/frr_exporter ]; then
    FRR_EXPORTER_VERSION="1.11.0"
    cd /tmp
    curl -sLO "https://github.com/tynany/frr_exporter/releases/download/v${FRR_EXPORTER_VERSION}/frr_exporter_${FRR_EXPORTER_VERSION}_linux_amd64.tar.gz"
    tar xzf "frr_exporter_${FRR_EXPORTER_VERSION}_linux_amd64.tar.gz"
    mv frr_exporter /usr/local/bin/frr_exporter
    chmod +x /usr/local/bin/frr_exporter
    rm -f "frr_exporter_${FRR_EXPORTER_VERSION}_linux_amd64.tar.gz"
  fi

  # Create systemd service for frr_exporter
  cat > /etc/systemd/system/frr-exporter.service <<'EOF'
[Unit]
Description=FRR Exporter for Prometheus
After=frr.service

[Service]
ExecStart=/usr/local/bin/frr_exporter --web.listen-address=[::]:9342
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable --now frr-exporter
REMOTE
echo "rtr: node_exporter + frr_exporter deployed"

# --- FreeBSD core routers ---
FREEBSD_ROUTERS=(
  "2a0c:b641:b50::a"  # cr1.nl1
  "2a0c:b641:b50::b"  # cr1.de1
)

echo ""
echo "=== Deploying node_exporter + frr_exporter to FreeBSD routers ==="
for HOST in "${FREEBSD_ROUTERS[@]}"; do
  echo "--- $HOST ---"
  $SSH svag@"$HOST" sh <<'REMOTE'
    # node_exporter
    doas pkg install -y node_exporter
    doas sysrc node_exporter_enable=YES
    doas sysrc node_exporter_user=root
    doas sysrc node_exporter_args="--web.listen-address=[::]:9100"
    doas mkdir -p /var/tmp/node_exporter
    doas /usr/sbin/daemon -f -p /var/run/node_exporter.pid -T node_exporter /usr/local/bin/node_exporter --web.listen-address="[::]:9100"

    # frr_exporter — install from GitHub release (FreeBSD amd64)
    if [ ! -f /usr/local/bin/frr_exporter ]; then
      FRR_EXPORTER_VERSION="1.11.0"
      cd /tmp
      doas fetch "https://github.com/tynany/frr_exporter/releases/download/v${FRR_EXPORTER_VERSION}/frr_exporter_${FRR_EXPORTER_VERSION}_freebsd_amd64.tar.gz"
      doas tar xzf "frr_exporter_${FRR_EXPORTER_VERSION}_freebsd_amd64.tar.gz"
      doas mv frr_exporter /usr/local/bin/frr_exporter
      doas chmod +x /usr/local/bin/frr_exporter
      rm -f "frr_exporter_${FRR_EXPORTER_VERSION}_freebsd_amd64.tar.gz"
    fi

    # Create rc.d script for frr_exporter
    doas tee /usr/local/etc/rc.d/frr_exporter > /dev/null <<'RCEOF'
#!/bin/sh
# PROVIDE: frr_exporter
# REQUIRE: NETWORKING frr
# KEYWORD: shutdown

. /etc/rc.subr

name="frr_exporter"
rcvar="frr_exporter_enable"
command="/usr/local/bin/frr_exporter"
command_args="--web.listen-address=[::]:9342 &"
pidfile="/var/run/${name}.pid"

start_cmd="frr_exporter_start"

frr_exporter_start() {
  /usr/sbin/daemon -p ${pidfile} ${command} ${command_args}
}

load_rc_config $name
: ${frr_exporter_enable:=NO}
run_rc_command "$1"
RCEOF
    doas chmod +x /usr/local/etc/rc.d/frr_exporter
    doas sysrc frr_exporter_enable=YES
    doas service frr_exporter start 2>/dev/null || doas service frr_exporter restart
REMOTE
  echo "$HOST: node_exporter + frr_exporter deployed"
done

# --- dom0 (XCP-NG) ---
echo ""
echo "=== Deploying node_exporter to dom0 ==="
echo "NOTE: dom0 is reached via mgmt bridge (10.0.0.1). Run from mon VM."
$SSH root@10.0.0.1 bash <<'REMOTE'
  # XCP-NG is CentOS-based — use RPM
  if ! command -v node_exporter &>/dev/null && [ ! -f /usr/local/bin/node_exporter ]; then
    NODE_EXPORTER_VERSION="1.8.2"
    cd /tmp
    curl -sLO "https://github.com/prometheus/node_exporter/releases/download/v${NODE_EXPORTER_VERSION}/node_exporter-${NODE_EXPORTER_VERSION}.linux-amd64.tar.gz"
    tar xzf "node_exporter-${NODE_EXPORTER_VERSION}.linux-amd64.tar.gz"
    mv "node_exporter-${NODE_EXPORTER_VERSION}.linux-amd64/node_exporter" /usr/local/bin/
    chmod +x /usr/local/bin/node_exporter
    rm -rf "node_exporter-${NODE_EXPORTER_VERSION}.linux-amd64"*
  fi

  # Create systemd service
  cat > /etc/systemd/system/node-exporter.service <<'EOF'
[Unit]
Description=Prometheus Node Exporter
After=network.target

[Service]
ExecStart=/usr/local/bin/node_exporter --web.listen-address=0.0.0.0:9100
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable --now node-exporter
REMOTE
echo "dom0: node_exporter deployed"

echo ""
echo "=== All exporters deployed ==="
echo "Prometheus on mon VM should now be able to scrape all targets."
echo "Check: curl -s http://[::1]:9090/api/v1/targets | python3 -m json.tool"
