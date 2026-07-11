# Deploy runbook (CI lane)

How to ship a change from "PR merged" to "live on production." Production is
the next environment after `main` — there is no staging VM, by design (the
approved plan trades fidelity for a smaller blast surface). For app-backed
services, production deploys are promoted through `network-operations` by
pinning exact app commit SHAs. Safety lives in three layers:

1. **App CI** before promotion — the exact app commit has passed its required
   lint, type, and test checks.
2. **Render-check** before merge — the diff between `ansible/generated/` and
   what render produces is empty.
3. **Post-deploy Goss validation** after apply — the role's Goss suite runs
   against the target hosts, and live Icinga / the hyrule MCP surface any
   regression the change caused.

## App promotion model

Application repositories do not own normal production applies. They produce
reviewed commits with green CI; `network-operations` owns production by
pinning those commits in inventory:

- `ansible/inventory/host_vars/noc.yml`: `noc_agent_version`,
  `hyrule_mcp_version`
- `ansible/inventory/host_vars/loop.yml`: `engineering_loop_version`,
  `knowledge_mcp_version`, `knowledge_loop_version`, `knowledge_api_version`,
  `agent_core_collector_version`, `agent_core_coordinator_version`, and
  `agentic_observatory_version`
- `ansible/inventory/host_vars/soc.yml`: `soc_agent_version` and the immutable
  `soc_network_operations_version` desired-state baseline
- `ansible/inventory/host_vars/api.yml`: `hyrule_cloud_version`
- `ansible/inventory/host_vars/web.yml`: `hyrule_web_version`
- `ansible/inventory/host_vars/netproxy.yml`:
  `hyrule_network_proxy_version`

The coordinator and dedicated SOC VM first land as dark scaffolds on `main`:
their app versions may remain the moving ref `main` only while their apply and
service gates are false. Their first promotion replaces those refs with exact
merged SHAs. `app-promotion-deploy` refuses to schedule the SOC play until both
SOC pins are immutable; the coordinator role likewise refuses a live apply on
a moving ref.

Use the promotion PR template for coordinated deploys. Merge app PRs first,
then let the app repo request or manually update a promotion PR with the exact
merged app SHAs. Production deploys only happen from `network-operations/main`
after the promotion PR merges and the GitHub `production` environment gate is
approved.

The normal automated path is:

1. Merge app PRs after app CI is green.
2. The app repo's **request-promotion** workflow runs after its `ci` workflow
   succeeds on `main`. It uses the AS215932 Promotion Bot GitHub App to send
   `repository_dispatch` to this repository.
3. **promote-apps** opens or updates the promotion PR with app pins, compare
   links, and rollback SHAs.
4. Review the generated promotion PR.
5. Merge the promotion PR after checks pass.
6. **app-promotion-deploy** starts automatically on the `main` push when an app
   pin file changed. It calls `apply.yml` for the affected playbook(s).
7. Approve the GitHub `production` environment gate. This is the intended
   manual deploy step.
8. Review the workflow summary: app pins, compare links, and the post-deploy
   Goss result.

Manual fallback: run **Actions -> promote-apps** in this repository and paste
the merged app SHAs into the relevant inputs. A Knowledge promotion updates its
MCP, loop, and API entry points together; an Agent Core promotion updates its
collector and coordinator entry points together; a SOC promotion also pins the
current reviewed `network-operations/main` commit as the posture desired-state
baseline. Use this when a dispatch failed, when a coordinated promotion should
pin multiple app repos at once, or when an operator intentionally wants to
replay a promotion request.

The workflow rebuilds `promotion/app-sha-pins` from `origin/main` on every run
and carries forward only pins the app repo confirms are still ahead of main,
so manually merged deploy PRs can no longer wedge the branch into merge
conflict (PR #316). Running promote-apps with **all SHA inputs empty** is a
supported self-heal: it rebuilds the branch without promoting anything new.
If no pending pins remain, the branch resets to match main and no PR is
opened; pins still ahead of main are carried forward and the promotion PR is
(re)opened to cover them.

`apply.yml` itself is not a push-triggered workflow. It runs when either:

- an operator manually starts it with `workflow_dispatch`, or
- another workflow calls it through `workflow_call` (for app promotions, this is
  `app-promotion-deploy` after a SHA-pin PR merges).

## When to ship

For pure infrastructure changes, ship after the `network-operations` PR merges
to `main`. For app-backed services, ship only after the app PRs are merged and
the `network-operations` promotion PR pins their exact SHAs. Deploys happen one
at a time. If two operators merge back-to-back, ship sequentially (each apply's
post-snapshot is the next apply's pre-snapshot baseline).

Do not ship during the documented merge-freeze windows (see `MEMORY.md` for
any active freezes).

## How to dispatch a deploy

```bash
gh workflow run apply.yml \
  -F playbook=noc \
  -F limit=noc \
  -F dry_run=false \
  -F pr_number=42         # optional — auto-comments the diff onto the PR
```

Or via the GitHub UI: **Actions → apply → Run workflow → pick playbook /
limit / dry-run / PR**.

The workflow pauses immediately at the **`production` environment review**
gate. Approve in the UI; the run unfreezes.

## What the workflow does

1. **Source secrets** — Vault Agent on the `ci` runner has rendered
   `/etc/github-runner/secrets.env` with `DISCORD_WEBHOOK_URL`,
   `ICINGA_API_USER`, etc. The workflow exports them into `GITHUB_ENV`.
2. **Render-only OR apply** — depending on `dry_run`. Apply uses
   `--tags apply -e <pb>_apply=true --limit <limit>`.
3. **Post-deploy Goss validation** — `scripts/ci/goss-validate.sh` runs the
   role's Goss suite against the target hosts. Regressions from the change
   surface here and in live Icinga / the hyrule MCP, not a pre/post snapshot
   bracket.

## Checking for regressions

Live monitoring is the source of truth for health: check the `mon` Icinga
problem list (or the hyrule MCP `icinga_list_problems` tool) before and after
the run. Because Icinga re-checks on its own interval (minutes), watch it for a
few minutes after the apply rather than expecting an instant signal — a
post-deploy snapshot taken seconds after the reload would just echo the
pre-deploy state, which is why the bracket was removed.

If checks related to your change start failing, the expected response is:

1. **If the apply succeeded but new checks fail**: roll forward — fix the
   templates and dispatch another apply. Don't roll back the systemd unit
   reload; that just makes the next deploy harder.
2. **If the apply failed mid-flight**: roll back the change with another PR,
   re-dispatch.

## App rollback

Prefer rollback by PR: revert the app version pin in `network-operations` to
the previous known-good SHA, merge the rollback promotion PR, then run
`apply.yml` for the affected playbook.

During an active outage, an operator may pass an explicit old SHA through
Ansible extra-vars from a trusted shell, then follow up with a PR that records
the deployed pin. Extra-vars are an emergency escape hatch, not the normal
promotion path.

## Manual deploy (bypass CI)

When the runner is offline or unreachable. Same shape as the workflow but
local:

```bash
cd ansible
set -a; source ../secrets.local.sh; set +a

# Apply
ansible-playbook playbooks/<pb>.yml --tags apply \
  -e '{"<pb>_apply":true}' \
  --limit <limit>

# Then check live Icinga on mon (or the hyrule MCP icinga_list_problems tool)
# for new problems attributable to the change.
```

Record the deploy in the PR description after the fact.

## Environment protection setup (one-time)

The `production` environment with a required reviewer is set via the GitHub
UI: **Settings → Environments → New environment → production → Required
reviewers: @<your-handle>**. Or via:

```bash
gh api -X PUT /repos/AS215932/network-operations/environments/production \
  -f reviewers='[{"type":"User","id":<user-id>}]'
```

Once set, every `apply.yml` run pauses for approval before the apply step.

## Branch protection setup (one-time)

`main` must require: `lint`, `render-check`, `ai-review` checks. See the
PR #44 description (`feat/0d-ci-auto-merge`) for the exact `gh api` call.

## Common failure modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| Workflow stuck "Waiting for self-hosted runner" | `ci` VM offline / runner not registered | Re-run `docs/ci/provision.md` from step 4 |
| Apply step fails with "Permission denied (publickey)" | `runner` user's SSH key not in target host's authorized_keys | Push key via `playbooks/noc-mcp-key.yml`-style fan-out (filed as follow-up if not done) |
