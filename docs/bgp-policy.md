# BGP routing policy

AS215932 announces a single aggregate, `2a0c:b641:b50::/44`, from three core
routers, and originates two /48 more-specifics from one of them. This document
records *why* the policy looks the way it does, so the next person to touch a
route-map does not have to re-derive it from packet captures.

Live configs are `configs/<router>/frr.conf` — hand-maintained and pushed verbatim
by `ansible/roles/frr/` (nothing is templated). Static invariants are enforced by
`tests/iac/test_frr_static.py`.

## Topology recap

| Router | Loopback | Upstreams | Role |
|---|---|---|---|
| cr1-nl1 | `::a` | AS34872 Servperso NL | core |
| cr1-de1 | `::b` | AS34872 Servperso DE, AS210233 | core |
| cr1-ch1 | `::c` | AS58057 Securebit CH, SBIX RS AS56755 | core, **preferred** |
| rtr | `::d` | none — transit-free | OVH edge, hosts the VM estate |

All four run a full iBGP mesh (no route reflector) over WireGuard, with
`next-hop-self` on every session. rtr is where every customer and infra VM lives,
so rtr's best-path choice decides egress for the whole estate.

## Local-preference ladder

Local-pref is per-router. Two independent ladders exist.

**On the cores** — which upstream to use for traffic leaving that router:

| LP | Applies to | Where |
|---|---|---|
| 200 | IXP route-server routes (`IXP-IN`) | cr1-ch1 |
| 150 | CDN overrides: Cloudflare `_13335$`, Fastly `_54113$` (`TRANSIT-IN` seq 4/5) | cr1-ch1 |
| 100 | default — everything else | all |
| 80 | anything transiting AS24961 (`TRANSIT-IN` seq 5) | cr1-de1 |

IX routes outrank the CDN overrides deliberately: peering is cheaper and shorter
than transit, so a CDN reachable over SBIX should never be dragged back onto
Securebit by an override.

**On rtr** — which core to hand traffic to (`IBGP-*-IN`, applied inbound on each
iBGP session):

| LP | Core |
|---|---|
| 200 | cr1-ch1 |
| 90 | cr1-nl1 |
| 80 | cr1-de1 |

These are strictly ordered rather than equal so egress is deterministic. Failover
is automatic and needs no operator action: lose ch1 and nl1 wins, lose both and
de1 wins.

### The `on-match next` rule

Any `route-map` clause that sets local-preference and is **not** the last clause
must end with `on-match next`, so the route still falls through to the shared
AS-path hygiene filter (`bgp as-path access-list 1`, which drops our own ASN,
private ASNs and absurd path lengths). Without it, a CDN override silently
becomes a hole in the transit filter — a matched route is accepted and the
route-map terminates.

This is not hypothetical: the Fastly clause ran live on cr1-ch1 for three weeks
without `on-match next`. `test_non_terminal_local_pref_clauses_fall_through_to_the_hygiene_filter`
now enforces it.

## Inbound steering — the part that actually mattered

Local-preference only controls **egress**. It has no effect on how the rest of the
internet routes *back* to us, and for a download the return path carries the bytes.

Measured 2026-07-25 from `mon`, against `deb.debian.org` (Fastly, AS54113):

| Path | Throughput |
|---|---|
| Native IPv6 | **52.7 KB/s** |
| Same object over NAT64 (IPv4) | **96.3 MB/s** |

Egress was already correct — the LP-150 override put the request on
`rtr → cr1-ch1 → Securebit → Cogent → Fastly`. But a `tcpdump` on rtr filtered to
`src net 2a04:4e42::/32` showed **every return packet arriving on wg0 (cr1-nl1)**,
with visible TCP reordering and a collapsed receive window. The session was fully
asymmetric: out via CH, back via NL. NAT64 was fast precisely because its return
path is rtr's OVH IPv4 address, which bypasses the overlay entirely.

Cumulative tunnel counters on rtr confirmed it was systemic, not a single flow —
77% of all return traffic was landing on the two degraded cores:

| tunnel | core | RX (return) | TX (egress) |
|---|---|---|---|
| wg0 | cr1-nl1 | 229.6 GB | 36.5 GB |
| wg1 | cr1-de1 | 311.7 GB | 26.9 GB |
| wg2 | cr1-ch1 | 160.4 GB | 12.1 GB |

Two levers now steer inbound traffic, in increasing order of force.

### 1. AS-path prepending (a hint)

cr1-nl1 and cr1-de1 export through `TRANSIT-OUT-PREPEND-3X`, which prepends
`215932` three times. cr1-ch1 exports unprepended, so it presents the shortest
AS-path back to AS215932.

Prepending is only advisory — every AS weighs its own local-pref before AS-path
length, so a network that buys transit from AS34872 and peers with Securebit may
ignore it entirely. It costs nothing and helps at the margin, but it is not what
makes the policy work.

### 2. /48 more-specifics from cr1-ch1 only (deterministic)

rtr originates `2a0c:b641:b50::/48` (infra) and `2a0c:b641:b51::/48` (customer
VMs). Only cr1-ch1 exports them, via its own `AS215932v6-out-ch1` prefix-list;
nl1 and de1 continue to export the `/44` alone.

Longest-prefix-match is honoured by every router on the internet regardless of
local policy, so return traffic for the infra and customer estates lands on
cr1-ch1 unconditionally. The `/44` stays announced from all three cores, so this
degrades gracefully: if cr1-ch1 or its tunnel to rtr fails, the iBGP session drops,
the /48s are withdrawn, and the `/44` still announced by nl1/de1 takes over.

**Do not announce anything longer than a /48.** The ROA for
`2a0c:b641:b50::/44` (origin 215932) carries `maxLength 48`. A /49 or longer would
be RPKI **Invalid** and dropped by every validating network — unreachable, which
is far worse than slow. Verify before changing:

```bash
curl -s "https://stat.ripe.net/data/rpki-roas/data.json?resource=2a0c:b641:b50::/44" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["data"]["roas"])'
```

If the aggregate is ever re-issued with a different maxLength, the more-specifics
must be re-checked before the next FRR deploy.

## Transit and IX filters

Every eBGP neighbour has both an inbound and an outbound route-map — enforced by
`test_external_bgp_neighbors_have_inbound_and_outbound_route_maps`.

- **Inbound** (`TRANSIT-IN` / `IXP-IN`) matches `as-path access-list 1`: denies our
  own ASN (loop prevention), private 16- and 32-bit ASN ranges, and paths longer
  than 200 characters.
- **Outbound** (`TRANSIT-OUT` / `IXP-OUT`) matches a prefix-list, never an as-path.
  `AS215932v6-out` permits exactly the aggregate on every router and is pinned by
  `test_transit_export_prefix_list_only_permits_canonical_aggregate`. cr1-ch1 adds
  `AS215932v6-out-ch1` for the more-specifics; that list is ch1-only and asserted
  as such.

We never export a route we did not originate — there is no `redistribute` into
BGP anywhere, and the aggregate comes from a `network` statement backed by a
blackhole/Null0 static.

## Rollback criteria

Revert the FRR change and re-deploy in reverse order (nl1 → ch1 → rtr) if any of:

- Either /48 shows RPKI **Invalid**, or fails to appear in public looking glasses
  within ~30 minutes.
- Native-IPv6 throughput does not improve materially over the 52.7 KB/s baseline.
- Icinga shows new problems on `mon` attributable to the change (compare against
  the problem list captured immediately before the deploy).
- A core loses its upstream session and traffic does not fall through to the next
  local-pref tier within a couple of minutes.

The `frr` role arms an `at(1)` watchdog (`frr_watchdog_minutes`, default 5) that
restores the previous config automatically if the deploy is not confirmed, so a
botched push self-heals without operator action.

## Known gaps

- **No throughput monitoring.** Existing checks watch BGP session state, prefix
  visibility and ICMP — all of which stayed green throughout this incident, because
  the failure mode is clean pings with crushed bulk TCP. Tracked in #351.
- **Single preferred upstream.** Concentrating on cr1-ch1 means Securebit carries
  most traffic. The structural fix is IX peering where the CDNs actually are
  (AMS-IX / NL-ix / FrysIX) — tracked in #138.
- The Batfish snapshot under `tests/iac/batfish/snapshot/` is a simplified 3-node
  model that predates cr1-ch1 and does not reflect this policy.
