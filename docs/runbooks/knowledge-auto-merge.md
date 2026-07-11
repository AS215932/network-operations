# Knowledge nightly-PR auto-merge — rollout and guardrails

`AS215932/knowledge` ships `.github/workflows/auto-merge.yml`: when the repo
variable `KNOWLEDGE_AUTO_MERGE` is `on`, nightly bot refresh PRs
(`bot/knowledge-refresh/*`, `bot/knowledge-loop/*`) queue for auto-merge after
two workflow-level guards, with branch protection's required checks remaining
the actual merge gate (`gh pr merge --auto --squash`).

## What can and cannot auto-merge

- **Allowed diff surface**: `okf/generated/**`, `okf/observed/**`,
  `exports/**`, `reports/**` — deterministic projections of source repos.
- **Never auto-merges**: anything touching `okf/curated/**` (human knowledge),
  `ledger/**` (the production insight/learning stream stays human-reviewed),
  `src/**`, `tests/**`, `evals/**`, `schema/**`, `.github/**`.
- **Quality non-regression**: head `critical_count == 0`, `warning_count` not
  above base, `concept_count ≥ 80%` of base (mass-deletion guard), compared
  against the PR base's `reports/coverage.json`.

## Rollout

1. Confirm branch protection on `main` requires the full `validate` check set
   (ruff, mypy, pytest, validate okf, quality --check, export --check,
   eval --check, ledger --check, lifecycle --check, scan-secrets).
2. Set the repo variable to dry-run and burn in for a week:
   `gh variable set KNOWLEDGE_AUTO_MERGE --repo AS215932/knowledge --body dry_run`
   Each qualifying nightly PR gets a "would auto-merge" comment; keep merging
   by hand and confirm every comment matches your own judgement.
3. Flip live: `gh variable set KNOWLEDGE_AUTO_MERGE --repo AS215932/knowledge --body on`
4. Kill switch: `--body off` (or delete the variable). In-flight queued merges
   can be cancelled with `gh pr merge --disable-auto <n>`.

Note: the workflow triggers on `pull_request`; PRs created with the default
`GITHUB_TOKEN` do not trigger downstream workflows, so the nightly jobs must
keep using `KNOWLEDGE_GH_TOKEN` (already the configured path in `ingest.yml`).
