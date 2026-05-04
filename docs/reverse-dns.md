# Reverse DNS (PTR) for AS215932

We delegate the reverse zone for our /48
(`2a0c:b641:b50::/48`) from RIPE and serve PTRs from our own Knot
nameservers (`ns1.servify.network` primary, `ns2.servify.network`
secondary). PTR targets resolve to `as215932.net` hostnames.

## Zone-name math (the 12-nibble gotcha)

A reverse zone for an IPv6 prefix has one label per nibble of prefix
length. **A /48 = 12 nibbles**, full stop — including any leading
zeroes in the second-to-last group of the shorthand.

```
shorthand:    2a0c:b641:b50::/48
expanded:     2a0c:b641:0b50           ← that 0 is significant
nibbles:      2 a 0 c b 6 4 1 0 b 5 0  (12 nibbles, count them)
reversed:     0.5.b.0.1.4.6.b.c.0.a.2
zone:         0.5.b.0.1.4.6.b.c.0.a.2.ip6.arpa
```

The frequent error is dropping the leading `0` of `0b50` and ending up
with **11 labels** (`0.5.b.1.4.6.b.c.0.a.2.ip6.arpa`). It looks right
at a glance and it parses. But RIPE's reverse-domain DNS-Check sends
an SOA query for the **actual** zone of the /48 they're delegating —
12 labels — so an 11-label zone returns `REFUSED` from every endpoint
the check probes (ns1/v4, ns2/v4, ns2/v6 — three identical errors in
the GUI). Likewise, querying the wrong name from anywhere on the
internet just gets `REFUSED` because Knot isn't authoritative for it.

If you ever need to redo the math for a different prefix length: pad
each group to 4 hex digits, concatenate, reverse, dot-separate,
append `.ip6.arpa.`. A /44 is 11 nibbles, a /56 is 14, a /64 is 16,
and so on.

## Zone file

Source: [`configs/0.5.b.0.1.4.6.b.c.0.a.2.ip6.arpa.zone`](../configs/0.5.b.0.1.4.6.b.c.0.a.2.ip6.arpa.zone),
deployed by the [`knot` Ansible role](../ansible/roles/knot/tasks/zones.yml)
to `/var/lib/knot/zones/<zone>.zone` on the primary (`dns` VM, 
`2a0c:b641:b50:2::10`). The secondary AXFR-pulls — no per-host edits.

Zones served by the nameservers are declared in
[`ansible/inventory/group_vars/nameservers.yml`](../ansible/inventory/group_vars/nameservers.yml)
under `knot_zones`. Add the reverse zone there if you ever stand up
another one (e.g. a future /48 for a downstream).

Required structure:

- `$ORIGIN <zone>.` so all records below can use relative names.
- SOA `MNAME` = `ns1.servify.network.`, `RNAME` = `admin.servify.network.`.
  Serial is `YYYYMMDDNN` (NN = 01..99 within a day). If a zone-file
  edit doesn't appear to take effect after a reload, the journal is
  shadowing it — see "Adding or moving a PTR" below for the purge.
- **Two `NS` records, both inside the zone**: `ns1` + `ns2`. RIPE's
  DNS-Check enforces ≥2 nameservers for reverse-domain delegation;
  delegating with one will fail validation.
- PTR records keyed by the *relative* nibble-reversed address. The
  trick: take the full address, expand it, reverse all 32 nibbles,
  drop the trailing labels that match `$ORIGIN`. What's left is the
  PTR's owner name.

```dns
; full address: 2a0c:b641:0b50:0002:0000:0000:0000:0001
; reversed:     1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.2.0.0.0.0.5.b.0.1.4.6.b.c.0.a.2
; minus $ORIGIN (last 12 labels): 1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.2.0.0.0
1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.2.0.0.0   IN  PTR  rtr.as215932.net.
```

PTRs the file currently carries (all under the `:2::/64` infra
subnet):

| Suffix    | Host  | PTR target              |
|-----------|-------|-------------------------|
| `:2::1`   | rtr   | `rtr.as215932.net.`     |
| `:2::10`  | dns   | `dns.as215932.net.`     |
| `:2::20`  | api   | `api.as215932.net.`     |
| `:2::30`  | web   | `web.as215932.net.`     |
| `:2::40`  | proxy | `proxy.as215932.net.`   |
| `:2::50`  | mon   | `mon.as215932.net.`     |
| `:2::60`  | vpn   | `vpn.as215932.net.`     |
| `:2::70`  | xoa   | `xoa.as215932.net.`     |
| `:2::80`  | irc   | `irc.as215932.net.` (host not yet provisioned) |

The forward `as215932.net.` records live in
[`configs/as215932.net.zone`](../configs/as215932.net.zone) and must
match — RIPE's reverse-domain check doesn't enforce forward-confirmed
reverse DNS but mail receivers and `traceroute` users will.

## RIPE delegation (one-time per prefix)

The reverse zone is delegated to our nameservers via a
**`reverse-domain`** object in the RIPE database. Submit through the
[RIPE webupdates GUI](https://apps.db.ripe.net/db-web-ui/webupdates)
authenticating with NCC SSO. The REST API for `reverse-domain`
creation hit a `MNT-LOWER` / LIR-/29 auth wall during this
deployment; the GUI accepts it because SSO carries different scopes.

Object template (also kept at `/tmp/reverse-domain.rpsl` during the
RIPE work):

```rpsl
domain:         0.5.b.0.1.4.6.b.c.0.a.2.ip6.arpa
descr:          AS215932 reverse DNS — 2a0c:b641:b50::/48
admin-c:        DH8824-RIPE
tech-c:         DH8824-RIPE
zone-c:         DH8824-RIPE
nserver:        ns1.servify.network
nserver:        ns2.servify.network
mnt-by:         SERVIFY-MNT
source:         RIPE
```

On submit, RIPE's DNS-Check probes both nameservers over v4 and v6,
asking for SOA on the exact `domain:` value. **All probed endpoints
must return `NOERROR + AA + SOA`** before the object is created. If
any returns `REFUSED`, the most likely cause is the 12-nibble
zone-name mismatch above; if any returns `SERVFAIL`, the zone exists
but isn't loaded; if any times out, the v4 DNAT or v6 path is broken
(see [rtr-vrf.md](rtr-vrf.md)).

Once accepted, RIPE's `ip6.arpa` parent inserts the NS records that
make `dig +trace` queries from the world end up at our nameservers.

## Adding or moving a PTR

1. Edit `configs/0.5.b.0.1.4.6.b.c.0.a.2.ip6.arpa.zone`. Compute the
   relative owner name as shown above.
2. Bump the SOA serial (`YYYYMMDDNN` — see
   [feedback_serial_format memory]).
3. Apply with the knot playbook (TSIG secret loaded from
   `secrets.local.sh`):
   ```bash
   cd ansible
   set -a; source ../secrets.local.sh; set +a
   ansible-playbook playbooks/knot.yml --tags apply \
       -e '{"knot_apply":true}'
   ```
   This copies the zone file to `dns:/var/lib/knot/zones/` and
   triggers `knotc reload`. `ns2` picks up the change via NOTIFY +
   AXFR (TSIG key `hyrule-dns`).
4. Verify both nameservers serve the new serial — see below.

If the file change doesn't appear to take effect, the per-zone journal
under `/var/lib/knot/journal/` is shadowing it:

```bash
ssh dns sudo systemctl stop knot
ssh dns sudo rm -rf /var/lib/knot/journal /var/lib/knot/timers
ssh dns sudo systemctl start knot
```

Then re-run the playbook. ns2 will refresh from the new serial via
NOTIFY + AXFR.

## Verification

```bash
# SOA + AA on both nameservers, both families
dig @ns1.servify.network 0.5.b.0.1.4.6.b.c.0.a.2.ip6.arpa SOA
dig @ns2.servify.network 0.5.b.0.1.4.6.b.c.0.a.2.ip6.arpa SOA
dig @46.105.40.223       0.5.b.0.1.4.6.b.c.0.a.2.ip6.arpa SOA
dig @54.38.14.218        0.5.b.0.1.4.6.b.c.0.a.2.ip6.arpa SOA
# Expect: NOERROR, flags ;; flags: ... aa, identical SOA serial.

# A specific PTR resolves
dig +short -x 2a0c:b641:b50:2::10
# Expect: dns.as215932.net.

# Delegation chain reaches our nameservers (queries any recursor;
# the answer comes from RIPE's ip6.arpa parent zone)
dig +short NS 0.5.b.0.1.4.6.b.c.0.a.2.ip6.arpa
# Expect: ns1.servify.network. ns2.servify.network.

# Full trace from the public root
dig +trace -x 2a0c:b641:b50:2::10
```
