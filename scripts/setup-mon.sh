#!/bin/bash
# setup-mon.sh — Bootstrap the mon VM with Prometheus, Grafana, Icinga 2, and Blackbox Exporter.
# Run via SSH on the mon VM (2a0c:b641:b50:2::50) after cloud-init completes.
#
# Prerequisites:
#   - mon VM created via create-vms.sh with cloud-init
#   - Second NIC (enX1) on xenbr-mgmt added via xo-cli
#   - configs/mon/ directory available (copy from this repo)

set -euo pipefail

CONFIGS="/root/mon-configs"  # Copy configs/mon/* here before running

echo "=== Installing packages ==="
apt-get update
apt-get install -y \
  prometheus \
  prometheus-blackbox-exporter \
  prometheus-node-exporter \
  grafana \
  icinga2 \
  icinga2-ido-pgsql \
  icingaweb2 \
  icingacli \
  monitoring-plugins \
  postgresql \
  nginx \
  php-fpm \
  php-icinga \
  php-pgsql \
  php-intl \
  php-imagick

echo "=== Configuring Prometheus ==="
cp "$CONFIGS/prometheus.yml" /etc/prometheus/prometheus.yml
cp "$CONFIGS/blackbox.yml" /etc/prometheus/blackbox.yml
systemctl enable --now prometheus
systemctl enable --now prometheus-blackbox-exporter
systemctl enable --now prometheus-node-exporter

echo "=== Configuring Grafana ==="
# Grafana listens on [::]:3000 by default.
# Configure Prometheus as the default datasource.
cat > /etc/grafana/provisioning/datasources/prometheus.yaml <<'EOF'
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://localhost:9090
    isDefault: true
    editable: true
EOF

# Set root URL for reverse proxy
sed -i 's|;root_url = .*|root_url = https://grafana.servify.network/|' /etc/grafana/grafana.ini

systemctl enable --now grafana-server

echo "=== Configuring PostgreSQL for Icinga IDO ==="
sudo -u postgres createuser -S icinga2 2>/dev/null || true
sudo -u postgres createdb -O icinga2 icinga2 2>/dev/null || true
sudo -u postgres psql -c "ALTER USER icinga2 WITH PASSWORD 'icinga2';" 2>/dev/null || true

# Import IDO schema
PGPASSWORD=icinga2 psql -U icinga2 -d icinga2 -f /usr/share/icinga2-ido-pgsql/schema/pgsql.sql 2>/dev/null || true

# Icinga Web database
sudo -u postgres createuser -S icingaweb2 2>/dev/null || true
sudo -u postgres createdb -O icingaweb2 icingaweb2 2>/dev/null || true
sudo -u postgres psql -c "ALTER USER icingaweb2 WITH PASSWORD 'icingaweb2';" 2>/dev/null || true

echo "=== Configuring Icinga 2 ==="
# Enable IDO PostgreSQL feature
icinga2 feature enable ido-pgsql
cat > /etc/icinga2/features-available/ido-pgsql.conf <<'EOF'
library "db_ido_pgsql"

object IdoPgsqlConnection "ido-pgsql" {
  user = "icinga2"
  password = "icinga2"
  host = "localhost"
  database = "icinga2"
}
EOF

# Enable API (required for Icinga Web)
icinga2 feature enable api
icinga2 api setup

# Deploy Icinga 2 config objects
mkdir -p /etc/icinga2/conf.d/hosts /etc/icinga2/conf.d/services
cp "$CONFIGS/icinga2/zones.conf" /etc/icinga2/zones.conf
cp "$CONFIGS/icinga2/hosts/"*.conf /etc/icinga2/conf.d/hosts/
cp "$CONFIGS/icinga2/services/"*.conf /etc/icinga2/conf.d/services/
cp "$CONFIGS/icinga2/notifications.conf" /etc/icinga2/conf.d/

# Validate config before starting
icinga2 daemon -C

systemctl enable --now icinga2

echo "=== Configuring Icinga Web 2 ==="
# Generate setup token
icingacli setup token create

# Configure Nginx for Icinga Web on port 80 (Caddy terminates TLS)
cat > /etc/nginx/sites-available/icingaweb2 <<'NGINX'
server {
    listen [::]:80 default_server;
    listen 80 default_server;

    root /usr/share/icingaweb2/public;
    index index.php;

    location ~ ^/icingaweb2/index\.php(.*)$ {
        fastcgi_pass unix:/run/php/php-fpm.sock;
        fastcgi_index index.php;
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME /usr/share/icingaweb2/public/index.php;
        fastcgi_param ICINGAWEB_CONFIGDIR /etc/icingaweb2;
        fastcgi_param REMOTE_USER $remote_user;
    }

    location ~ ^/icingaweb2(.+)? {
        alias /usr/share/icingaweb2/public;
        index index.php;
        try_files $1 $uri $uri/ /icingaweb2/index.php$is_args$args;
    }

    location / {
        rewrite ^/$ /icingaweb2 redirect;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/icingaweb2 /etc/nginx/sites-enabled/default
systemctl enable --now nginx
systemctl enable --now php*-fpm

echo "=== Configuring systemd-networkd ==="
cp "$CONFIGS/10-enX0.network" /etc/systemd/network/10-enX0.network
cp "$CONFIGS/10-enX1.network" /etc/systemd/network/10-enX1.network
systemctl restart systemd-networkd

echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Open https://mon.servify.network/ and complete Icinga Web setup wizard"
echo "     Setup token: $(icingacli setup token show 2>/dev/null || echo 'run: icingacli setup token show')"
echo "  2. Open https://grafana.servify.network/ (default: admin/admin)"
echo "  3. Import Grafana dashboards:"
echo "     - Node Exporter Full: dashboard ID 1860"
echo "     - FRR: search community dashboards for FRRouting"
echo "  4. Deploy exporters to target hosts: run scripts/deploy-exporters.sh"
echo "  5. Configure notification targets in /etc/icinga2/conf.d/notifications.conf"
