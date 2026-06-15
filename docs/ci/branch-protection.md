# Branch protection — required checks per repo

Current `main` protection across the org (set in Wave 6). PR-Agent is **never**
required (advisory, same-repo-only — requiring it would wedge fork/dependabot
PRs). Semgrep is required as a *presence* gate while reporting-only; flip it to
a blocking gate per `docs/ci/semgrep.md` once each repo's baseline is triaged.

| Repo | Required checks | strict | reviews | enforce_admins |
|------|-----------------|:------:|:-------:|:--------------:|
| `network-operations` | `lint, render, iac-gate, semgrep` | ✓ | 1 | off |
| `hyrule-web` | `test, frontend` | ✓ | 1 | off |
| `hyrule-cloud` | `test, semgrep` | ✓ | 0 | off |
| `noc-agent` | `semgrep` | ✓ | 0 | off |
| `hyrule-mcp` | `semgrep` | ✓ | 0 | off |
| `as215932.net` | `semgrep` | ✓ | 0 | off |

`Sourcery review` was removed from `network-operations` **before** the
`sourcery-ai` app was uninstalled (no window where merges blocked on a check
that could never report). The app is gone (`gh api orgs/AS215932/installations`
lists only `claude-for-github`, `claude`).

## Why these settings

- **No required reviews on the four newly-protected repos** (and `enforce_admins`
  off everywhere): this is a solo-maintainer org. Requiring an approval with no
  second maintainer forces an `--admin` bypass on every merge (self-approval is
  forbidden) and risks a merge lockout. `network-operations`/`hyrule-web` keep
  their pre-existing 1-review rule; the rest protect via status checks + no
  force-push/deletion. Tighten later when there's a second reviewer. The
  `@AS215932/ops` team exists and backs `.github/CODEOWNERS`, but
  `require_code_owner_reviews` is intentionally left **off** for now.
- **`semgrep`-only on `noc-agent`/`hyrule-mcp`/`as215932.net`**: those repos have
  no test/lint workflow yet — `semgrep` is the only existing green check. Adding
  a real `ruff`+`pytest` gate (uv-managed 3.14, deselect `test_live_smoke.py`)
  is tracked as follow-up, after which it should be added as required.

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
# Four repos protected on existing green contexts (no required reviews):
gh api -X PUT repos/AS215932/<repo>/branches/main/protection --input - <<'EOF'
{ "required_status_checks": {"strict": true, "contexts": [...]},
  "enforce_admins": false, "required_pull_request_reviews": null,
  "restrictions": null, "allow_force_pushes": false, "allow_deletions": false }
EOF

# Add a context to an already-protected repo without disturbing the rest:
gh api -X POST repos/AS215932/network-operations/branches/main/protection/required_status_checks/contexts \
  -f 'contexts[]=iac-gate' -f 'contexts[]=semgrep'
```

## admin:org token — revoke when done

The org-level changes (runner groups, `OPENROUTER_API_KEY` secret, `ops` team,
Sourcery removal, these protection edits) are the *only* steady-state need for
`admin:org`. Once branch protection is settled, downscope the token:

```bash
gh auth refresh -h github.com --reset-scopes   # resets to repo,read:org,gist,workflow
```

(or revoke the "GitHub CLI" OAuth grant in github.com → Settings → Applications
and re-login with minimal scopes).
