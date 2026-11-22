import configparser
import re
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
FRR_CONF = REPO / "configs/rtr/frr.conf"
NETWORKD_DIR = REPO / "configs/rtr/networkd"


def _frr_overlay_addressed_interfaces() -> set[str]:
    """Return overlay VRF interfaces whose IPv6 address is configured in FRR."""
    interfaces: set[str] = set()
    current_iface: str | None = None
    for raw_line in FRR_CONF.read_text().splitlines():
        line = raw_line.strip()
        m = re.match(r"^interface\s+(\S+)\s+vrf\s+overlay$", line)
        if m:
            current_iface = m.group(1)
            continue
        if line == "exit":
            current_iface = None
            continue
        if current_iface and line.startswith("ipv6 address "):
            interfaces.add(current_iface)
    return interfaces


def _networkd_units_by_name() -> dict[str, tuple[Path, configparser.ConfigParser]]:
    units: dict[str, tuple[Path, configparser.ConfigParser]] = {}
    for path in sorted(NETWORKD_DIR.glob("*.network")):
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        cp.read(path)
        if cp.has_option("Match", "Name"):
            units[cp.get("Match", "Name").strip()] = (path, cp)
    return units


class RtrNetworkdContractsTest(unittest.TestCase):
    def test_frr_addressed_overlay_interfaces_keep_static_config(self):
        """FRR-managed IPv6 addresses must survive systemd-networkd restarts.

        Without KeepConfiguration=static (or yes), a systemd-networkd restart
        (e.g. triggered by unattended-upgrade of libc6/systemd) flushes
        FRR-assigned addresses such as lo-overlay's BGP loopback ::d/128.
        """
        frr_ifaces = _frr_overlay_addressed_interfaces()
        self.assertTrue(
            {"lo-overlay", "enX3", "wg0", "wg1", "wg2"}.issubset(frr_ifaces),
            f"Expected core FRR overlay interfaces in frr.conf, got {sorted(frr_ifaces)}",
        )

        units = _networkd_units_by_name()
        for iface in sorted(frr_ifaces):
            with self.subTest(interface=iface):
                self.assertIn(
                    iface,
                    units,
                    f"Missing systemd-networkd unit for FRR interface {iface}",
                )
                path, cp = units[iface]
                keep = cp.get("Network", "KeepConfiguration", fallback="").strip()
                self.assertIn(
                    keep,
                    {"static", "yes"},
                    f"{path.relative_to(REPO)} must set KeepConfiguration=static "
                    f"so systemd-networkd restarts do not flush FRR-managed addresses",
                )
