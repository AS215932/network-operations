# AS215932 CI/CD inventory

Authoritative inventory of the org's CI/CD surface. Repository workflows and
branch protection were verified on **2026-08-03**; the runner and credential
sections retain the **2026-06-17** modernization snapshot. Keep this file in
sync when workflows, runners, secrets, or required checks change.

> Naming note: the local working-copy directory `hyrule-infra/` maps to the
> GitHub repo **`AS215932/network-operations`**. There is **no** repo named
> `hyrule-infra`. Use `network-operations` everywhere.

## Repositories

| Repo | Stack | Workflows (`main`) | Branch protection / required checks | Deploys? | AI review | Semgrep |
|------|-------|--------------------|-------------------------------------|----------|-----------|---------|
| `agent-core` | Python | `ci.yml`, `release.yml` | **Protected** — `test` (strict, 0 reviews) | Release workflow | PR-Agent advisory | none |
| `agentic-observatory` | Python + frontend | `test.yml` | **Protected** — `python`, `frontend` (strict, 0 reviews) | No | PR-Agent advisory | none |
| `as215932.net` | Static HTML / CSS | `semgrep.yml` | **Protected** — `semgrep` (strict, 0 reviews) | `deploy.sh` | PR-Agent advisory | token-less SARIF |
| `engineering-loop` | Python (uv), LangGraph, Pi extension | `ci.yml` | **Protected** — `pytest`, `ruff`, `mypy` (strict, 0 reviews) | Runtime state remains in `network-operations` | PR-Agent advisory | none |
| `hyrule-beacon` | TypeScript | `ci.yml`, `docker-image.yml`, `pr-preview.yml`, `sourcemaps.yml` | **Protected** — `ci`, `docker-build` (strict, 0 reviews) | Image/preview workflows | none | none |
| `hyrule-business` | Python | `ci.yml` | **Unprotected** — private-repo protection unavailable on current plan | No | none | none |
| `hyrule-cloud` | Python (uv), FastAPI / x402 | `ci.yml`, `deploy.yml`, `request-promotion.yml`, `semgrep.yml` | **Protected** — `test`, `semgrep` (strict, 0 reviews) | Promotion through `network-operations` | PR-Agent advisory | token-less SARIF |
| `hyrule-mcp` | Python ≥3.14, MCP | `ci.yml`, `request-promotion.yml`, `semgrep.yml` | **Protected** — `semgrep`, `test` (strict, 0 reviews) | Promotion through `network-operations` | PR-Agent advisory | token-less SARIF |
| `hyrule-network-proxy` | Go | `ci.yml`, `request-promotion.yml`, `semgrep.yml` | **Protected** — `go`, `semgrep` (strict, 0 reviews) | Promotion through `network-operations` | PR-Agent advisory | token-less SARIF |
| `hyrule-prober` | Python | `ci.yml` | **Protected** — `test` (strict, 0 reviews) | No | none | none |
| `hyrule-seo-agent` | Python | `ci.yml`, `request-promotion.yml`, `semgrep.yml` | **Protected** — `test`, `semgrep` (strict, 0 reviews) | Promotion workflow | none | token-less SARIF |
| `hyrule-web` | Python (uv) + TS/Vite | `ci.yml`, `deploy-validation.yml`, `request-promotion.yml`, `semgrep.yml` | **Protected** — `test`, `frontend` (strict, 0 reviews) | Promotion through `network-operations` | PR-Agent advisory | token-less SARIF |
| `knowledge` | Python | `validate.yml`, `ingest.yml`, `enrich.yml`, `auto-merge.yml` | **Protected** — `validate` (strict, 0 reviews) | No | PR-Agent advisory | none |
| `network-operations` | Ansible / IaC + Python tests | `lint.yml`, `render-check.yml`, `iac-tests.yml`, `apply.yml`, promotion/deploy workflows | **Protected** — `lint`, `render`, `iac-gate`, `semgrep` (strict, 0 reviews) | Yes (`apply.yml`, main-only `production`) | PR-Agent advisory | token-less SARIF |
| `noc-agent` | Python ≥3.14, PydanticAI / LangGraph | `ci.yml`, `request-promotion.yml`, `semgrep.yml` | **Protected** — `semgrep`, `test` (strict, 0 reviews) | Promotion through `network-operations` | PR-Agent advisory | token-less SARIF |
| `soc-agent` | Python | `pr-agent.yml` | **Unprotected** — no substantive PR test workflow | No | PR-Agent advisory | none |
| `.github` | Org metadata | none | **Unprotected** — no substantive PR test workflow | No | none | none |

Notes:

- Check names on `network-operations` are **bare job ids**: `lint`, `render`,
  `iac-gate`, and `semgrep`. `lint.yml` intentionally reports a single `lint`
  job while running yamllint, ansible-lint, shellcheck, and Jinja syntax checks
  as steps to reduce queue slots on the single public PR runner. The
  `iac-tests.yml` tier jobs (`static-iac`, `ansible-idempotency`, `batfish`,
  `containerlab-frr`) are **not** required individually; `iac-gate` is the
  required aggregate context.
- All protected branches use status-only authorization: strict required checks,
  no approving review requirement, no force pushes, and no branch deletion.
  Agents use the normal merge action after checks and review feedback settle;
  native auto-merge is disabled.
- `engineering-loop` now owns the loop runtime code, prompt/skill library,
  Pi `/loop` extension, model policy, and loop test suite. `network-operations`
  keeps only Ansible deployment state for the dedicated `loop` VM.
- `hyrule-cloud` `ci.yml` lints/types **touched files only**, and `mypy
  --strict` is currently suffixed `|| true` (deliberate, temporary — tracked as
  the post-A0 type-cleanup PR's exit criterion).
- `hyrule-cloud` `ci.yml` runs `scripts/verify_facilitator.py` only when
  `PaymentConfig` changes (the verified-payment-chains gate).
- `hyrule-web` `ci.yml` enforces ruff, strict mypy on `hyrule_web/`, pytest with
  a 90% line+branch coverage gate, the frontend lint/typecheck/Vitest/Vite
  pipeline, and a **committed-`dist` drift guard** (the web host has no Node;
  deploy git-checks-out the repo, so `hyrule_web/static/dist` must equal a fresh
  build).
- The exact required contexts and protection reproduction command live in
  [branch-protection.md](./branch-protection.md).

## Runner topology (today)

Two org-scoped self-hosted runners:

- **`ci-runner`** on the `ci` VM (`2a0c:b641:b50:2::d0`), sized **4 vCPU / 8 GiB RAM** plus a 20 GiB root disk and 50 GiB runner data disk, labels `self-hosted, Linux, X64, hyrule, hyrule-infra`.
- **`ci-pr-runner-recovery2`** on the `ci-pr` VM (`2a0c:b641:b51::c1`), sized **4 vCPU / 8 GiB RAM** with a 20 GiB root disk, labels `self-hosted, Linux, X64, hyrule-public-pr`.
- **Privileged `ci`**: Vault AppRole → `/etc/github-runner/secrets.env`, the fleet deploy key `id_ci` (`/var/lib/github-runner/.ssh/id_ci`), Docker + Containerlab, and overlay-v6 reach to every infra host. Provisioned by the toggle-driven `ansible/roles/github_runner` role (+ `ansible/roles/ci_runner_key`). Host vars: `ansible/inventory/host_vars/ci.yml`. Provisioning runbook: `docs/ci/provision.md`.
- **Unprivileged `ci-pr`**: no Vault, no `id_ci`, no `secrets.env`, no management-overlay reachability, Docker only. Provisioned by `ansible/roles/github_runner` with the unprivileged host vars in `ansible/inventory/host_vars/ci-pr.yml`. Provisioning runbook: `docs/ci/provision-ci-pr.md`.

Runner groups (org Actions settings):

| Group | id | Visibility | Repos | Runners |
|-------|----|-----------|-------|---------|
| `Default` | 1 | all | (all) | none |
| `hyrule-ci` | 3 | selected | `hyrule-cloud`, `hyrule-web`, `network-operations` | `ci-runner` |
| `public-pr` | org-scoped | selected | AS215932 repos with untrusted PR jobs | `ci-pr-runner-recovery2` |

**Consequence**: untrusted `pull_request` jobs run on the isolated `ci-pr` runner, while deploy/apply/lab work stays on the privileged `ci` runner. Each VM still runs a single GitHub Actions runner process, so resizing improves per-job runtime and memory headroom without increasing job concurrency.

## Secrets & credentials

| Name | Scope | Used by | Purpose |
|------|-------|---------|---------|
| `HYRULE_INFRA_DEPLOY_KEY` | repo (`hyrule-web`, `hyrule-cloud`) | `deploy.yml` | Deploy key to checkout `network-operations` (Ansible) during app deploy |
| Vault-rendered `/etc/github-runner/secrets.env` | on `ci` host | `apply.yml`, `deploy.yml`, `drift-detection.yml` | `DISCORD_WEBHOOK_URL`, `ICINGA_API_*`, etc. for privileged Ansible runs |
| `id_ci` | on `ci` host | `apply.yml`, app `deploy.yml` | SSH as the `ci` deploy user across the fleet |
| `OPENROUTER_API_KEY` | **org (planned)** | `pr-agent.yml` (selected public repos, including `engineering-loop`) | PR-Agent LLM calls via OpenRouter — read/comment-only, `ci-pr` only |

Semgrep is **token-less** (no `SEMGREP_APP_TOKEN`): it uploads SARIF to GitHub
Code Scanning, free for these public repos.

## Installed GitHub Apps (org)

| App | Repo selection | Disposition |
|-----|----------------|-------------|
| `claude-for-github` | all | keep |
| `claude` | all | keep |

## Current architecture

- **Two-runner security model**: the privileged `ci-runner` (`hyrule`,
  `hyrule-infra`, group `hyrule-ci`) runs deploy/apply/Vault/labs only. The
  unprivileged `ci-pr` runner (label `hyrule-public-pr`, `public-pr` runner
  group) has no Vault, no `id_ci`, no `secrets.env`, and no management-overlay
  reachability. All untrusted-PR-code jobs (PR-Agent, Semgrep,
  lint/test/build/static checks) run on `ci-pr`.
- **PR-Agent** replaces Sourcery: advisory, read/comment-only, OpenRouter
  primary `openrouter/deepseek/deepseek-v4-flash`, fallback
  `openrouter/minimax/minimax-m2.7`, pinned `The-PR-Agent/pr-agent` Docker
  action, same-repo-PR + trusted-author gated (no secret on fork PRs).
- **Semgrep** added to all repos (reporting-only first, then gating on
  high-confidence findings).
- Full design, waves, and acceptance criteria: the CI/CD modernization plan
  (see `docs/ci/security-model.md` and `docs/ci/runner-threat-model.md` once
  written).
