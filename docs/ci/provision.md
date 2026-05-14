# Provisioning the `ci` VM

The `ci` VM hosts the self-hosted GitHub Actions runner that powers every
workflow in `.github/workflows/`. It must exist before the workflows can
do any work — until then the workflow jobs sit queued.

Reasonable size: **1 vCPU, 2 GB RAM, 20 GB disk** (Debian 13). The runner is
mostly I/O-bound during `ansible-playbook --tags validate` jobs.

## 1. Provision the VM via Xen Orchestra

The repo's established pattern is `xo-cli` run **on XOA** (`10.0.0.10`, reached
via overlay v6 `2a0c:b641:b50:2::70` or the dom0 jump `193.70.32.138`) — see
`scripts/create-vms.sh` for the `vm.create` + `vdi.set` + `vm.setBootOrder` +
`vm.start` sequence. The `ci` VM follows it with these parameters:

- Template: **Debian 13 cloud-init**. Name: `ci`. 1 vCPU, 2 GiB RAM,
  20 GiB disk on `local_storage`.
- VIF on `xenbr-infra` (overlay). Static IPv6 `2a0c:b641:b50:2::d0`
  (matches `peers.ci.ipv6` in `group_vars/all.yml`).
- Cloud-init user-data from `autoinstall/debian-cloud-init.yaml.j2`;
  hostname `ci.as215932.net`; `id_servify.pub` authorized for root.

The XOA web UI (New → VM → Debian 13 cloud-init) is an equivalent manual
path if you prefer.

Start the VM, wait for cloud-init to finish, and verify `ssh svag@ci` works
over overlay v6.

## 2. Bootstrap firewall + monitoring + logs

The same way every other infra VM comes up:

```bash
cd ansible
set -a && source ../secrets.local.sh && set +a

# Firewall first (default-deny + extra rules from host_vars/ci.yml).
ansible-playbook playbooks/firewall.yml --tags apply \
  -e '{"firewall_apply":true}' --limit ci

# Monitoring (node_exporter + Icinga2 host registration).
ansible-playbook playbooks/monitoring.yml --tags apply \
  -e '{"monitoring_apply":true}' --limit ci

# Logs (Vector agent → log VM).
ansible-playbook playbooks/logs.yml --tags apply \
  -e '{"logs_apply":true}' --limit ci
```

## 3. Mint a runner registration token

GitHub runner tokens are short-lived (1 h). Mint just before applying the
runner role:

```bash
export GH_RUNNER_TOKEN=$(gh api -X POST \
  /repos/AS215932/network-operations/actions/runners/registration-token \
  --jq .token)
```

Use the `ops-workstation` GitHub PAT with `repo` scope to mint — or run
`gh auth status` first to confirm the right account is active.

## 4. Apply the github_runner role

> **Prerequisite (once feat/0f has landed):** the `github_runner` role
> includes `vault_agent` to render `/etc/github-runner/secrets.env` for
> `apply.yml` runs. Bootstrap the runner's Vault AppRole first — see
> `docs/runbooks/bootstrap-runner-vault.md` — and export
> `VAULT_CI_RUNNER_ROLE_ID` / `VAULT_CI_RUNNER_SECRET_ID` before applying.

```bash
ansible-playbook playbooks/ci.yml --tags apply \
  -e github_runner_apply=true \
  -e github_runner_registration_token="$GH_RUNNER_TOKEN" \
  --limit ci
```

After this, `.runner` and `.credentials` persist under
`/var/lib/github-runner/runner/`. The runner survives reboots; the
registration token is not needed again unless you tear down and re-register.

## 5. Verify

```bash
gh api /repos/AS215932/network-operations/actions/runners \
  | jq '.runners[] | {name, status, labels: [.labels[].name]}'
```

Expect an entry with `name=ci-runner`, `status=online`, and labels including
`self-hosted, linux, x64, hyrule-infra`.

`mcp__hyrule__icinga_get_host_state host=ci` should show the host UP with
the `github-runner-online` service OK.

## Tear down / re-register

If the runner goes stale (token reset, host rebuild):

```bash
# Drop the old registration on GitHub
gh api -X DELETE /repos/AS215932/network-operations/actions/runners/<ID>

# Mint a new token, re-apply
export GH_RUNNER_TOKEN=$(gh api -X POST \
  /repos/AS215932/network-operations/actions/runners/registration-token \
  --jq .token)
ansible-playbook playbooks/ci.yml --tags apply \
  -e github_runner_apply=true \
  -e github_runner_registration_token="$GH_RUNNER_TOKEN" \
  --limit ci
```

`--replace` is already set in the role's `config.sh` invocation so a stale
registration is overwritten cleanly.
