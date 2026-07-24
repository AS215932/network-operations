# XOA root filesystem condition

`xoa` (`2a0c:b641:b50:2::70`) is the Xen Orchestra management appliance.
It runs Debian 13 on a 20 GB root disk and hosts the XO-from-sources UI that
orchestrates the XCP-NG hypervisor. Low root filesystem pressure on `xoa`
can block package updates, source rebuilds, and XAPI reachability.

## Incident context

Issue #348 opened a proactive `disk /` case for `xoa`. Because operator
monitor text and the GitHub issue body are untrusted evidence, this runbook
starts with direct inspection. Do not assume a specific root cause without
`df` and `du` output.

## Immediate investigation

1. SSH to `xoa` as `root`:
   ```bash
   ssh root@2a0c:b641:b50:2::70
   ```

2. Confirm pressure:
   ```bash
   df -h /
   du -sh /* 2>/dev/null | sort -rh | head -n 10
   ```

3. Drill into the largest directories:
   ```bash
   du -sh /var/* /opt/* /tmp/* /root/* 2>/dev/null | sort -rh | head -n 20
   ```

4. Check systemd journal footprint explicitly:
   ```bash
   journalctl --disk-usage
   ```

5. Check for deleted-but-held (unlinked) files:
   ```bash
   lsof +L1
   ```

6. Check Vector agent disk buffer (`xoa` ships logs to the aggregator):
   ```bash
   du -sh /var/lib/vector
   ```

## Rollback and safety snapshot

Before mutative cleanup, protect operational state:

1. From a second session or via the Xen Orchestra web UI, snapshot `xoa`
   on `dom0`. Self-snapshot is possible because `xoa` manages itself through
   XAPI on the mgmt link-local network.
2. Prefer bounded `vacuum` and `logrotate` commands over `rm -rf`.

Disk cleanup is largely non-reversible; the XO snapshot is the only safe
rollback for deleted data.

## Safe remediation (human-loop approved)

Only execute after `du`/`df` evidence identifies safe deletion targets.

- Clean package cache:
  ```bash
  apt-get clean
  ```

- Vacuum journals to a 7-day floor:
  ```bash
  journalctl --vacuum-time=7d
  ```

- Rotate and compress logs if `logrotate` has missed cycles:
  ```bash
  logrotate -f /etc/logrotate.conf 2>/dev/null || true
  ```

- Purge stale temporary files older than 10 days:
  ```bash
  find /tmp /var/tmp -type f -atime +10 -delete 2>/dev/null || true
  ```

- Remove old kernels only when the current kernel is stable and a prior
  kernel exists:
  ```bash
  dpkg -l 'linux-image-*' | awk '/^ii/{print $2}' | grep -v "$(uname -r)" | \
    xargs -r apt-get remove --purge -y
  ```

- If the largest consumer is the XO source/build tree, verify the currently
  running version and prune only confirmed stale build artifacts or caches.
  **Do not delete the active XO installation directory.**

## Structural options

If safe cleanup does not yield enough headroom:

- Extend the root VDI via Xen Orchestra / `xo-cli` and grow the partition
  inside the VM. See the resize pattern in `docs/deployment.md`.
- If root pressure is chronic, evaluate whether the 20 GB default in the
  `xoa` provisioning table is still adequate and open a follow-up to adjust
  the template or move XO build/workspace data to a dedicated data disk.

## Verification

After remediation:

```bash
ssh root@2a0c:b641:b50:2::70 'df -h /'
```

- The Icinga/Prometheus `disk /` alert must clear.
- The Xen Orchestra web UI must remain reachable through `proxy`.
- `node_exporter` (`:9100`) must still be scraped by `mon`.
- Core XO services remain active:
  ```bash
  ssh root@2a0c:b641:b50:2::70 'systemctl is-active xo-server || true'
  ssh root@2a0c:b641:b50:2::70 'systemctl is-active redis-server || true'
  ```
- No alert suppression on `xoa` should be left in a permanent state.

The NOC control plane must remain unaffected; confirm `/health`,
`/health/cases`, and the CaseService outbox on `noc` are still reporting
normally.

## NOC loop constraints

- This remediation must not be executed directly by the NOC agent
  (`do_not_directly_remediate_disk_from_noc_agent`).
- A human operator must review the `du`/`df` evidence and apply
  `loop:approved` before executing any mutative commands.
- Do not convert a temporary monitoring suppression into a permanent
  silencing rule without a separate change ticket.

## Related NOC handoffs

Low root filesystem CaseService handoffs for `xoa` should use this runbook as
the source-backed engineering context. The handoff objective is usually phrased
as "resolve low root filesystem condition"; the expected outcome is that the
disk alert clears while `/health`, `/health/cases`, and the CaseService outbox
remain healthy.
