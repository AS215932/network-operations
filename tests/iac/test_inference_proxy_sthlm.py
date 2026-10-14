"""inf-sthlm inference proxy contracts: SSE-safe proxying, Cloudflare-only 443, IPv6 overlay."""
import ipaddress
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CFG = REPO / "configs/inf-sthlm"
GPU_NET = ipaddress.ip_network("2a0c:b641:b50:10::/64")


def cf_ranges():
    lines = (CFG / "cloudflare-ips-v6").read_text().splitlines()
    return [l.strip() for l in lines if l.strip() and not l.startswith("#")]


class InferenceProxyTest(unittest.TestCase):
    def test_cloudflare_list_is_ipv6(self):
        nets = cf_ranges()
        self.assertTrue(nets)
        for n in nets:
            self.assertEqual(ipaddress.ip_network(n).version, 6, n)

    def test_nginx_streaming_and_aop(self):
        conf = (CFG / "nginx.conf").read_text()
        for needle in ("location = /v1/chat/completions", "proxy_buffering off;",
                       "proxy_read_timeout 600s;", "ssl_verify_client on;", "listen [::]:443"):
            self.assertIn(needle, conf)
        self.assertNotRegex(conf, r"listen\s+(\d+\.|443)")
        self.assertEqual(sorted(re.findall(r"set_real_ip_from (\S+);", conf)), sorted(cf_ranges()))

    def test_pf_443_only_from_cloudflare(self):
        pf = (CFG / "pf.conf").read_text()
        self.assertIn('table <cloudflare6> persist file "/etc/pf.cloudflare6"', pf)
        rules = [l for l in pf.splitlines() if "port 443" in l and l.startswith("pass")]
        self.assertEqual(rules, ["pass in on $ext_if inet6 proto tcp from <cloudflare6> to port 443"])

    def test_overlay_addresses_in_gpu_net(self):
        for name in ("wg0.conf", "nginx.conf", "wg-gpu-node.conf.template"):
            text = (CFG / name).read_text().replace("NODE_ID", "11")
            for addr in re.findall(r"2a0c:b641:b50:10::[0-9a-f]+", text):
                self.assertIn(ipaddress.ip_address(addr), GPU_NET, name)

    def test_no_private_keys_committed(self):
        for f in CFG.iterdir():
            for line in f.read_text().splitlines():
                m = re.match(r"\s*PrivateKey\s*=\s*(\S+)", line)
                if m:
                    self.assertTrue(m.group(1).startswith("<"), f.name)


if __name__ == "__main__":
    unittest.main()
