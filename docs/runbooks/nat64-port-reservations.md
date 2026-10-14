# NAT64 port reservations and recovery

Jool and inbound DNAT share the router public IPv4 address. A translated source port must not also be reserved for an inbound service. Maintain disjoint TCP and UDP pools in `configs/rtr/jool/jool.conf`; ICMP identifiers are a separate namespace. Keep both transport pools below the host ephemeral range declared in `configs/rtr/sysctl.conf`.

The regression test in `tests/iac/test_rtr_nat64_contracts.py` reads reservations from the deployed firewall template. Run it whenever a public DNAT service or a NAT64 pool changes. The firewall render stages `ansible/generated/rtr/jool.conf` for review; the apply-gated task installs the same source at `/etc/jool/jool.conf` with a backup. `/etc/jool/managed-config.sha256` records the desired configuration only after the reload, Jool restart and VRF restoration handlers succeed and rollback-watchdog cancellation completes. A missing or stale stamp requests reconciliation even when a retry finds the configuration file unchanged. The stamp is not a substitute for live health checks.

## Normal deployment

1. Require current-head CI and resolved review feedback before merging the configuration PR. Review the generated firewall and Jool artifacts together.
2. Use the normal firewall validation and apply workflow from main, scoped to the router. Plan for active NAT64 connections to reconnect: the existing handler chain reloads nftables, restarts Jool, then restores the VRF route service.
3. Verify Jool, the VRF route service, FRR and Vector are active. Compare the live TCP and UDP pools with the approved configuration and confirm return routes to both infrastructure and customer segments.
4. Verify ordinary HTTPS requests from both CI runners to the GitHub API, broker and pipeline endpoints. A root-path HTTP 404 proves an HTTPS exchange, but does not prove runner recovery: also require a listening/job lifecycle marker and progress on an existing queued job.
5. Check the kernel journal for new Jool allocation warnings and check router disk headroom. Verify the independent NOC delivery monitor separately.

## Recovery and rollback

A live partial pool removal on Jool 4.1.13 was followed by `mask_domain_find` kernel warnings and failed new translations. Do not treat a successful pool display as proof of working allocations, and do not repeat live pool edits or flush bindings as an unreviewed workaround.

If this condition occurs, prepare the intended persistent configuration and a backup before requesting any required approval for a disruptive restart. Rebuilding Jool from the reviewed configuration interrupts existing translated connections. Restore the VRF route service even if the Jool restart fails, then inspect the resulting state before taking another action. A pending approval must not be bypassed through a workflow or alternative command.

Preserve the previous configuration backup and record its exact filename in the local incident checkpoint. The nftables rollback watchdog restores only nftables; it does not roll back Jool configuration. If a configuration rollback is needed, review the previous pool against current DNAT reservations first. Reintroducing overlapping ports is not a safe rollback. Restore a compatible configuration and use the same ordered restart and validation steps within the approved maintenance scope.
