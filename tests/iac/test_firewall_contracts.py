from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


def test_rtr_customer_isolation_is_destination_prefix_enforced():
    """Customer→infra isolation must not depend only on VRF oifname matching."""
    ruleset = (REPO / "ansible/generated/rtr/nftables.conf").read_text()

    assert (
        "ip6 saddr 2a0c:b641:b51::/48 "
        "ip6 daddr { 2a0c:b641:b50:2::/64, 2a0c:b641:b50::/64, 2a0c:b641:b50:ff00::/56 } "
        "counter drop comment \"customer→infra/router v6 isolation\""
    ) in ruleset
    assert (
        "iifname enX3 ip daddr { 10.0.0.0/24, 10.0.2.0/24 } "
        "counter drop comment \"customer→infra/mgmt v4 isolation\""
    ) in ruleset


def test_rtr_customer_isolation_runs_before_established_accept():
    ruleset = (REPO / "ansible/generated/rtr/nftables.conf").read_text()

    v4_drop = ruleset.index("customer→infra/mgmt v4 isolation")
    v4_established = ruleset.index("ct state established,related accept", v4_drop)
    assert v4_drop < v4_established

    v6_drop = ruleset.index("customer→infra/router v6 isolation")
    output_chain = ruleset.index("chain output", v6_drop)
    assert v6_drop < output_chain
