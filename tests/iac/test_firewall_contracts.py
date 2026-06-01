from pathlib import Path
import unicodedata

import yaml
from jinja2 import Environment, StrictUndefined


REPO = Path(__file__).resolve().parents[2]


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _ruleset_has_line_with_parts(ruleset: str, parts: list[str]) -> bool:
    normalized_parts = [_normalize(part) for part in parts]
    for line in ruleset.splitlines():
        normalized_line = _normalize(line)
        if all(part in normalized_line for part in normalized_parts):
            return True
    return False


def _all_group_vars() -> dict:
    return yaml.safe_load((REPO / "ansible/inventory/group_vars/all.yml").read_text())


def _render_inventory_value(value: str, context: dict) -> str:
    return Environment(undefined=StrictUndefined).from_string(value).render(context)


def _resolved_isolation_prefixes(family: str) -> list[str]:
    all_vars = _all_group_vars()
    return [
        _render_inventory_value(prefix, all_vars)
        for prefix in all_vars[f"customer_isolation_block_{family}"]
    ]


def test_rtr_customer_isolation_is_destination_prefix_enforced():
    """Customer→infra isolation must not depend only on VRF oifname matching."""
    ruleset = (REPO / "ansible/generated/rtr/nftables.conf").read_text()
    v6_prefixes = _resolved_isolation_prefixes("v6")
    v4_prefixes = _resolved_isolation_prefixes("v4")

    assert _ruleset_has_line_with_parts(
        ruleset,
        [
            "ip6 saddr 2a0c:b641:b51::/48",
            "ip6 daddr",
            *v6_prefixes,
            "counter drop",
            'comment "customer→infra/router v6 isolation"',
        ],
    )
    assert _ruleset_has_line_with_parts(
        ruleset,
        [
            "iifname enX3",
            "ip daddr",
            *v4_prefixes,
            "counter drop",
            'comment "customer→infra/mgmt v4 isolation"',
        ],
    )


def test_rtr_customer_isolation_runs_before_established_accept():
    ruleset = (REPO / "ansible/generated/rtr/nftables.conf").read_text()

    v4_drop = ruleset.index("customer→infra/mgmt v4 isolation")
    v4_established = ruleset.index("ct state established,related accept", v4_drop)
    assert v4_drop < v4_established

    v6_drop = ruleset.index("customer→infra/router v6 isolation")
    output_chain = ruleset.index("chain output", v6_drop)
    assert v6_drop < output_chain
