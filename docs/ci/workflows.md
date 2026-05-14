# CI workflows

All CI runs on the self-hosted `ci` VM (see [docs/ci/provision.md](./provision.md)).
Workflows match the runner label set `self-hosted, linux, x64, hyrule-infra`.

## Workflows

| Workflow | Trigger | Purpose | PR |
|----------|---------|---------|----|
| `lint.yml` | `pull_request`, `push` to `main` | yamllint + ansible-lint + shellcheck + Jinja2 syntax | 0b |
| `render-check.yml` | `pull_request` touching `ansible/**`, `configs/**` | render every playbook + assert `ansible/generated/` is fresh | 0b |
| `ai-review.yml` | `pull_request` | Claude API review on diff, file:line comments | 0c |
| `auto-merge.yml` | `pull_request_target` on `labeled` | auto-merge trivial-class PRs (generated/, docs/, research-comment) | 0d |
| `apply.yml` | `workflow_dispatch` | manual gated apply (pre-snapshot, `--tags apply`, post-snapshot, diff) | 0e |

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
