# Rollback by SHA for hyrule-web and hyrule-cloud

How to roll a production app back to a previously-deployed commit SHA using
the existing promotion pipeline, verify the rollback, and roll forward again.

This runbook is the acceptance artifact for issue #358 (split from #142).
Perform it once per app to prove the path works under pressure.

## Quick reference

| Step | hyrule-web | hyrule-cloud |
|---|---|---|
| Record current good SHA | `ansible/inventory/host_vars/web.yml` | `ansible/inventory/host_vars/api.yml` |
| Rollback SHA | previous known-good SHA | previous known-good SHA |
| Migration concern | None (stateless) | Alembic `upgrade head` must be N‑1 safe, or a manual downgrade step is required |
| Playbook | `web` | `cloud` |
| Limit | `web` | `api` |
| Health check | `curl -s http://127.0.0.1:8080/` | `curl -s http://[::1]:8402/health` |
| Version signal | Git SHA in `/opt/hyrule-web/.git/refs/heads/…` or remote log | Git SHA in `/opt/hyrule-cloud/.git/…` and `/health` response |

## Before you start

1. **Pick a quiet window** — avoid customer-visible launches, billing windows,
   or overlapping infrastructure changes.
2. **Capture current good SHAs** from the inventory files. These are the values
   you will roll *forward* to after the rollback test.
3. **Confirm CI is green** for both the current SHA and the rollback target SHA.
   The promotion workflow validates this, but an operator should eyeball it.
4. **Notify** — post in the ops channel that a rollback rehearsal is starting.

## 1. Record baseline SHAs

```bash
cd ansible/inventory/host_vars
echo "web  current: $(sed -n 's/^hyrule_web_version:[[:space:]]*//p' web.yml)"
echo "api  current: $(sed -n 's/^hyrule_cloud_version:[[:space:]]*//p' api.yml)"
```

Save these. The rollback test uses the *previous* known-good SHA for each app.

## 2. Rollback hyrule-web

### 2.1 Open the rollback promotion PR

Use `promote-apps` with the **previous** known-good SHA. Do **not** hand-edit
`host_vars/web.yml`; the promotion workflow regenerates the PR body with
compare links and rollback SHAs automatically.

```bash
# From the repo root, on a fresh branch from main
git fetch origin main
git switch -c ops/rollback-web origin/main

gh workflow run promote-apps.yml \
  -F title="Rollback hyrule-web to <SHORT_SHA>" \
  -F hyrule_web_sha="<PREVIOUS_KNOWN_GOOD_SHA_40_CHARS>" \
  -F impact="Rollback rehearsal for #358 — hyrule-web"
```

Wait for the workflow to open the promotion PR, then:

1. Review the PR body. Confirm the **Rollback** section lists the current
   SHA you will return to.
2. Confirm `scripts/ci/iac-static.sh` passes in CI.
3. Merge the PR.

### 2.2 Approve the production gate

`app-promotion-deploy` triggers automatically on the merge to `main`.

1. Go to the running `apply.yml` workflow for playbook `web`.
2. Approve the **`production` environment gate**.
3. Wait for the apply to finish and the **post-deploy Icinga snapshot** to
   upload.

### 2.3 Verify the rollback

SSH to the web VM and confirm the running code is the target SHA:

```bash
ssh -i ~/.ssh/id_servify root@2a0c:b641:b50:2::30 \
  'cd /opt/hyrule-web && git rev-parse HEAD'
# Must match the SHA you promoted.
```

Confirm the service is healthy:

```bash
ssh -i ~/.ssh/id_servify root@2a0c:b641:b50:2::30 \
  'systemctl is-active hyrule-web && curl -sf http://127.0.0.1:8080/ && echo OK'
```

Confirm the site is reachable through the proxy:

```bash
curl -sf https://hyrule.host/ && echo "hyrule.host OK"
```

Check the Icinga snapshot diff in the workflow summary. There should be **no
new problems** (`+` lines) related to `hyrule-web`.

### 2.4 Roll forward again

Repeat the promotion workflow with the **original** (current good) SHA to
restore the forward state:

```bash
gh workflow run promote-apps.yml \
  -F title="Roll forward hyrule-web" \
  -F hyrule_web_sha="<ORIGINAL_GOOD_SHA_40_CHARS>" \
  -F impact="Roll forward after rollback rehearsal #358"
```

Merge the PR, approve the gate, and verify the forward SHA is active with the
same checks as in §2.3.

## 3. Rollback hyrule-cloud

### 3.1 Migration compatibility posture

`hyrule-cloud` runs Alembic `upgrade head` on every apply (`ansible/roles/
hyrule_cloud/tasks/health.yml`). This means a rollback to an older code SHA
will start the old code against whatever schema `upgrade head` produced from
the *newer* code.

**Rule:** Hyrule Cloud migrations must be **N‑1 compatible** — the code at
SHA `N-1` must still function correctly against the schema produced by the
migrations at SHA `N`.

If a migration is **not** N‑1 compatible (e.g. it renames a column the old
code still reads, drops a table the old code references, or changes enum
values), a one-release rollback by SHA **alone is unsafe**. In that case you
must either:

- **Include a manual downgrade** — run the appropriate Alembic downgrade on
  the `api` VM *before* the rollback apply, or
- **Accept that a single-release rollback requires operator intervention** and
  document the downgrade revision in the promotion PR.

For the purpose of this rehearsal, choose a rollback target whose migrations
are known to be N‑1 compatible (typically any adjacent pair after a routine
feature merge with additive-only changes).

### 3.2 Open the rollback promotion PR

Same shape as web, using the `cloud` playbook:

```bash
git switch -c ops/rollback-cloud origin/main

gh workflow run promote-apps.yml \
  -F title="Rollback hyrule-cloud to <SHORT_SHA>" \
  -F hyrule_cloud_sha="<PREVIOUS_KNOWN_GOOD_SHA_40_CHARS>" \
  -F impact="Rollback rehearsal for #358 — hyrule-cloud"
```

Review, confirm iac-static, merge.

### 3.3 Approve the production gate

`app-promotion-deploy` calls `apply.yml` for playbook `cloud`, limit `api`.
Approve the `production` gate and wait for completion.

### 3.4 Verify the rollback

SSH to the api VM and confirm the target SHA:

```bash
ssh -i ~/.ssh/id_servify root@2a0c:b641:b50:2::20 \
  'cd /opt/hyrule-cloud && git rev-parse HEAD'
# Must match the promoted SHA.
```

Confirm the service and Alembic state:

```bash
ssh -i ~/.ssh/id_servify root@2a0c:b641:b50:2::20 \
  'systemctl is-active hyrule-cloud && curl -sf http://[::1]:8402/health && echo OK'
```

Smoke the public API via the proxy:

```bash
curl -sf https://cloud.hyrule.host/health && echo "cloud.hyrule.host OK"
```

Check the Icinga snapshot diff. New problems related to `hyrule-cloud`,
`postgres_exporter`, or `monero-wallet-rpc` are **unexpected** and should be
investigated before declaring the rollback successful.

### 3.5 Confirm N‑1 migration compatibility

From the api VM, inspect the Alembic revision:

```bash
ssh -i ~/.ssh/id_servify root@2a0c:b641:b50:2::20 \
  'cd /opt/hyrule-cloud && uv run alembic current'
```

The printed revision should be the **head revision from the newer SHA** (the
one you rolled back *from*), because `apply.yml` ran `upgrade head` before
restarting with the old code. If the old code is still functional and the
health check passes, the migration is N‑1 compatible for this pair.

If the service failed to start or health-check, capture logs:

```bash
ssh -i ~/.ssh/id_servify root@2a0c:b641:b50:2::20 \
  'journalctl -u hyrule-cloud --since "10 minutes ago" --no-pager'
```

and treat it as a migration-compatibility finding. Update the app repo’s
migration policy (or the PR template checklist) to require N‑1 compatibility
for merging.

### 3.6 Roll forward again

Promote the original good SHA back to `api.yml`:

```bash
gh workflow run promote-apps.yml \
  -F title="Roll forward hyrule-cloud" \
  -F hyrule_cloud_sha="<ORIGINAL_GOOD_SHA_40_CHARS>" \
  -F impact="Roll forward after rollback rehearsal #358"
```

Merge, approve the gate, verify with the same checks as §3.4.

## 4. Emergency rollback (outage, no PR yet)

When the site is down and you need the old code *now*, use Ansible extra-vars
as an escape hatch, then follow up with a PR that records the pin.

```bash
cd ansible
set -a; source ../secrets.local.sh; set +a

# Rollback hyrule-web immediately (example)
ansible-playbook playbooks/web.yml --tags apply \
  -e ansible_user=ci \
  -e hyrule_web_apply=true \
  -e hyrule_web_version="<PREVIOUS_KNOWN_GOOD_SHA>" \
  --limit web

# Rollback hyrule-cloud immediately (example)
ansible-playbook playbooks/cloud.yml --tags apply \
  -e ansible_user=ci \
  -e hyrule_cloud_apply=true \
  -e hyrule_cloud_version="<PREVIOUS_KNOWN_GOOD_SHA>" \
  --limit api
```

**After** the service recovers, open a promotion PR that pins the same SHA
into `host_vars/web.yml` or `host_vars/api.yml` so `main` stays truthful.
Do not leave the inventory file out of sync with the live state.

## 5. Runbook checklist

Use this checklist when performing the rehearsal, or paste it into the tracker
issue.

### hyrule-web

- [ ] Current good SHA recorded.
- [ ] Previous known-good SHA identified.
- [ ] `promote-apps.yml` dispatched with rollback SHA.
- [ ] Promotion PR reviewed and merged.
- [ ] `production` gate approved.
- [ ] Post-deploy Icinga snapshot diff reviewed (no new web problems).
- [ ] `git rev-parse HEAD` on web VM matches rollback SHA.
- [ ] `systemctl is-active hyrule-web` == `active`.
- [ ] `curl -sf https://hyrule.host/` succeeds.
- [ ] Roll-forward PR opened, merged, and verified.

### hyrule-cloud

- [ ] Current good SHA recorded.
- [ ] Previous known-good SHA identified.
- [ ] Migration compatibility verified as N‑1 safe (or downgrade step planned).
- [ ] `promote-apps.yml` dispatched with rollback SHA.
- [ ] Promotion PR reviewed and merged.
- [ ] `production` gate approved.
- [ ] Post-deploy Icinga snapshot diff reviewed (no new api problems).
- [ ] `git rev-parse HEAD` on api VM matches rollback SHA.
- [ ] `systemctl is-active hyrule-cloud` == `active`.
- [ ] `curl -sf https://cloud.hyrule.host/health` succeeds.
- [ ] `alembic current` shows head from the *forward* SHA (proving rollback
code runs against newer schema).
- [ ] Roll-forward PR opened, merged, and verified.

## Related

- `docs/ci/deploy-runbook.md` — Promotion pipeline overview and normal deploy flow.
- `.github/workflows/promote-apps.yml` — Automation that opens promotion PRs.
- `.github/workflows/app-promotion-deploy.yml` — Auto-detects merged pin
changes and calls `apply.yml`.
- `.github/workflows/apply.yml` — The gated apply workflow with Icinga
snapshot bracket.
- `ansible/roles/hyrule_web/tasks/health.yml` — Web restart + health check.
- `ansible/roles/hyrule_cloud/tasks/health.yml` — Cloud restart, Alembic
`upgrade head`, and health check.
- `docs/runbooks/vps-launch-proof-smoke.md` — Related rehearsal that also
exercises rollback-by-SHA for hyrule-cloud.
