# CI workflows

All CI runs on the self-hosted `ci` VM (see [docs/ci/provision.md](./provision.md)).
Workflows match the runner label set `self-hosted, linux, x64, hyrule-infra`.

## Workflows

| Workflow | Trigger | Purpose | PR |
|----------|---------|---------|----|
| `lint.yml` | `pull_request`, `push` to `main` | yamllint + ansible-lint + shellcheck + Jinja2 syntax + static IaC contracts | 0b |
| `render-check.yml` | `pull_request` touching `ansible/**`, `configs/**` | render every playbook + deploy preflight + assert `ansible/generated/` is fresh | 0b |
| `iac-tests.yml` | `pull_request`, `push` to `main`, manual | DNS/inventory/Vault/FRR tests, render idempotency; Batfish/Containerlab run manually or when repo vars enable them | current |
| `drift-detection.yml` | nightly + manual | `ansible-playbook --check --diff`; alerts NOC, never auto-applies | current |
| `apply.yml` | `workflow_dispatch` | manual gated apply with runner preflight and postflight Goss validation | 0e |

AI review is handled by the repo's **hosted review service** (configured in
GitHub repo settings), not a workflow we maintain — there is no `ai-review.yml`.
There is also no auto-merge: every PR, including rendered-artifact and
docs-only ones, gets a human merge click.

## Lint config

Both `.yamllint` and `.ansible-lint` start permissive so the existing repo
passes. Tighten via follow-up issues — pick one rule per issue, fix
violations, promote the rule to error.

`scripts/ci/render-all.sh` is the single entry point for "render every
playbook." Use it locally before committing if you've touched any Ansible
template:

```bash
scripts/ci/render-all.sh
git diff ansible/generated/   # commit anything that shows up
```

## Why self-hosted?

Decision recorded in the approved plan `we-need-to-go-zany-robin.md` →
Phase 0. Self-hosted gets us overlay v6 to every host (for apply runs),
Vault AppRole access (for secrets), and a stable network egress (firewall
rules don't chase GH Actions IP ranges).

## Bootstrap chicken-and-egg

These workflows reference the `hyrule-infra` runner label. Before the `ci`
VM is provisioned and the runner registered, jobs queue indefinitely. That's
expected — the first time the runner comes online, all pending PR runs
unfreeze together. Document this in the PR description if you're opening one
during the bootstrap window.

## First-time bootstrap

The foundation PRs lint and render-check *themselves*, so the first merges
can't be gated by checks that don't exist on `main` yet. Bootstrap order:

1. **Provision the `ci` VM and register the runner** —
   [docs/ci/provision.md](./provision.md). Until this is done, every workflow
   job queues.
2. **Wire the runner's Vault AppRole** —
   [docs/runbooks/bootstrap-runner-vault.md](../runbooks/bootstrap-runner-vault.md),
   so `apply.yml` can source `/etc/github-runner/secrets.env`.
3. **Merge the foundation PRs in order, with admin bypass** (branch
   protection isn't on yet, so this is just the normal merge button):
   `0a` (ci VM + `github_runner` role) → `0b` (lint + render-check) →
   `0e` (apply) → `0f` (runner Vault wiring + CODEOWNERS).
4. **Enable branch protection on `main`** once `0f` is merged and no
   foundation PRs are in flight. Require the `lint`, `render-check`, and
   `iac-tests / static-iac` and `iac-tests / ansible-idempotency`
   status checks plus the hosted review service's check (read its exact
   context name off a recent PR's checks list first), and set
   `required_approving_review_count: 1` — since there is no auto-merge,
   every PR needs a human approval.

`enforce_admins` should stay **off** so a broken workflow can still be
force-merged to unblock the lane.
