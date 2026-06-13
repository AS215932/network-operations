# AS215932 CI/CD inventory

Authoritative snapshot of the org's CI/CD surface as of **2026-05-31**, captured
at the start of the CI/CD modernization effort (PR-Agent + Semgrep + two-runner
security model). Verified live against the GitHub org and each repo's default
branch. Update this file when workflows, runners, secrets, or required checks
change.

> Naming note: the local working-copy directory `hyrule-infra/` maps to the
> GitHub repo **`AS215932/network-operations`**. There is **no** repo named
> `hyrule-infra`. Use `network-operations` everywhere.

## Repositories

| Repo | Stack | Workflows (`main`) | Branch protection / required checks | Deploys? | AI review | Semgrep |
|------|-------|--------------------|-------------------------------------|----------|-----------|---------|
| `network-operations` | Ansible / IaC + Python tests | `lint.yml`, `render-check.yml`, `iac-tests.yml`, `apply.yml`, `drift-detection.yml` | **Protected** — required: `yamllint`, `ansible-lint`, `shellcheck`, `jinja-syntax`, `render`, `Sourcery review` (strict) | Yes (`apply.yml`, manual + `production`) | Sourcery (to remove) | none yet |
| `hyrule-web` | Python (uv) + TS/Vite | `ci.yml` (`test`, `frontend`), `deploy.yml` | **Protected** — required: `test`, `frontend` (strict) | Yes (`deploy.yml`, push→main / dispatch, `production`) | Sourcery (to remove) | none yet |
| `hyrule-cloud` | Python (uv), FastAPI / x402 | `ci.yml` (`test`), `deploy.yml` | **Not protected** | Yes (`deploy.yml`, `production`) | Sourcery (to remove) | none yet |
| `noc-agent` | Python ≥3.14, PydanticAI / langgraph / redis / mcp | none | **Not protected** | No | Sourcery (to remove) | none yet |
| `hyrule-mcp` | Python ≥3.14, mcp | none | **Not protected** | No | Sourcery (to remove) | none yet |
| `as215932.net` | Static HTML / CSS + `deploy.sh` | none | **Not protected** | `deploy.sh` (trigger TBD) | Sourcery (to remove) | none yet |
| `engineering-loop` | Python (uv), LangGraph | `ci.yml` (`ruff`, `mypy`, `pytest`) | **Protected** — required: `ruff`, `mypy`, `pytest` (strict) | No (opens draft PRs only) | claude-for-github | planned |

Notes:

- Check names on `network-operations` are **bare job ids** (`yamllint`, not
  `lint / yamllint`); `render` is the job in `render-check.yml`. The
  `iac-tests.yml` jobs (`static-iac`, `ansible-idempotency`, `batfish`,
  `containerlab-frr`) are **not** required yet.
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
- `engineering-loop` was extracted from `network-operations` (Phase G of the
  v2 refactor, issue #196) with history preserved; see
  `docs/ci/engineering-loop-extraction.md`. Its CI runs **only** on the
  unprivileged `ci-pr` runner (group `public-pr`) — never `hyrule-ci` — because
  its backend executes generated code; the daemon refuses to run when
  `GITHUB_ACTIONS` is set. The full suite is offline (mock backend, no API
  keys). Until the extraction lands this row is **pending**.

## Runner topology (today)

One org-scoped self-hosted runner:

- **`ci-runner`** on the `ci` VM (`2a0c:b641:b50:2::d0`), online, labels
  `self-hosted, Linux, X64, hyrule, hyrule-infra`.
- **Privileged**: Vault AppRole → `/etc/github-runner/secrets.env`, the fleet
  deploy key `id_ci` (`/var/lib/github-runner/.ssh/id_ci`), Docker + Containerlab,
  and overlay-v6 reach to every infra host. Provisioned by the toggle-driven
  `ansible/roles/github_runner` role (+ `ansible/roles/ci_runner_key`). Host
  vars: `ansible/inventory/host_vars/ci.yml`. Provisioning runbook:
  `docs/ci/provision.md`.

Runner groups (org Actions settings):

| Group | id | Visibility | Repos | Runners |
|-------|----|-----------|-------|---------|
| `Default` | 1 | all | (all) | none |
| `hyrule-ci` | 3 | selected | `hyrule-cloud`, `hyrule-web`, `network-operations` | `ci-runner` |

**Consequence**: today every `pull_request` job in web/cloud/network-operations
runs untrusted PR code on the single privileged runner. The only controls are
GitHub's fork-PR approval requirement (public repos) and the `hyrule-ci` group
ACL. This is the exposure the two-runner model closes.

## Secrets & credentials

| Name | Scope | Used by | Purpose |
|------|-------|---------|---------|
| `HYRULE_INFRA_DEPLOY_KEY` | repo (`hyrule-web`, `hyrule-cloud`) | `deploy.yml` | Deploy key to checkout `network-operations` (Ansible) during app deploy |
| Vault-rendered `/etc/github-runner/secrets.env` | on `ci` host | `apply.yml`, `deploy.yml`, `drift-detection.yml` | `DISCORD_WEBHOOK_URL`, `ICINGA_API_*`, etc. for privileged Ansible runs |
| `id_ci` | on `ci` host | `apply.yml`, app `deploy.yml` | SSH as the `ci` deploy user across the fleet |
| `OPENROUTER_API_KEY` | **org (planned)** | `pr-agent.yml` (all repos) | PR-Agent LLM calls via OpenRouter — read/comment-only, `ci-pr` only |

Semgrep is **token-less** (no `SEMGREP_APP_TOKEN`): it uploads SARIF to GitHub
Code Scanning, free for these public repos.

## Installed GitHub Apps (org)

| App | Repo selection | Disposition |
|-----|----------------|-------------|
| `claude-for-github` | all | keep |
| `claude` | all | keep |
| `sourcery-ai` | all | **remove** — drop the `Sourcery review` required check on `network-operations` first, then uninstall/limit the app |

## Target architecture (being implemented)

- **Two-runner security model**: keep the privileged `ci-runner` (`hyrule`,
  `hyrule-infra`, group `hyrule-ci`) for deploy/apply/Vault/labs only; add a new
  **unprivileged `ci-pr`** runner (label `hyrule-public-pr`, its own `public-pr`
  runner group permitting all repos — the original six plus `engineering-loop`)
  with no Vault, no `id_ci`, no `secrets.env`, and no management-overlay
  reachability. All untrusted-PR-code jobs (PR-Agent, Semgrep,
  lint/test/build/static checks) move to `ci-pr`. `engineering-loop` is
  **`ci-pr`-only** by construction — its loop backend runs generated code, so
  it must never touch the privileged runner.
- **PR-Agent** replaces Sourcery: advisory, read/comment-only, OpenRouter
  primary `openrouter/deepseek/deepseek-v4-flash`, fallback
  `openrouter/minimax/minimax-m2.7`, pinned `The-PR-Agent/pr-agent` Docker
  action, same-repo-PR + trusted-author gated (no secret on fork PRs).
- **Semgrep** added to all repos (reporting-only first, then gating on
  high-confidence findings).
- Full design, waves, and acceptance criteria: the CI/CD modernization plan
  (see `docs/ci/security-model.md` and `docs/ci/runner-threat-model.md` once
  written).
