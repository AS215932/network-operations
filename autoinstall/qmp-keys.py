#!/usr/bin/env python3
"""Send keystrokes to a QEMU VM via QMP socket.

Usage:
    qmp-keys.py <dom-id> <text>
    qmp-keys.py 28 "echo hello"

The QMP socket path is /var/run/xen/qmp-libxl-<dom-id>.
Supports full US keyboard layout including shifted characters.
"""
import socket
import json
import time
import sys

def qmp_send(sock, cmd, args=None):
    msg = {"execute": cmd}
    if args:
        msg["arguments"] = args
    sock.sendall(json.dumps(msg).encode() + b"\n")
    time.sleep(0.2)
    try:
        sock.recv(4096)
    except Exception:
        pass

def send_key(sock, key):
    qmp_send(sock, "send-key", {"keys": [{"type": "qcode", "data": key}]})

def send_shift_key(sock, key):
    qmp_send(sock, "send-key", {"keys": [
        {"type": "qcode", "data": "shift"},
        {"type": "qcode", "data": key}
    ]})

def send_string(sock, s):
    shift_map = {
        ":": "semicolon", "?": "slash", "!": "1", "@": "2",
        "#": "3", "$": "4", "%": "5", "^": "6", "&": "7",
        "*": "8", "(": "9", ")": "0", "_": "minus", "+": "equal",
        ">": "dot", "<": "comma", "{": "bracket_left", "}": "bracket_right",
        "|": "backslash", "~": "grave_accent", "\"": "apostrophe",
    }
    keymap = {
        "a": "a", "b": "b", "c": "c", "d": "d", "e": "e", "f": "f",
        "g": "g", "h": "h", "i": "i", "j": "j", "k": "k", "l": "l",
        "m": "m", "n": "n", "o": "o", "p": "p", "q": "q", "r": "r",
        "s": "s", "t": "t", "u": "u", "v": "v", "w": "w", "x": "x",
        "y": "y", "z": "z",
        "0": "0", "1": "1", "2": "2", "3": "3", "4": "4", "5": "5",
        "6": "6", "7": "7", "8": "8", "9": "9",
        ".": "dot", "/": "slash", "-": "minus", " ": "spc", "=": "equal",
        ",": "comma", "[": "bracket_left", "]": "bracket_right",
        "\\": "backslash", "`": "grave_accent", "'": "apostrophe",
        ";": "semicolon",
    }
    for c in s:
        if c == "\n":
            send_key(sock, "ret")
        elif c in shift_map:
            send_shift_key(sock, shift_map[c])
        elif c.isupper():
            send_shift_key(sock, c.lower())
        elif c in keymap:
            send_key(sock, keymap[c])
        time.sleep(0.05)

def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <dom-id> <text>")
        sys.exit(1)

    domid = sys.argv[1]
    text = sys.argv[2]

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(f"/var/run/xen/qmp-libxl-{domid}")
    sock.recv(4096)
    qmp_send(sock, "qmp_capabilities")

    send_string(sock, text)
    sock.close()

if __name__ == "__main__":
    main()
