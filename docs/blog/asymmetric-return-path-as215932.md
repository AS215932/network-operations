# Clean pings, 50 KB/s downloads: debugging an asymmetric IPv6 return path

*How AS215932 spent three months fixing the wrong direction, and what the traffic engineering could and couldn't buy.*

---

## The symptom

Customer VMs on AS215932 couldn't bootstrap. `apt` would crawl. `pip install` would stall and eventually die on a broken connection mid-wheel. Docker pulls that should take twenty seconds took most of an hour, or never finished.

The measurement that frames everything, taken from a monitoring host inside our network against `deb.debian.org` (Fastly, AS54113):

```
native IPv6                    52.7 KB/s
same object, forced via NAT64  96.3 MB/s
```

Same file. Same server. Same second. A factor of **1,800**.

## Why it hid for so long

Every alarm was green, and stayed green throughout:

- BGP sessions: established, stable, months of uptime.
- Prefix visibility: announced, RPKI valid.
- ICMP: **0% loss, 24 ms RTT**, rock steady.

That combination — pristine ICMP with collapsed bulk TCP — is the signature of congestion or policing on a path, and it is invisible to every check we had. Ping is a handful of small packets. Nothing in our monitoring measured *throughput*, so nothing in our monitoring could see the actual failure. We had alerts for the network being down and none for the network being useless.

The bug had been chased three times across three issues. Each time it was mitigated by pinning a specific hostname to NAT64, which worked, which made the problem disappear, which meant nobody found the cause.

## Following the packets

The forward path was easy and looked correct:

```
$ traceroute deb.debian.org
 1  2a0c:b641:b50:2::1              0.4 ms   # rtr, our edge
 2  2a0c:b641:b50:ff06::           14.6 ms   # WireGuard tunnel to cr1-ch1 (Zurich)
 3  2a09:4c0:100:2d88::8bfa        14.9 ms   # Securebit, AS58057
 4  2001:978:2:bd::29:1            24.3 ms
 5  2001:550:0:1000::…             23.7 ms   # Cogent, AS174
 …                                           # Fastly
```

Requests were leaving via Zurich, exactly as our routing policy intended. A local-preference override installed months earlier was doing its job.

So we looked at the other direction. On the edge router, the cumulative byte counters per WireGuard tunnel — one to each of our three core routers — over roughly nine days:

| tunnel | core | **RX** (traffic arriving) | **TX** (traffic leaving) |
|---|---|---|---|
| wg0 | cr1-nl1 (Amsterdam) | 229.6 GB | 36.5 GB |
| wg1 | cr1-de1 (Düsseldorf) | **311.7 GB** | 26.9 GB |
| wg2 | cr1-ch1 (Zurich) | 160.4 GB | 12.1 GB |

Two things fall out immediately.

**77% of everything arriving** came through the two cores we'd already identified as degraded. And **only 16% of what we sent** left via Zurich — the local-pref override covered two CDN AS numbers, so everything else still scattered on BGP tie-break.

Then the decisive capture. A download running, `tcpdump` on the edge router filtered to the CDN's prefix, watching which tunnel the *responses* arrived on:

```
$ tcpdump -i any 'src net 2a04:4e42::/32 and inbound'
wg0  In  IP6 2a04:4e42::644.443 > …:60494: seq 1153086669:1153088017, win 276, length 1348
wg0  In  IP6 2a04:4e42::644.443 > …:60494: seq 1348:2696,   win 276, length 1348
wg0  In  IP6 2a04:4e42::644.443 > …:60494: seq 22916:24264, win 276, length 1348   # <-- out of order
wg0  In  IP6 2a04:4e42::644.443 > …:60494: seq 17524:18872, win 276, length 1348
wg0  In  IP6 2a04:4e42::644.443 > …:60494: seq 18872:20220, win 276, length 1348
```

Every single packet on `wg0`. Not one on `wg2`. Requests went out through Zurich; responses came back through Amsterdam. And note segment `22916` arriving ahead of `17524` — visible **reordering**, which with a collapsed window is precisely how you get 50 KB/s on a link with 24 ms RTT and no loss.

## Why NAT64 was fast

This is the detail that had been misleading everyone. NAT64 wasn't fast because IPv4 is better. It was fast because **NAT64 return traffic doesn't use the overlay at all.**

Our IPv6 estate is reachable only through our own cores, over WireGuard tunnels. But NAT64 translation happens on the edge router itself, using an IPv4 address belonging to the hosting provider. Replies to that address come back through the provider's network, straight to the router, never touching a tunnel or a core.

Every "fix" that pinned a hostname to NAT64 was routing *around* the broken return path. That's why they worked, and why nothing was ever learned.

## The thing we'd got backwards

Local-preference decides which path *we* use to send traffic. It has no effect whatsoever on how the rest of the internet routes traffic *back* to us.

For a download, the bytes are in the return direction. We had spent three months tuning the direction that carries the HTTP GET.

Inbound is governed by what we announce and how attractive it looks. There, our config was lopsided: one core prepended its announcements three times (from an earlier incident), and the other two announced plain. Nothing was deliberately steering traffic toward the good path.

## The fix

Three changes, deployed in a deliberate order — establish the good path before de-preferring the old one.

**1. Egress, on the edge router.** Its three iBGP sessions had no inbound policy at all, so all three cores tied at the default local-pref and the choice fell to tie-break. Pin it:

```
route-map IBGP-CH1-IN permit 10
 set local-preference 200
route-map IBGP-NL1-IN permit 10
 set local-preference 90
route-map IBGP-DE1-IN permit 10
 set local-preference 80
```

Strictly ordered rather than equal, so egress is deterministic. Failover stays automatic: lose Zurich and Amsterdam wins, lose both and Düsseldorf wins.

**2. Ingress, the soft lever.** Prepend our own ASN three times on the Amsterdam upstream, matching what Düsseldorf already did, leaving Zurich as the only unprepended announcement:

```
route-map TRANSIT-OUT-PREPEND-3X permit 10
 match ipv6 address prefix-list AS215932v6-out
 set as-path prepend 215932 215932 215932
```

**3. Ingress, the hard lever.** Prepending is only a hint — every AS weighs its own local-pref before AS-path length, so a network that buys transit from our Amsterdam upstream can ignore it entirely. The deterministic mechanism is **longest-prefix-match**, which every router on the internet honours regardless of local policy. Announce two `/48` more-specifics from Zurich *only*, while all three cores keep announcing the covering `/44`:

```
ipv6 prefix-list AS215932v6-out-ch1 seq 5  permit 2a0c:b641:b50::/44
ipv6 prefix-list AS215932v6-out-ch1 seq 10 permit 2a0c:b641:b50::/48
ipv6 prefix-list AS215932v6-out-ch1 seq 15 permit 2a0c:b641:b51::/48
```

This also fails safe. If Zurich or its tunnel drops, the iBGP session goes with it, the /48s are withdrawn, and the /44 still announced by the other two takes over. Withdrawal *is* the failover path — no additional logic.

**One hard precondition.** Our ROA for the aggregate carries `maxLength 48`, so both /48s are RPKI Valid. A /49 would have been **Invalid and dropped** by every validating network — unreachable, which is dramatically worse than slow. Check before you announce:

```bash
curl -s "https://stat.ripe.net/data/rpki-roas/data.json?resource=<aggregate>"
```

## What it bought, and what it didn't

Deployed, verified, no regressions — no session flaps, monitoring clean.

**Egress: fixed.** All traffic from the estate now deterministically prefers Zurich, up from 16%.

**Return path: unchanged.** Throughput still ~54 KB/s. Responses still arriving on `wg0`.

The prepend had propagated widely and correctly — 365 of 372 observed peer-paths carried it. The /48s were live, RPKI Valid, and visible. Everything worked as designed, and the outcome didn't move.

## The measurement that explained it

The instinct here is to assume the more-specifics are being filtered. Testing that properly means comparing them against the covering aggregate as a **control**, using a real-time source:

| prefix | peer-paths via Zurich (`58057`/`56755`) | via Amsterdam / Düsseldorf | total |
|---|---|---|---|
| `2a0c:b641:b50::/48` | 6 | — | **6** |
| `2a0c:b641:b51::/48` | 6 | — | **6** |
| `2a0c:b641:b50::/44` | 6 | 365 (prepended) | **372** |

The /48s reach **exactly the same six peer-paths** as Zurich's own /44. If they were being filtered as more-specifics, they'd reach *fewer* peers than the aggregate does through that same upstream. They don't. Nothing is filtering them.

What the table actually shows is that **our Zurich transit accounts for about 1.6% of the observed paths to our network.** It's a small, high-quality upstream with a correspondingly small footprint.

And that's the answer. **You cannot steer traffic onto a path the other network doesn't have.** A more-specific announced only through a transit with 1.6% reach is invisible to the other 98.4%, who continue to use the /44 via Amsterdam — now prepended, but still the only route they've ever been offered. Prepending made the bad path less attractive; it could not conjure a better one into their table.

Traffic engineering redistributes traffic among the paths your neighbours already know about. It cannot create reach. That's a peering problem, and no route-map solves it.

## Two methodology notes that cost us real time

**Don't verify a routing change with a batch snapshot.** Our first propagation check reported `0 / 324` peers seeing either /48 — which reads exactly like "filtered upstream," and we wrote that conclusion into an issue, a doc, and a PR comment before noticing the payload's own timestamp was nearly ten hours old and predated the deploy. Public BGP data sources mix real-time and periodically-recomputed datasets behind near-identical endpoints. Use the one computed at query time, and read the timestamp before the number.

**Always measure against a control.** "Being filtered" and "announced through a narrow-reach transit" produce identical readings if you only measure the prefix you changed. The aggregate is the control that separates them, and it costs one extra query.

Both mistakes have the same shape: data that looked conclusive, wasn't, and didn't say so.

## Where this leaves us

The changes are correct and worth keeping — deterministic egress, a closed filter hole in our transit policy, and inbound steering that will start working the moment it has somewhere to steer *to*. They're a prerequisite, not a fix.

The actual fix is reach: peering at the exchanges where the CDNs we depend on actually are. When a core has meaningful presence, the /48 mechanism already in place begins pulling return traffic without another config change.

Until then, NAT64 remains our fastest path to a large chunk of the IPv6 internet — which is a genuinely strange sentence to write about an IPv6-first network, and a precise measure of how much reach matters.

We're also adding throughput probes, because the entire incident lived in the gap between "the network is up" and "the network is usable," and for three months we only measured the first one.

---

*AS215932 is an IPv6-first ISP running on a handful of cores. Configs, incidents, and the issues referenced here are public at [github.com/AS215932/network-operations](https://github.com/AS215932/network-operations).*
