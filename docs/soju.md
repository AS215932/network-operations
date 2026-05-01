# Soju IRC bouncer — irc.as215932.net

Soju runs on the `irc` VM (`2a0c:b641:b50:2::80`) and serves IRCS directly on
port 6697, with its own Let's Encrypt certificate. Caddy on `proxy` is NOT
involved.

Public hostname: `irc.as215932.net`. AAAA in
[configs/as215932.net.zone](../configs/as215932.net.zone). Inbound 6697/tcp
+ 80/tcp open to the world (see [host_vars/irc.yml](../ansible/inventory/host_vars/irc.yml)).

## Certificate issuance — HTTP-01 (current)

DNS-01 against Knot was tried first but Openprovider secondaries don't
refresh in time for LE's challenge query (see project_openprovider_notify
memory). HTTP-01 sidesteps DNS entirely: certbot briefly binds tcp/80,
LE fetches `/.well-known/acme-challenge/<token>` over IPv6, done.

```sh
sudo certbot certonly --non-interactive --agree-tos \
  --email admin@servify.network \
  --standalone --preferred-challenges http \
  -d irc.as215932.net
```

Renewal uses the same plumbing — `certbot renew` reads the per-cert config
in `/etc/letsencrypt/renewal/irc.as215932.net.conf` and re-runs standalone.
Soju is not bound to :80 so there's no conflict.

## DNS-01 plumbing (kept around as a fallback)

If Openprovider ever honors NOTIFY (or we add a self-hosted secondary),
DNS-01 against Knot is ready:

- Knot ACL `irc-update` for the as215932.net zone, in [configs/knot.conf.j2](../configs/knot.conf.j2).
- `/etc/letsencrypt/rfc2136.ini` on irc with the hyrule-dns TSIG secret (mode 0600).
- `python3-certbot-dns-rfc2136` installed.

To switch over: drop `--standalone --preferred-challenges http` from the
certbot invocation and use `--dns-rfc2136 --dns-rfc2136-credentials …
--dns-rfc2136-propagation-seconds 3600` instead.

## Soju config

`/etc/soju/config`:

```
listen ircs://[::]:6697
listen unix+admin://

hostname irc.as215932.net

tls /etc/soju/tls/cert.pem /etc/soju/tls/key.pem

db sqlite3 /var/lib/soju/main.db
message-store fs /var/lib/soju/logs/
```

The cert + key at `/etc/soju/tls/{cert,key}.pem` are populated by the
deploy hook (next section). Soju reads them once at startup; after each
renewal the hook reloads/restarts the service.

## Deploy hook (cert renewal → soju reload)

`/etc/letsencrypt/renewal-hooks/deploy/soju.sh` (mode 0755):

```sh
#!/bin/sh
set -e
DOMAIN=irc.as215932.net
LIVE=/etc/letsencrypt/live/${DOMAIN}
[ -d "$LIVE" ] || { echo "no live dir for $DOMAIN"; exit 0; }
install -d -o soju -g soju -m 750 /etc/soju/tls
install -o soju -g soju -m 644 $LIVE/fullchain.pem /etc/soju/tls/cert.pem
install -o soju -g soju -m 600 $LIVE/privkey.pem  /etc/soju/tls/key.pem
systemctl reload soju 2>/dev/null || systemctl restart soju
```

Note: SIGHUP (`systemctl reload`) tells soju to re-read its main config but
doesn't reopen TLS — a full restart is needed when the cert changes. The
hook's restart fallback covers that.

## Create the admin user (manual)

```sh
sudo -u soju sojuctl -config /etc/soju/config user create -username svag -admin
# enter password when prompted
```

## Register networks

From an IRC client connected to soju as the admin user:

```
/msg BouncerServ network create -addr ircs://irc.ircnet.com:6697 -name ircnet -nick svag
/msg BouncerServ network create -addr ircs://irc.quakenet.org:6667 -name quakenet -nick svag
```

(QuakeNet's TLS on 6697 isn't universal — check the chosen server.)

## Verify

```sh
# TLS handshake from off-host
openssl s_client -connect irc.as215932.net:6697 -servername irc.as215932.net </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer -dates

# IRCS port reachability
nc -6 -vz irc.as215932.net 6697
```
