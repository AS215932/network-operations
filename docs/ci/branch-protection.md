# Branch protection — status-gated agent merges

This is the `main` protection inventory across the org as of **2026-08-03**.
Every protected branch uses strict required status checks, requires no approving
reviews, does not enforce the rule on administrators, and disallows force pushes
and deletion. AI agents wait for the exact required checks below and any
automated review feedback, then use GitHub's normal merge action. Native GitHub
auto-merge remains disabled.

PR-Agent is **never** required: it is advisory and same-repo-only, so requiring
it would wedge fork and Dependabot PRs. Semgrep remains a required *presence*
gate while reporting-only; flip it to a blocking findings gate per
`docs/ci/semgrep.md` once each repo's baseline is triaged.

| Repo | Required checks |
|------|-----------------|
| `agent-core` | `test` |
| `agentic-observatory` | `python`, `frontend` |
| `as215932.net` | `semgrep` |
| `engineering-loop` | `pytest`, `ruff`, `mypy` |
| `hyrule-beacon` | `ci`, `docker-build` |
| `hyrule-cloud` | `test`, `semgrep` |
| `hyrule-mcp` | `semgrep`, `test` |
| `hyrule-network-proxy` | `go`, `semgrep` |
| `hyrule-prober` | `test` |
| `hyrule-seo-agent` | `test`, `semgrep` |
| `hyrule-web` | `test`, `frontend` |
| `knowledge` | `validate` |
| `network-operations` | `lint`, `render`, `iac-gate`, `semgrep` |
| `noc-agent` | `semgrep`, `test` |

Three repositories are intentionally outside this matrix:

- `hyrule-business` is private and branch protection is unavailable under the
  org's current GitHub plan.
- `soc-agent` and `.github` do not yet have substantive PR test workflows.
  Add status-only protection when such checks exist and have reported green on
  `main`.

## Why these settings

- **No required reviews**: a green, current required-check set is the merge
  authorization. This lets agents merge without a human approval while
  preserving test enforcement. Human review remains available when requested
  or when a normal review comment needs resolution.
- **Strict checks**: a PR branch must be current with `main`, preventing an
  agent from merging against stale green results.
- **No force pushes or branch deletion**: bypassing the normal merge path is
  still prohibited.
- **`enforce_admins` off**: administrators retain a recovery path for a broken
  required workflow. It is an emergency control, not the agent merge path.
- **CODEOWNERS is advisory**: `@AS215932/ops` continues to document ownership,
  but `require_code_owner_reviews` is intentionally off.

## The `iac-gate` deadlock guard (acceptance #7)

`network-operations` requires **`iac-gate`**, not the individual Tier-0 jobs.
`iac-tests.yml` is not workflow-level path-filtered: GitHub does not create
check runs for a workflow skipped by `paths`, so a required context from that
workflow can remain stuck at "Expected" on a docs-only PR. Instead, the workflow
always starts, an internal `changes` job decides whether IaC paths changed, and
the `iac-gate` job reports either way. Always re-verify after changing required
checks with a docs-only PR that touches none of the IaC paths: `iac-tests` must
run, the tier jobs should be skipped, and `iac-gate` must report success.

`iac-gate` itself (`if: always()`, `needs:` the internal `changes` job plus all
tiers) passes only when change detection succeeds, required tiers succeed for
IaC changes, and the trusted lab tiers are success-or-skipped — see
`docs/netops/testing-strategy.md`.

## Reproducing the protection

```bash
# Apply a status-only rule after every named context has reported green:
gh api -X PUT repos/AS215932/<repo>/branches/main/protection --input - <<'EOF'
{ "required_status_checks": {"strict": true, "contexts": [...]},
  "enforce_admins": false, "required_pull_request_reviews": null,
  "restrictions": null, "required_linear_history": false,
  "allow_force_pushes": false, "allow_deletions": false,
  "block_creations": false, "required_conversation_resolution": false,
  "lock_branch": false, "allow_fork_syncing": false }
EOF

# Add a context to an already-protected repo without disturbing the rest:
gh api -X POST repos/AS215932/network-operations/branches/main/protection/required_status_checks/contexts \
  -f 'contexts[]=iac-gate' -f 'contexts[]=semgrep'
```

## Verification

Always read back the rule after mutation and confirm the required check names,
`strict: true`, a null review rule, and disabled force pushes/deletions:

```bash
gh api repos/AS215932/<repo>/branches/main/protection
```

A docs-only PR in `network-operations` must still produce `iac-gate` success
while its internal IaC tiers skip; this guards against an Expected-check
deadlock. A status-only rule is not permission to merge red or pending work.

## admin:org token — revoke when done

The org-level changes (runner groups, `OPENROUTER_API_KEY` secret, `ops` team,
Sourcery removal, these protection edits) are the *only* steady-state need for
`admin:org`. Once branch protection is settled, downscope the token:

```bash
gh auth refresh -h github.com --reset-scopes   # resets to repo,read:org,gist,workflow
```

(or revoke the "GitHub CLI" OAuth grant in github.com → Settings → Applications
and re-login with minimal scopes).
