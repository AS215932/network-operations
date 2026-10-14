# CI workflows

Self-hosted untrusted PR checks run on the isolated `ci-pr` runner. Secret-free
public checks may instead use GitHub-hosted runners. Trusted deploy, apply, and
lab work runs on the privileged `ci` runner; see
[security-model.md](./security-model.md) and [provision.md](./provision.md).

## Workflows

| Workflow | Trigger | Purpose | PR |
|----------|---------|---------|----|
| `lint.yml` | `pull_request`, `push` to `main` | yamllint + ansible-lint + shellcheck + Jinja2 syntax + static IaC contracts | 0b |
| `render-check.yml` | every `pull_request`; relevant paths on `main` push | render every playbook + deploy preflight + assert `ansible/generated/` is fresh | 0b |
| `iac-tests.yml` | `pull_request`, `push` to `main`, manual | DNS/inventory/Vault/FRR tests, render idempotency; Batfish/Containerlab run manually or when repo vars enable them | current |
| `drift-detection.yml` | nightly + manual | `ansible-playbook --check --diff`; alerts NOC, never auto-applies | current |
| `apply.yml` | `workflow_dispatch`, `workflow_call` | main-only production apply with runner preflight and postflight Goss validation | 0e |

AI review is advisory and is never a required status context. Native GitHub
auto-merge is disabled. Once all required checks are green and automated review
feedback is resolved, an AI agent may use the normal merge action; no approving
human review is required.

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
Phase 0. The privileged self-hosted runner provides overlay v6 to every host
for apply runs and Vault AppRole access for secrets. The isolated self-hosted
PR runner provides stable CI capacity without inheriting production reach or
credentials.

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
4. **Enable branch protection on `main`** once the foundation checks have
   reported green. Require strict `lint`, `render`, `iac-gate`, and
   `semgrep` contexts, set `required_pull_request_reviews` to `null`, and
   disallow force pushes and branch deletion. See
   [branch-protection.md](./branch-protection.md) for the current rule and
   verification procedure.

`enforce_admins` stays **off** so an administrator can recover the lane if a
required workflow itself is broken. Agents use the ordinary green-check merge
path, not the administrative bypass.
