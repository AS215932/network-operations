# Rotate the `noc-agent` Icinga2 ApiUser password

Used by `hyrule-mcp` on `noc` to authenticate against the Icinga2 REST API
on `mon`. Per issue #15, this user replaces the legacy `root` ApiUser with
scoped permissions (`actions/acknowledge-problem`, `objects/query/Host`,
`objects/query/Service`).

## When to rotate

- **Annually**, on the same cadence as Vault secret rotation.
- **Immediately** if the credential leaks (logged accidentally, committed to
  git, etc.).
- **After a maintainer leaves the project**.

## Rotate

```bash
# 1. Generate a new password.
NEW_PW=$(openssl rand -hex 32)

# 2. Push the new password to the ansible apply via the env var.
export ICINGA_NOC_AGENT_API_PASSWORD="$NEW_PW"

# 3. Re-apply the icinga2 role to mon (renders + reloads).
cd ansible
set -a; source ../secrets.local.sh; set +a
ansible-playbook playbooks/icinga2.yml --tags apply \
  -e '{"icinga2_apply":true}' --limit mon

# 4. Update the Vault kv/noc-agent entry. hyrule-mcp reads from there via
#    Vault Agent's render of /opt/noc-agent/.env.
vault kv patch kv/noc-agent \
  icinga_api_user=noc-agent \
  icinga_api_password="$NEW_PW"

# 5. Watch vault-agent on noc render the new value.
ssh noc 'journalctl -fu vault-agent-noc-agent'
# After the next /opt/noc-agent/.env render (driven by Vault TTL — see
# CTMPL config), noc-agent re-reads .env on its next restart. Since PR #40
# (vault-agent JWT no-restart), the env-template re-render DOES bounce
# noc-agent, so the new credential is picked up automatically.

# 6. Smoke: hyrule-mcp should still successfully call icinga_get_host_state.
curl -s -k -u "noc-agent:$NEW_PW" \
  "https://mon.as215932.net:5665/v1/objects/hosts/noc" | jq .results
```

## Failure modes

- **`401 Unauthorized` after rotation**: vault-agent on noc hasn't yet
  re-rendered `/opt/noc-agent/.env`. Wait one render cycle (≤ wait.max in
  vault-agent.hcl, default 10s) or `systemctl reload vault-agent-noc-agent`.
- **`403 Forbidden` on acknowledge-problem**: permissions list mismatch.
  Compare `/etc/icinga2/conf.d/api-users-managed.conf` against the
  Ansible template `roles/icinga2/templates/api-users-managed.conf.j2`.
- **`icinga2 daemon -C` fails**: a stray syntax error somewhere; the
  managed file uses `--strict-permissions` ownership (`nagios:nagios 0640`),
  so check perms first with `ls -la /etc/icinga2/conf.d/api-users-managed.conf`.

## Tighten further (future work)

If we ever need more than read-and-ack, this is the file where new
permissions get added. Don't bring back `permissions = ["*"]`. Each new
permission warrants a follow-up issue documenting why.
