#!/usr/bin/env python3
"""Take a screenshot of a QEMU VM via QMP socket.

Usage:
    qmp-screenshot.py <dom-id>

Saves screenshot to /var/xen/qemu/root-<dom-id>/tmp/screen.ppm
Requires the /tmp directory inside the QEMU chroot to exist and be writable:
    mkdir -p /var/xen/qemu/root-<dom-id>/tmp
    chmod 777 /var/xen/qemu/root-<dom-id>/tmp
"""
import socket
import json
import time
import sys

def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <dom-id>")
        sys.exit(1)

    domid = sys.argv[1]
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(f"/var/run/xen/qmp-libxl-{domid}")
    sock.recv(4096)

    sock.sendall(json.dumps({"execute": "qmp_capabilities"}).encode() + b"\n")
    time.sleep(0.3)
    sock.recv(4096)

    sock.sendall(json.dumps({
        "execute": "screendump",
        "arguments": {"filename": "/tmp/screen.ppm"}
    }).encode() + b"\n")
    time.sleep(0.5)
    resp = sock.recv(4096).decode()
    print(resp)

    sock.close()
    print(f"Screenshot saved to /var/xen/qemu/root-{domid}/tmp/screen.ppm")

if __name__ == "__main__":
    main()
