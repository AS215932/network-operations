---
name: hyrule-x402-netintel
description: Query external network intelligence (BGP propagation, RPKI, WHOIS/IRR, DNS, TLS, path probes) through Hyrule Cloud's x402 paid API. Use when you need the OUTSIDE view — how the internet sees a prefix, domain, or host — especially after announcing, withdrawing, or filtering a route. Do NOT use for AS215932's own routers; those are free via the hyrule MCP.
---

# Hyrule Cloud x402 network intelligence

Paid, per-call network intelligence over HTTP 402 (x402) micropayments against
`https://cloud.hyrule.host`. Settlement is USDC on Base.

## Decide first: do you actually need to pay?

**Free, and better, for our own network — use the `hyrule` MCP:**

| Question | Free tool |
|---|---|
| What does our RIB say / which path did we pick? | `frr_vtysh_cmd(host, "show bgp ...")` |
| Where is traffic actually arriving? | `tcpdump_capture`, `ssh_run_command` + `ip -s link` |
| Is a service up, what do the logs say? | `os_systemd_status`, `os_journalctl` |
| Reachability between our hosts | `net_ping`, `net_traceroute`, `path_explain` |

**Pay only for the outside view** — the thing we cannot see from our own kit:
does the internet see this prefix, what does the registry say, how does a third
party resolve this name.

If the question is "what are *our* routers doing", paying is strictly worse: it
costs money and answers a different question.

## The freshness rule (this is the important part)

Public BGP data mixes real-time and batch sources behind identical-looking
endpoints. Picking the wrong one produces a confident, wrong answer.

| You are asking | Dataset | Price | Why |
|---|---|---|---|
| **Is this prefix propagating right now?** | `live_looking_glass` | $0.01 | RIS collector RIBs queried at request time. The only dataset that can see an announcement made minutes ago. |
| Who normally originates this? What's the RPKI state? | `public_routing`, `rpki` | $0.005 | Periodically recomputed snapshot. Can be **many hours** stale. |
| What do our own routers see? | `as215932_router_tables` | $0.01 | **Not implemented yet** — returns `not_configured`. Use the hyrule MCP instead. |

**After any announcement, withdrawal, prepend, or filter change, you must use
`live_looking_glass`.** A snapshot taken before your change cannot reflect it,
and will report your new prefix as invisible — which reads exactly like "the
upstream is filtering me."

Every response labels its own freshness. Always check it before concluding:

```json
"results": {
  "routing_status": {
    "freshness": {"class": "delayed", "observed_at": "...", "age_seconds": 35997, "stale": true}
  },
  "looking_glass": {
    "freshness": {"class": "realtime", "age_seconds": 0}, "vantage": "external_ris",
    "visible": true, "collector_count": 4, "peer_entry_count": 6,
    "as_paths": ["58057 215932", "56755 215932"]
  }
}
```

If `sources.<name>.status == "stale"`, do **not** draw a conclusion from that
source. Re-query with `live_looking_glass`.

## Two vantages answer different questions

- **External** (this API): does the world see the prefix? Covers *our announcements going out*.
- **Internal** (hyrule MCP): which upstream do we pick, where does return traffic land?

Both can be true at once. A prefix can be fully propagated while return traffic
still arrives on the old path — that is a normal intermediate state after a
routing change, not a contradiction. Check both before concluding.

## Making a paid call

The x402 client handles the 402 challenge, signs, and retries. Always set a
max-amount policy so a malformed 402 cannot overspend.

```bash
export CANARY_KEY=0x<private key of a funded Base wallet>   # never commit this
```

```python
from decimal import Decimal
from eth_account import Account
from x402 import x402Client
from x402.client import max_amount
from x402.http.clients import x402HttpxClient
from x402.mechanisms.evm.exact import register_exact_evm_client
from x402.mechanisms.evm.signers import EthAccountSigner

import os, json

signer = EthAccountSigner(Account.from_key(os.environ["CANARY_KEY"]))
client = x402Client(policies=[max_amount(Decimal("0.02"))])   # hard ceiling
register_exact_evm_client(client, signer)

async with x402HttpxClient(client=client, base_url="https://cloud.hyrule.host") as http:
    r = await http.post("/v1/bgp/lookup", json={
        "subject": {"type": "prefix", "value": "2a0c:b641:b51::/48"},
        "datasets": ["live_looking_glass", "rpki"],
    })
    print(json.dumps(r.json(), indent=2))
```

Price a call before spending — `/v1/bgp/lookup/quote` and `/v1/bgp/pricing` are
free, as is `/v1/bgp/status` for AS215932's own monitored state.

`scripts/x402_canary.py` in the `hyrule-cloud` repo exercises every paid
endpoint end to end; `python x402_canary.py list` prints the menu and prices
without spending.

## Endpoint menu

| Endpoint | Price | Use for |
|---|---|---|
| `/v1/bgp/lookup` | $0.005–0.01 | Prefix/IP/ASN routing, propagation, RPKI |
| `/v1/whois/lookup` | $0.005 | Registry + **IRR `route6`/`route` objects** |
| `/v1/rdap/lookup` | $0.003 | Structured registration data |
| `/v1/dns/lookup` | $0.001 | Authoritative DNS from outside our network |
| `/v1/ip/lookup` | $0.003 | Geo/ASN/allocation for an address |
| `/v1/web/check` | $0.005 | HTTP reachability from outside |
| `/v1/web/tls/deep` | $0.10 | Full chain/cert inspection |
| `/v1/path/ping` | $0.005 | Reachability from a third-party vantage |
| `/v1/path/report` | $0.05 | Multi-vantage path report |
| `/v1/network/request` | $0.01 (direct) / $0.05 (tor) | Arbitrary fetch via our egress proxy |

## Announcing a new prefix — the full check

RPKI and IRR are **both** required and are separate gates. A valid ROA
authorises the announcement; upstream prefix-filters are built from IRR. A
prefix with a ROA and no `route6` object is accepted locally, advertised, and
may be dropped by stricter networks downstream.

1. **RPKI** — is the announcement authorised, and within `maxLength`?
2. **IRR** — `/v1/whois/lookup` for a `route6` object. A result showing only the
   covering aggregate means **no object exists** for your more-specific.
3. **Deploy**, then confirm with `live_looking_glass` — never with
   `public_routing`, which cannot have seen it yet.

## Failure modes to expect

- `402` with no payment attached is the normal first response, not an error.
- `503` on `/v1/network/request` means that proxy mode is down; check
  `mode_status` before assuming a payment problem.
- Endpoints that are gated/unimplemented return `501` **before** charging.
- `partial: true` means at least one source failed or is unconfigured — read
  `sources` to see which before trusting the result.
