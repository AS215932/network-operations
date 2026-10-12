# Knowledge nightly-PR auto-merge — dormant capability

`AS215932/knowledge` ships `.github/workflows/auto-merge.yml`: when the repo
variable `KNOWLEDGE_AUTO_MERGE` is `on`, nightly bot refresh PRs
(`bot/knowledge-refresh/*`, `bot/knowledge-loop/*`) queue for auto-merge after
two workflow-level guards, with branch protection's required checks remaining
the actual merge gate (`gh pr merge --auto --squash`).

Current policy is `KNOWLEDGE_AUTO_MERGE=dry_run`, and native GitHub auto-merge
is disabled. Agents may normally merge any Knowledge PR after the strict
`validate` context is green, but this nightly workflow must not queue merges.

## What can and cannot auto-merge

- **Allowed diff surface**: `okf/generated/**`, `okf/observed/**`,
  `exports/**`, `reports/**` — deterministic projections of source repos.
- **Never auto-merges**: anything touching `okf/curated/**` (human knowledge),
  `ledger/**` (the production insight/learning stream stays human-reviewed),
  `src/**`, `tests/**`, `evals/**`, `schema/**`, `.github/**`.
- **Quality non-regression**: head `critical_count == 0`, `warning_count` not
  above base, `concept_count ≥ 80%` of base (mass-deletion guard), compared
  against the PR base's `reports/coverage.json`.

## Current operation

1. Branch protection on `main` requires the aggregate `validate` context.
   That job contains ruff, mypy, pytest, OKF validation, quality, export, eval,
   ledger, lifecycle, and secret-scan gates.
2. Keep dry-run enabled:
   `gh variable set KNOWLEDGE_AUTO_MERGE --repo AS215932/knowledge --body dry_run`.
   Qualifying nightly PRs may receive a "would auto-merge" comment.
3. An agent can merge through the normal PR action after `validate` succeeds
   and review feedback is resolved.
4. Setting the variable to `on` requires a separate policy decision and
   enabling native GitHub auto-merge. The immediate kill switch is `--body off`
   (or delete the variable); cancel queued merges with
   `gh pr merge --disable-auto <n>`.

Note: the workflow triggers on `pull_request`; PRs created with the default
`GITHUB_TOKEN` do not trigger downstream workflows, so the nightly jobs must
keep using `KNOWLEDGE_GH_TOKEN` (already the configured path in `ingest.yml`).
