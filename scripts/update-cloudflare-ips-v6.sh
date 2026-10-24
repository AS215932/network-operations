#!/bin/sh
# Refresh configs/inf-sthlm/cloudflare-ips-v6 from Cloudflare's published list.
set -eu
out="$(dirname "$0")/../configs/inf-sthlm/cloudflare-ips-v6"
list="$(curl -fsS https://www.cloudflare.com/ips-v6)"
{ echo "# Cloudflare IPv6 ranges, source https://www.cloudflare.com/ips-v6 (fetched $(date -u +%F))."
  echo "# Loaded into pf table <cloudflare6>; refresh with scripts/update-cloudflare-ips-v6.sh."
  echo "$list"; } > "$out"
echo "Updated $out; keep set_real_ip_from in configs/inf-sthlm/nginx.conf in sync (test enforces it)."
