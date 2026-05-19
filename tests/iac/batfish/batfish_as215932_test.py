import os

import pytest

pybatfish = pytest.importorskip("pybatfish")
from pybatfish.client.session import Session
from pybatfish.datamodel import HeaderConstraints


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
SNAPSHOT = os.path.join(REPO, "tests/iac/batfish/snapshot")
CORE_NODES = {"rtr", "cr1-nl1", "cr1-de1"}
CORE_PAIRS = {
    ("rtr", "cr1-nl1"),
    ("rtr", "cr1-de1"),
    ("cr1-nl1", "cr1-de1"),
}
LOCAL_AS = 215932
CANONICAL_PREFIX = "2a0c:b641:b50::/44"


@pytest.fixture(scope="session")
def bf():
    host = os.environ.get("BATFISH_HOST", "127.0.0.1")
    session = Session(host=host)
    session.set_network("as215932")
    session.init_snapshot(SNAPSHOT, name="current", overwrite=True)
    return session


def test_bgp_session_compatibility(bf):
    df = bf.q.bgpSessionCompatibility().answer().frame()
    bad = df[df["Configured_Status"] != "UNIQUE_MATCH"]
    assert bad.empty, bad


def test_ibgp_full_mesh(bf):
    df = bf.q.bgpEdges().answer().frame()
    ibgp = df[(df["AS_Number"] == LOCAL_AS) & (df["Remote_AS_Number"] == LOCAL_AS)]
    directed = {(row["Node"], row["Remote_Node"]) for _, row in ibgp.iterrows()}
    expected = set()
    for a, b in CORE_PAIRS:
        expected.add((a, b))
        expected.add((b, a))
    assert expected <= directed


def test_bgp_router_ids_are_unique(bf):
    df = bf.q.bgpProcessConfiguration().answer().frame()
    core = df[df["Node"].isin(CORE_NODES)]
    router_ids = list(core["Router_ID"])
    assert len(router_ids) == len(set(router_ids))


def test_no_undefined_references(bf):
    df = bf.q.undefinedReferences().answer().frame()
    assert df.empty, df


def test_customer_networks_cannot_reach_infra_management(bf):
    result = bf.q.reachability(
        pathConstraints={"startLocation": "@enter(rtr[enX3])"},
        headers=HeaderConstraints(dstIps="2a0c:b641:b50:2::/64"),
    ).answer().frame()
    assert result.empty, result


def test_authorized_ci_can_reach_management_ports(bf):
    result = bf.q.reachability(
        pathConstraints={"startLocation": "@enter(rtr[enX2])"},
        headers=HeaderConstraints(
            srcIps="2a0c:b641:b50:2::d0",
            dstIps="2a0c:b641:b50:2::/64",
            applications=["ssh", "https"],
        ),
    ).answer().frame()
    assert not result.empty
