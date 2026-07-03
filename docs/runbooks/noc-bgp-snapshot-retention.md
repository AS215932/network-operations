# NOC BGP router snapshot retention

`noc` runs Hyrule MCP and owns local BGP router table snapshots under
`/var/lib/hyrule-mcp/bgp-snapshots`. This data must be bounded before
`bgp-router-snapshot.timer` is enabled.

## Incident context

Issue #321 recorded the production failure mode: hourly BGP router snapshots
grew to about 14 GiB on the `noc` root filesystem. The snapshot metadata had a
7 day `expires_at`, but no host-local retention mechanism enforced it. Once `/`
filled, `apt-get update` failed and unrelated Ansible applies failed with
`No space left on device`.

The immediate mitigation was to stop and disable `bgp-router-snapshot.timer`,
clean apt cache and journals, and leave the host with healthy root filesystem
free space. That mitigation is temporary. Do not re-enable snapshot collection
until retention is managed by code.

## Permanent fix

The production fix belongs in the `noc` Ansible path:

- Manage `bgp-router-snapshot.service` and `bgp-router-snapshot.timer`, or
  explicitly remove unmanaged copies.
- Enforce retention on `/var/lib/hyrule-mcp/bgp-snapshots` before enabling the
  timer. The default retention horizon is 7 days to match snapshot metadata.
- Prefer `systemd-tmpfiles` for age-based deletion unless the service needs a
  dedicated cleanup timer.
- Keep root filesystem monitoring in place so `disk /` alerts before package
  management and applies break.
- Re-enable `bgp-router-snapshot.timer` only after retention is active.

## Verification

After applying the `noc` playbook:

```bash
ansible-playbook ansible/playbooks/noc.yml --tags apply \
  -e '{"noc_apply":true}' --limit noc
ssh noc 'systemctl is-active bgp-router-snapshot.timer'
ssh noc 'systemd-tmpfiles --clean || true'
ssh noc 'du -sh /var/lib/hyrule-mcp/bgp-snapshots; df -h /; apt-get update'
```

The timer should be active, cleanup policy should exist and execute
successfully, `/` should have healthy free space, and `apt-get update` should
succeed.

## Related NOC handoffs

Low root filesystem CaseService handoffs for `noc` should use this runbook as
the source-backed engineering context. The handoff objective is usually phrased
as "resolve low root filesystem condition"; the expected outcome is that the
disk alert clears while `/health`, `/health/cases`, and the CaseService outbox
remain healthy.
