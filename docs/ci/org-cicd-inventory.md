# AS215932 CI/CD inventory

Authoritative snapshot of the org's CI/CD surface as of **2026-06-17**, captured
at the start of the CI/CD modernization effort (PR-Agent + Semgrep + two-runner
security model) and updated after the Engineering Loop extraction. Keep this
file in sync when workflows, runners, secrets, or required checks change.

> Naming note: the local working-copy directory `hyrule-infra/` maps to the
> GitHub repo **`AS215932/network-operations`**. There is **no** repo named
> `hyrule-infra`. Use `network-operations` everywhere.

## Repositories

| Repo | Stack | Workflows (`main`) | Branch protection / required checks | Deploys? | AI review | Semgrep |
|------|-------|--------------------|-------------------------------------|----------|-----------|---------|
| `network-operations` | Ansible / IaC + Python tests | `lint.yml`, `render-check.yml`, `iac-tests.yml`, `apply.yml`, `drift-detection.yml` | **Protected** — required: `lint`, `render`, `iac-gate`, `semgrep` (strict) | Yes (`apply.yml`, manual + `production`) | PR-Agent advisory | token-less SARIF |
| `engineering-loop` | Python (uv), LangGraph, Pi extension | `ci.yml` (`pytest`, `ruff`, `mypy`), `semgrep.yml` | **Protected** — required: `pytest`, `ruff`, `mypy`, `semgrep` (strict) | No — runtime deploy state remains in `network-operations` | PR-Agent advisory | token-less SARIF |
| `hyrule-web` | Python (uv) + TS/Vite | `ci.yml` (`test`, `frontend`), `deploy.yml` | **Protected** — required: `test`, `frontend` (strict) | Yes (`deploy.yml`, push→main / dispatch, `production`) | Sourcery (to remove) | none yet |
| `hyrule-cloud` | Python (uv), FastAPI / x402 | `ci.yml` (`test`), `deploy.yml` | **Not protected** | Yes (`deploy.yml`, `production`) | Sourcery (to remove) | none yet |
| `noc-agent` | Python ≥3.14, PydanticAI / langgraph / redis / mcp | none | **Not protected** | No | Sourcery (to remove) | none yet |
| `hyrule-mcp` | Python ≥3.14, mcp | none | **Not protected** | No | Sourcery (to remove) | none yet |
| `as215932.net` | Static HTML / CSS + `deploy.sh` | none | **Not protected** | `deploy.sh` (trigger TBD) | Sourcery (to remove) | none yet |

Notes:

- Check names on `network-operations` are **bare job ids**: `lint`, `render`,
  `iac-gate`, and `semgrep`. `lint.yml` intentionally reports a single `lint`
  job while running yamllint, ansible-lint, shellcheck, and Jinja syntax checks
  as steps to reduce queue slots on the single public PR runner. The
  `iac-tests.yml` tier jobs (`static-iac`, `ansible-idempotency`, `batfish`,
  `containerlab-frr`) are **not** required individually; `iac-gate` is the
  required aggregate context.
- `engineering-loop` now owns the loop runtime code, prompt/skill library,
  Pi `/loop` extension, model policy, and loop test suite. `network-operations`
  keeps only Ansible deployment state for the dedicated `loop` VM.
- `hyrule-cloud` `ci.yml` lints/types **touched files only**, and `mypy
  --strict` is currently suffixed `|| true` (deliberate, temporary — tracked as
  the post-A0 type-cleanup PR's exit criterion). Its in-file comment claims
  branch protection, but `main` is currently **unprotected**.
- `hyrule-cloud` `ci.yml` runs `scripts/verify_facilitator.py` only when
  `PaymentConfig` changes (the verified-payment-chains gate).
- `hyrule-web` `ci.yml` enforces ruff, strict mypy on `hyrule_web/`, pytest with
  a 90% line+branch coverage gate, the frontend lint/typecheck/Vitest/Vite
  pipeline, and a **committed-`dist` drift guard** (the web host has no Node;
  deploy git-checks-out the repo, so `hyrule_web/static/dist` must equal a fresh
  build).
- `noc-agent` and `hyrule-mcp` both require **Python ≥3.14** and currently
  declare **no ruff/mypy**; both ship a `test_live_smoke.py` that needs live
  infrastructure (must be deselected in CI).

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
| `sourcery-ai` | all | **remove** — drop the `Sourcery review` required check on `network-operations` first, then uninstall/limit the app |

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
