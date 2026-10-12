# Semgrep (token-less SAST)

Semgrep is the real security gate (PR-Agent is advisory). It runs token-less —
no `SEMGREP_APP_TOKEN`, no dashboard — and uploads SARIF to GitHub Code Scanning
(free for public repos).

- Image: `semgrep/semgrep@sha256:bc8b15e245d7bd392bcadce7ef4db36601b375fab35bfd8070ed8ae3d7824c74`, pinned by digest, run via `docker run` (not a job `container:`, so the Node-based upload-sarif action runs in the normal runner env).
- Upload: `github/codeql-action/upload-sarif@84498526a009a99c875e83ef4821a8ba52de7c22`.
- Runner: `hyrule-public-pr` (unprivileged).
- Triggers: every PR, `workflow_dispatch`, push on workflow/rule changes, nightly cron.

## Reporting-only baseline → gate

The `Run Semgrep` step is `continue-on-error: true` during the baseline, so
findings land in the Security tab without failing the job. The `semgrep` job
itself therefore (almost) always reports success — which is why it is safe to
require as a status check now: it guarantees the scan *runs* on every PR.

To turn Semgrep into a true blocking gate once the baseline is triaged: drop
`continue-on-error` from the scan step (or use `semgrep ci --error`). Do this
per repo only after the existing findings are reviewed — don't block on
historical findings. Recommended progression: baseline (reporting-only) → block
new high-severity → high-confidence medium+ on the security-sensitive repos
(`engineering-loop`, `hyrule-cloud`, `noc-agent`, `hyrule-mcp`).

## Packs

Curated `p/` rulesets per stack (`p/ci`, `p/github-actions`, `p/secrets`, plus
`p/python` / `p/javascript` / `p/typescript` as relevant). The Actions rules are
the security-critical ones for this org: they flag `pull_request_target`,
`permissions: write-all`, unpinned third-party actions, `curl | sh`, secrets
echoed to logs, and self-hosted-runner use on PR workflows.

## Fork note

Code Scanning SARIF upload may be restricted on external fork PRs (no
`security-events: write`). For fork PRs, surface findings via the job log /
`$GITHUB_STEP_SUMMARY` so they remain visible without org secrets. The upload
step is `if: always() && hashFiles('semgrep.sarif') != ''` + `continue-on-error`
so a restricted upload never fails the job.

## Triage

- **SARIF not in Security tab** → check the upload step ran and `security-events:
  write` is granted; on forks this is expected to be unavailable (see above).
- **Job red unexpectedly** → during baseline the scan step can't fail the job;
  a red `semgrep` job means the *upload* or Docker run errored, not a finding.
