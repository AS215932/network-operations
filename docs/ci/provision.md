# Provisioning the `ci` VM

The `ci` VM hosts the self-hosted GitHub Actions runner that powers every
workflow in `.github/workflows/`. It must exist before the workflows can
do any work — until then the workflow jobs sit queued.

Reasonable size: **1 vCPU, 2 GB RAM, 20 GB root disk + 50 GB runner data disk** (Debian 13). The runner is
mostly I/O-bound during `ansible-playbook --tags validate` jobs.

## 1. Provision the VM via Xen Orchestra

The repo's established pattern is `xo-cli` run **on XOA** (`10.0.0.10`, reached
via overlay v6 `2a0c:b641:b50:2::70` or the dom0 jump `193.70.32.138`) — see
`scripts/create-vms.sh` for the `vm.create` + `vdi.set` + `vm.setBootOrder` +
`vm.start` sequence. The `ci` VM follows it with these parameters:

- Template: **Debian 13 cloud-init**. Name: `ci`. 1 vCPU, 2 GiB RAM,
  20 GiB root disk on `local_storage` plus a second 50 GiB data disk attached
  at VBD position `8` so the guest sees it as `/dev/xvdi`.
- VIF on `xenbr-infra` (overlay). Static IPv6 `2a0c:b641:b50:2::d0`
  (matches `peers.ci.ipv6` in `group_vars/all.yml` and
  `ci.as215932.net`).
- Cloud-init user-data from `autoinstall/debian-cloud-init.yaml.j2`;
  hostname `ci.as215932.net`; `id_servify.pub` authorized for root.

The XOA web UI (New → VM → Debian 13 cloud-init) is an equivalent manual
path if you prefer.

Start the VM, wait for cloud-init to finish, and verify `ssh svag@ci` works
over overlay v6.

If you are retrofitting the existing `ci` host rather than building a fresh VM,
attach the new 50 GiB VDI in Xen Orchestra first and place it at VBD position
`8` so the guest sees `/dev/xvdi` before you re-apply the role.

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
The role manages the dedicated `/dev/xvdi` data disk, mounts it at
`/var/lib/github-runner`, migrates existing runner and Docker data onto it on
the first apply after the disk is attached, and keeps transient content bounded:
`_diag` is pruned after 7 days, runner bootstrap/build scratch is pruned after a
few days, and a systemd timer runs `docker system prune` with a 7-day cutoff.

The `ci` runner is intentionally privileged enough to run the trusted
Containerlab FRR gate (`containerlab-frr`). The `github_runner` role grants
`runner` passwordless `sudo /usr/bin/containerlab` and relaxes the
`github-runner.service` sandbox when `github_runner_containerlab_enabled=true`
(`NoNewPrivileges=false`, `ProtectKernelModules=false`,
`ProtectControlGroups=false`). This is not suitable for untrusted pull request
jobs; the separate `ci-pr` runner keeps containerlab disabled and retains the
stricter sandbox.

## 5. Verify

```bash
gh api /repos/AS215932/network-operations/actions/runners \
  | jq '.runners[] | {name, status, labels: [.labels[].name]}'
```

Expect an entry with `name=ci-runner`, `status=online`, and labels including
`self-hosted, linux, x64, hyrule, hyrule-infra`. Existing registrations do not
always pick up label changes; if `hyrule-infra` or `hyrule` is missing, delete
the stale GitHub runner registration and re-apply the role with a fresh token.

`mcp__hyrule__icinga_get_host_state host=ci` should show the host UP with
the `github-runner-online` service OK.

Also verify the storage cutover directly on the host:

```bash
findmnt /var/lib/github-runner
df -h /var/lib/github-runner
docker info --format '{{ .DockerRootDir }}'
systemctl status github-runner-docker-prune.timer
```

Expect `/var/lib/github-runner` to be backed by `/dev/xvdi1` and Docker to use
`/var/lib/github-runner/docker`.

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
