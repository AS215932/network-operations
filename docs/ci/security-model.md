# CI/CD security model — the two-runner architecture

AS215932 CI runs on self-hosted runners with reach into a production ISP
network. The governing rule: **untrusted PR code and the LLM reviewer never
touch deploy credentials or the production network.** That is enforced by
splitting work across two runner classes.

## The two runners

| | `ci` (privileged) | `ci-pr` (unprivileged) |
|---|---|---|
| Host | `ci` VM, infra segment `2a0c:b641:b50:2::d0` | `ci-pr` VM, **customer** segment `2a0c:b641:b51::c1` |
| Labels | `hyrule`, `hyrule-infra` | `hyrule-public-pr` |
| Org runner group | `hyrule-ci` | `public-pr` |
| Vault / `id_ci` / `secrets.env` | yes | **no** |
| Reach to infra mgmt | yes | **no** (customer-isolated) |
| Docker / Containerlab | yes | Docker only |
| Runs | deploy/apply, Vault-backed Ansible, `production` jobs, Batfish/Containerlab labs | PR-Agent, Semgrep, all untrusted-PR test/lint jobs |

`ci-pr` is treated as **disposable and potentially attacker-controlled**: it
runs untrusted PR code and keeps Docker, so a malicious PR may be able to root
it — and that must not matter. Nothing of value lives there (no Vault, no
`id_ci`, no `secrets.env`, no mgmt route, no privileged bind-mounts). The
inventory schema gate (`tests/iac/test_inventory_schema.py`) pins the data-layer
half of this invariant: `ci-pr` must be in `customer_subnet` and never in
`infra_subnet`.

## Enforcement is layered (runner groups are necessary but not sufficient)

Runner-group ACLs control *which repositories* may target a runner class. They
do **not**, by themselves, stop a workflow inside a repo permitted to both
groups from selecting the wrong label. Job-level separation is the combination
of:

1. **Runner groups** (`public-pr` → selected public repos, including
   `engineering-loop`; `hyrule-ci` → only repos that deploy). A repo not in a
   group can't use it even if a workflow names the label.
2. **Trigger discipline** — privileged labels (`hyrule`/`hyrule-infra`) appear
   only in trusted `push`/`workflow_dispatch`/`schedule`/deploy/`environment`
   jobs, **never** in a `pull_request` job. The heavy labs keep the privileged
   label only because they are `if`-gated off `pull_request`
   (`workflow_dispatch` / repo var).
3. **Contract test** — `tests/iac/test_vault_and_runner_contracts.py`
   (`test_pull_request_jobs_use_the_unprivileged_runner`) fails CI if any
   `pull_request` job uses a privileged label without that if-gate, and
   (`test_privileged_deploy_workflows_stay_on_ci_runner`) that apply/drift stay
   on `ci` and never leak onto `ci-pr`.
4. **CODEOWNERS** (`.github/CODEOWNERS` → `@AS215932/ops`) on workflows + the
   high-blast-radius router/firewall configs.
5. **Branch protection** (`docs/ci/branch-protection.md`).
6. **Semgrep** flags `pull_request_target`, `permissions: write-all`, unpinned
   third-party actions, and privileged-runner use in PR workflows.

No `pull_request_target` anywhere. Least-privilege `permissions:` on every job.

## The Wave 4 migration guard

Before any `network-operations` job was moved from `ci` to `ci-pr`, it was
proven unprivileged: no Vault read, no `secrets.env` source, no `id_ci`, no
mgmt-overlay reach, no privileged Docker/Containerlab, repo-local files +
normal toolchain only. Jobs that fail any check stay on `ci` (the Batfish and
Containerlab labs) and are classified trusted-only. See
`docs/netops/testing-strategy.md` for the resulting tier placement.

## Public-fork policy

Repos are public and PR-Agent needs `OPENROUTER_API_KEY`. External-fork PRs do
**not** receive the secret and do **not** get LLM review; PR-Agent auto-runs
only for same-repo PRs and slash commands only from
`OWNER`/`MEMBER`/`COLLABORATOR`. See `docs/ci/pr-agent.md`.
