# Provisioning the `ci-pr` VM (unprivileged PR runner)

`ci-pr` is the **unprivileged** self-hosted GitHub Actions runner that handles
untrusted PR code — PR-Agent, Semgrep, and (after Wave 4) every `pull_request`
lint/test/build job. It is deliberately **disposable** and **isolated**:

- On the **customer segment** `2a0c:b641:b51::c1/64` (rtr `enX3`/`xenbr-vm`), so
  rtr drops customer-sourced forwarded packets to the infra/router prefixes and
  drops `enX3` forwarding to the infra/mgmt bridges. The primary rule matches
  destination prefixes, not only `oifname`, so it remains effective under the
  overlay VRF. A compromised PR job here cannot reach production.
- **No** Vault AppRole, **no** `/etc/github-runner/secrets.env`, **no** `id_ci`
  deploy key, **no** Containerlab/xcaddy — see `host_vars/ci-pr.yml`.
- Registered in the **`public-pr`** org runner group (label `hyrule-public-pr`),
  separate from the privileged `ci` runner's `hyrule-ci` group.

Contrast with the privileged `ci` runner: see `docs/ci/provision.md`.

## 0. Prereqs

- `~/.ssh/id_servify` (ops workstation key — also authorizes `ci-pr` SSH).
- `gh` authenticated to the AS215932 org.
- Decide sizing: **1 vCPU, 2 GiB RAM, 20 GiB root** (Debian 13). No data disk.

## 1. Create the VM on the customer (vm) bridge

The VM lands on `xenbr-vm`, not infra. Resolve that network's UUID on XOA, then
run the (now parameterized) `scripts/create-vms.sh` helper:

```bash
# On XOA (10.0.0.10, via the dom0 jump) or wherever xo-cli is configured:
xo-cli --list-objects type=network \
  | python3 -c 'import sys,json; [print(n["uuid"], n["name_label"]) for n in json.load(sys.stdin)]'
# Pick the xenbr-vm / "vm" network UUID:
export VM_NET=<that-uuid>

# create-vms.sh skips ci-pr unless VM_NET is set. It uses args 9,10 =
# NETWORK + GATEWAY to place ci-pr on the vm bridge with the rtr enX3 gateway:
#   create_vm ci-pr "..." 1 2147483648 21474836480 "2a0c:b641:b51::c1" "" "" "$VM_NET" "2a0c:b641:b51::1"
bash scripts/create-vms.sh   # or copy just the ci-pr block
```

cloud-init assigns `2a0c:b641:b51::c1/64`, gateway + DNS `2a0c:b641:b51::1`
(rtr Unbound/DNS64). Start the VM, wait for cloud-init, and verify
`ssh svag@2a0c:b641:b51::c1` works over global IPv6.

## 2. Bootstrap firewall + monitoring (from the ops workstation)

`ci-pr` is applied from the **workstation**, NOT the privileged `ci` runner: it
has no `id_ci`, so the `ci` deploy user cannot (and must not) reach it. Vault is
disabled for `ci-pr`, so no `secrets.local.sh` env is required for these.

```bash
cd ansible

# Firewall: default-deny + node_exporter scrape from mon (host_vars/ci-pr.yml).
ansible-playbook playbooks/firewall.yml --tags apply \
  -e '{"firewall_apply":true}' --limit ci-pr

# Monitoring: node_exporter + Icinga2 Host registration on mon.
ansible-playbook playbooks/monitoring.yml --tags apply \
  -e '{"monitoring_apply":true}' --limit ci-pr
```

Logs are intentionally **off** (`logs_register: false`): pushing to the log VM
(`ci-pr → log:6000`) is customer→infra and is dropped by the isolation. Keep
logs in local journald; do not punch a hole.

### 2a. Add `ci-pr` to Prometheus on mon (the one manual step)

The `monitoring` role does not manage `prometheus.yml`. On `mon`, add `ci-pr`
(`[2a0c:b641:b51::c1]:9100`) to the appropriate `static_configs` job and
`systemctl reload prometheus`. Confirm `mon → ci-pr:9100` scrapes succeed
(infra→customer is permitted by rtr's forward policy).

## 3. Register the runner in the `public-pr` group

```bash
# Org runner registration token (1 h TTL):
export GH_RUNNER_TOKEN=$(gh api -X POST \
  /orgs/AS215932/actions/runners/registration-token --jq .token)

cd ansible
ansible-playbook playbooks/ci_pr.yml --tags apply \
  -e github_runner_apply=true \
  -e github_runner_registration_token="$GH_RUNNER_TOKEN" \
  --limit ci-pr
```

The runner joins `public-pr` (via `github_runner_group_name` in
`host_vars/ci-pr.yml`) with labels `[self-hosted, linux, x64, hyrule-public-pr]`.
Confirm it shows online:
`gh api orgs/AS215932/actions/runners --jq '.runners[].name'`.

## 4. Verify isolation (acceptance probe)

From a throwaway workflow job on `hyrule-public-pr` (or `ssh svag@2a0c:b641:b51::c1`):

```bash
test ! -e /etc/github-runner/secrets.env && echo "OK: no secrets.env"
test ! -e /var/lib/github-runner/.ssh/id_ci && echo "OK: no id_ci"
pgrep -fa vault-agent || echo "OK: no vault agent"
# Must FAIL/timeout (no infra/mgmt reachability):
timeout 5 bash -c 'cat < /dev/null > /dev/tcp/2a0c:b641:b50:2::50/9100' \
  && echo "FAIL: reached mon (infra)" || echo "OK: cannot reach infra mgmt"
# Must SUCCEED (egress):
timeout 5 bash -c 'cat < /dev/null > /dev/tcp/openrouter.ai/443' \
  && echo "OK: egress to openrouter" || echo "FAIL: no egress"
```

Then open a draft PR to exercise PR-Agent (`deepseek-v4-flash`) and Semgrep
(SARIF → Code Scanning). See `docs/ci/pr-agent.md` / `docs/ci/semgrep.md`.
