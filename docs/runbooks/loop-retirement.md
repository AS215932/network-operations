# Loop VM retirement

The entire `loop` VM is deprecated as of 2026-09-07. Engineering Loop,
Knowledge Loop, the local Knowledge MCP/API, Agent-Core collector, and Agentic
Observatory are no longer active infrastructure. Their repository history,
databases, credentials and VM disks are retained for recovery or archival.

The old `engineering-loop.yml` entrypoint ends without deploying. Automatic
promotion no longer selects legacy roles; changes to retirement inventory select
`retire-loop.yml`. NOC handoff delivery and collector emissions are disabled in
both environment backends. Local NOC case verification and knowledge context
remain independent of the retired collector.

Apply the reviewed change through the normal main-only `apply.yml` controls:

1. Deploy the NOC environment changes and confirm no new Engineering Loop handoffs.
2. Apply `retire-loop` with limit `loop`. It replaces the managed Icinga host file
   with a retirement marker, validates/reloads Icinga, then stops and disables the
   known legacy timers, services and Vault Agents. It verifies their final states.
3. Apply Prometheus on `mon` to remove the loop scrape target. Verify there are no
   remaining loop-host alerts or periodic idle notifications in `#noc`.
4. Check remaining inventory/proxy/DNS references and archive any required data
   before powering off the VM. Power-off is separate from this playbook; do not
   destroy the VM or its storage.

The retained inventory address is reserved. Do not reassign it while the VM and
its disks exist. A rollback requires a reviewed change restoring the required
services, monitoring and consumers together; enabling a timer alone is insufficient.

Existing failed alerts for active hosts, including Vault, mail, routers and the
logging aggregator, are independent incidents and must remain visible.

NOC's retirement controls also live in the non-secret
`/etc/noc-agent/runtime.env`, loaded by both systemd services after the
Vault-rendered `/opt/noc-agent/.env`. This lets a reviewed deployment disable
retired integrations even while Vault is sealed and the credential file is stale.
The override contains no credentials. Keep it installed with the service units;
after deployment, verify effective process flags, not only the Vault template.
Rollback uses the previous units and inventory through the normal apply workflow.

The NOC role pins the Redis repository key checksum. A matching installed key
is verified locally without another download; a missing or changed key must be
downloaded and match the reviewed checksum. The 2026-09-07 key from
https://packages.redis.io/gpg has primary fingerprint
`54318FA4052D1E61A6B6F7BB5F4349D6BF53AA0C`. Key rotation must review the official
key and update the checksum together; never disable verification to recover an
app deployment. The CDN reachability incident remains separate routing work.
