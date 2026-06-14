#!/usr/bin/env python3
"""NETCONF/YANG smoke checks executed inside the lab router container."""

from __future__ import annotations

import json
import sys
from typing import Any

from ncclient import manager


NETCONF_HOST = "127.0.0.1"
NETCONF_PORT = 830
NETCONF_USER = "netconf"
NETCONF_PASSWORD = "netconf"
MARKER = "as215932-netconf-lab-probe"

INTERFACE_FILTER = """
<lib xmlns="http://frrouting.org/yang/interface">
  <interface>
    <name>lo</name>
  </interface>
</lib>
"""

INTERFACE_DESCRIPTION_MERGE = f"""
<config xmlns:nc="urn:ietf:params:xml:ns:netconf:base:1.0">
  <lib xmlns="http://frrouting.org/yang/interface">
    <interface>
      <name>lo</name>
      <description>{MARKER}</description>
    </interface>
  </lib>
</config>
"""

INTERFACE_DESCRIPTION_REMOVE = """
<config xmlns:nc="urn:ietf:params:xml:ns:netconf:base:1.0">
  <lib xmlns="http://frrouting.org/yang/interface">
    <interface>
      <name>lo</name>
      <description nc:operation="remove"/>
    </interface>
  </lib>
</config>
"""

NETCONF_STATE_FILTER = """
<netconf-state xmlns="urn:ietf:params:xml:ns:yang:ietf-netconf-monitoring">
  <capabilities/>
  <schemas/>
  <sessions/>
</netconf-state>
"""


def require_capability(caps: list[str], needle: str) -> None:
    if not any(needle in cap for cap in caps):
        raise AssertionError(f"missing NETCONF capability containing {needle!r}")


def running_interface_xml(conn: manager.Manager) -> str:
    return str(conn.get_config(source="running", filter=("subtree", INTERFACE_FILTER)).data_xml)


def main() -> int:
    summary: dict[str, Any] = {}
    with manager.connect(
        host=NETCONF_HOST,
        port=NETCONF_PORT,
        username=NETCONF_USER,
        password=NETCONF_PASSWORD,
        hostkey_verify=False,
        allow_agent=False,
        look_for_keys=False,
        timeout=15,
    ) as conn:
        caps = sorted(str(cap) for cap in conn.server_capabilities)
        summary["capability_count"] = len(caps)
        require_capability(caps, ":candidate")
        require_capability(caps, ":validate")
        require_capability(caps, "ietf-netconf-monitoring")

        state = conn.get(filter=("subtree", NETCONF_STATE_FILTER))
        summary["netconf_state_bytes"] = len(str(state.data_xml))

        schemas = {}
        for module in ("frr-interface", "frr-bgp"):
            schema = conn.get_schema(module)
            text = str(schema.data)
            if module not in text:
                raise AssertionError(f"get-schema({module}) did not return the expected module text")
            schemas[module] = len(text)
        summary["schemas"] = schemas

        before = running_interface_xml(conn)
        if MARKER in before:
            raise AssertionError("lab marker unexpectedly present before candidate test")

        conn.lock(target="candidate")
        try:
            conn.discard_changes()
            conn.edit_config(target="candidate", config=INTERFACE_DESCRIPTION_MERGE)
            conn.validate(source="candidate")
            conn.discard_changes()
        finally:
            conn.unlock(target="candidate")

        after_discard = running_interface_xml(conn)
        if MARKER in after_discard:
            raise AssertionError("candidate discard leaked into running datastore")

        conn.lock(target="candidate")
        try:
            conn.edit_config(target="candidate", config=INTERFACE_DESCRIPTION_MERGE)
            conn.validate(source="candidate")
            conn.commit()
        finally:
            conn.unlock(target="candidate")

        after_commit = running_interface_xml(conn)
        if MARKER not in after_commit:
            raise AssertionError("candidate commit did not update running datastore")

        conn.lock(target="candidate")
        try:
            conn.edit_config(target="candidate", config=INTERFACE_DESCRIPTION_REMOVE)
            conn.validate(source="candidate")
            conn.commit()
        finally:
            conn.unlock(target="candidate")

        after_cleanup = running_interface_xml(conn)
        if MARKER in after_cleanup:
            raise AssertionError("cleanup commit did not remove lab marker")

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - lab diagnostic path.
        print(f"NETCONF/YANG smoke failed: {exc}", file=sys.stderr)
        raise
