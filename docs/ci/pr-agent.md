# PR-Agent (advisory AI review)

PR-Agent replaces the hosted Sourcery app with a self-controlled reviewer that
uses **our own OpenRouter key**. It is advisory only — read + PR/issue-comment
permissions, never deploys, never writes code, never auto-merges, and is **not**
a required check.

- Action: `The-PR-Agent/pr-agent@0bd56c0508504c718cc03d504cd4ceb6725ba3c7` (v0.35.0, Docker-based), SHA-pinned.
- Runner: `hyrule-public-pr` (unprivileged `ci-pr`) only.
- Model: primary `openrouter/deepseek/deepseek-v4-flash`, fallback `openrouter/minimax/minimax-m2.7`.
- Key: `OPENROUTER_API_KEY` org secret (visibility = selected public repos, including `engineering-loop`), delivered per-job as `OPENROUTER__KEY`.
- Config: `.pr_agent.toml` per repo (model, fallback, `extra_instructions`).

## The model-pin gotcha (important)

PR-Agent reads `.pr_agent.toml` from the **default branch**, not the PR head
(its action has no checkout step; "Applying repo settings" loads from the
default branch). So until a new repo's `.pr_agent.toml` is merged to `main`,
PR-Agent silently falls back to its packaged default (`gpt-5.5…` → `gpt-5.4-mini`)
and the gpt-5.5 call errors "not a valid model ID".

Fix = pin via dynaconf env in `pr-agent.yml` `env:` (double-underscore, same path
that delivers `OPENROUTER__KEY`), which is honoured immediately:

```yaml
CONFIG__MODEL: openrouter/deepseek/deepseek-v4-flash
CONFIG__FALLBACK_MODELS: '["openrouter/minimax/minimax-m2.7"]'
CONFIG__CUSTOM_MODEL_MAX_TOKENS: "128000"
```

Both models are *custom* (litellm) models → `custom_model_max_tokens` is
required. Keep the env pin even after `.pr_agent.toml` lands (belt and braces).

## Fork / trust policy

```yaml
if: >
  (github.event_name == 'pull_request' &&
   github.event.pull_request.head.repo.full_name == github.repository) ||
  (github.event_name == 'issue_comment' &&
   github.event.issue.pull_request &&
   startsWith(github.event.comment.body, '/') &&
   contains(fromJSON('["OWNER","MEMBER","COLLABORATOR"]'), github.event.comment.author_association))
```

Auto-review only for same-repo PRs; slash commands (`/review`, `/improve`,
`/ask`) only from trusted authors. This keeps `OPENROUTER_API_KEY` off fork PRs
and stops random users burning OpenRouter budget. Auto-review fires on
`opened`/`reopened`/`ready_for_review` — to re-run on an existing PR, close and
reopen it (a bare `synchronize` push is intentionally skipped).

## Triage

- **Wrong model / "not a valid model ID"** → the env pin is missing or
  `.pr_agent.toml` isn't on the default branch. Check the run's "Applying repo
  settings" log; confirm `CONFIG__MODEL` is in `env:`.
- **No comment posted** → check the `if:` (fork PR? untrusted commenter?) and
  that the job ran on `hyrule-public-pr`.
- **OpenRouter spend / noise** → it's advisory; tune `.pr_agent.toml`
  `extra_instructions`, never make it a required check.
