#!/usr/bin/env python3
"""
check_dns_soa_ecmp.py — Icinga2 plugin probing a remote DNS server with
N source ports, classifying probes into ECMP halves, computing per-path
success rate.

Background — issue #27: existing dns-soa-* checks fire a single dig from
mon. The kernel's ephemeral source port determines which ECMP next-hop
rtr's overlay VRF picks (wg0 → cr1-nl1 or wg1 → cr1-de1). When one path
breaks (issue #25 NDP outage), ~50% of probes fail randomly — visible to
operators as flap, masking the structural single-path-down failure.

This plugin sweeps source ports across both halves of the ephemeral range
and reports per-half success. Alerts:
  OK       both halves >= 90%
  WARNING  one half between 50% and 90% (single-path partial)
  CRITICAL either half < 50% (single-path-down — the failure mode the
           legacy check missed for hours)

Plain UDP DNS query implemented in stdlib socket — no dnspython
dependency.

Exit codes follow Nagios convention:
  0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN.
"""
from __future__ import annotations

import argparse
import socket
import struct
import sys
import time

LOW_HALF_PORT_RANGE = (32768, 40959)   # rough lower half of typical ephemeral range
HIGH_HALF_PORT_RANGE = (49152, 60999)  # rough upper half


def encode_dns_query(qname: str, qtype: int = 6) -> tuple[bytes, int]:
    """Build a minimal DNS query packet. Returns (bytes, txid)."""
    txid = int(time.time() * 1000) & 0xFFFF
    flags = 0x0100  # standard query, RD=1
    header = struct.pack(">HHHHHH", txid, flags, 1, 0, 0, 0)
    body = b""
    for label in qname.encode("ascii").split(b"."):
        if label:
            body += bytes([len(label)]) + label
    body += b"\x00"
    body += struct.pack(">HH", qtype, 1)  # QTYPE, QCLASS=IN
    return header + body, txid


def probe(target: str, qname: str, src_port: int, family: int, timeout: float) -> bool:
    """Send one DNS query from a fixed source port. Return True on a
    well-formed reply with matching txid, False on timeout / error."""
    pkt, txid = encode_dns_query(qname)
    sock = socket.socket(family, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        bind_addr = "::" if family == socket.AF_INET6 else "0.0.0.0"
        sock.bind((bind_addr, src_port))
        sock.sendto(pkt, (target, 53))
        data, _ = sock.recvfrom(4096)
        if len(data) < 12:
            return False
        reply_txid = struct.unpack(">H", data[:2])[0]
        return reply_txid == txid
    except (socket.timeout, OSError):
        return False
    finally:
        sock.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-H", "--host", required=True, help="DNS server to probe")
    parser.add_argument("-z", "--zone", required=True, help="Zone to query (SOA)")
    parser.add_argument("-n", "--probes-per-half", type=int, default=15,
                        help="Probes per ECMP half (default: 15)")
    parser.add_argument("-t", "--timeout", type=float, default=2.0,
                        help="Per-probe timeout seconds (default: 2.0)")
    parser.add_argument("-w", "--warn-pct", type=float, default=90.0,
                        help="Per-half success threshold for WARN floor (default: 90)")
    parser.add_argument("-c", "--crit-pct", type=float, default=50.0,
                        help="Per-half success threshold for CRIT floor (default: 50)")
    parser.add_argument("-6", "--ipv6", action="store_true", help="Probe over IPv6")
    args = parser.parse_args()

    family = socket.AF_INET6 if args.ipv6 else socket.AF_INET

    # Sweep N distinct source ports per half. Skipping ports already in use
    # is handled by the OS; we just retry on bind failure.
    def sweep(port_range: tuple[int, int]) -> int:
        successes = 0
        low, high = port_range
        # Pick evenly spaced ports across the half.
        step = max(1, (high - low) // args.probes_per_half)
        for i in range(args.probes_per_half):
            port = low + i * step
            for _ in range(3):
                try:
                    if probe(args.host, args.zone, port, family, args.timeout):
                        successes += 1
                    break
                except OSError:
                    port += 1
                    continue
        return successes

    low_ok = sweep(LOW_HALF_PORT_RANGE)
    high_ok = sweep(HIGH_HALF_PORT_RANGE)
    n = args.probes_per_half
    low_pct = (100.0 * low_ok) / n if n else 0.0
    high_pct = (100.0 * high_ok) / n if n else 0.0

    msg = (
        f"low-half {low_ok}/{n} ({low_pct:.0f}%), "
        f"high-half {high_ok}/{n} ({high_pct:.0f}%)"
    )
    perfdata = (
        f" | low_pct={low_pct:.0f};{args.warn_pct};{args.crit_pct};0;100 "
        f"high_pct={high_pct:.0f};{args.warn_pct};{args.crit_pct};0;100"
    )

    if low_pct < args.crit_pct or high_pct < args.crit_pct:
        print(f"CRITICAL: ECMP path down — {msg}{perfdata}")
        return 2
    if low_pct < args.warn_pct or high_pct < args.warn_pct:
        print(f"WARNING: ECMP path degraded — {msg}{perfdata}")
        return 1
    print(f"OK: both ECMP paths healthy — {msg}{perfdata}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
