#!/usr/bin/env python3
"""Icinga check that performs a real RFC 5389 STUN binding request."""

from __future__ import annotations

import argparse
import ipaddress
import secrets
import socket
import struct
import sys


COOKIE = 0x2112A442


def mapped_address(packet: bytes, transaction_id: bytes) -> str:
    if len(packet) < 20:
        raise ValueError("short STUN response")
    message_type, length, cookie = struct.unpack("!HHI", packet[:8])
    if message_type != 0x0101 or cookie != COOKIE or packet[8:20] != transaction_id:
        raise ValueError("invalid STUN binding response")
    if len(packet) < 20 + length:
        raise ValueError("truncated STUN attributes")
    offset = 20
    while offset + 4 <= 20 + length:
        attr_type, attr_length = struct.unpack("!HH", packet[offset : offset + 4])
        value = packet[offset + 4 : offset + 4 + attr_length]
        offset += 4 + ((attr_length + 3) & ~3)
        if attr_type not in {0x0001, 0x0020} or len(value) < 8:
            continue
        family = value[1]
        address_size = 4 if family == 0x01 else 16 if family == 0x02 else 0
        if address_size == 0 or len(value) < 4 + address_size:
            continue
        raw = bytearray(value[4 : 4 + address_size])
        if attr_type == 0x0020:
            mask = struct.pack("!I", COOKIE) + transaction_id
            for index in range(address_size):
                raw[index] ^= mask[index]
        return str(ipaddress.ip_address(bytes(raw)))
    raise ValueError("response has no mapped address")


def probe(host: str, port: int, family: int, timeout: float) -> str:
    transaction_id = secrets.token_bytes(12)
    request = struct.pack("!HHI", 0x0001, 0, COOKIE) + transaction_id
    errors: list[str] = []
    for af, socktype, proto, _, address in socket.getaddrinfo(
        host, port, family=family, type=socket.SOCK_DGRAM
    ):
        try:
            with socket.socket(af, socktype, proto) as client:
                client.settimeout(timeout)
                client.sendto(request, address)
                packet, _ = client.recvfrom(2048)
            return mapped_address(packet, transaction_id)
        except (OSError, ValueError) as exc:
            errors.append(type(exc).__name__)
    raise RuntimeError(", ".join(errors) or "no usable address")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-H", "--host", required=True)
    parser.add_argument("-p", "--port", type=int, default=3478)
    parser.add_argument("-t", "--timeout", type=float, default=3.0)
    family = parser.add_mutually_exclusive_group(required=True)
    family.add_argument("-4", dest="family", action="store_const", const=socket.AF_INET)
    family.add_argument("-6", dest="family", action="store_const", const=socket.AF_INET6)
    args = parser.parse_args()
    try:
        address = probe(args.host, args.port, args.family, args.timeout)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"CRITICAL - STUN binding failed ({type(exc).__name__})")
        return 2
    print(f"OK - STUN binding returned {address}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
