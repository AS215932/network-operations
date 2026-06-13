# Engineering Loop extraction runbook (Phase G)

How the Hyrule Engineering Loop moves from `AS215932/network-operations` into
its own repo, `AS215932/engineering-loop`, with history preserved. This is the
final phase of the v2 refactor (`docs/engineering-loop/v2-roadmap.md` § G,
issue #196). It is deliberately operator-gated: the loop's backend executes
generated code, so the new repo must land on the **unprivileged** runner, and
the destructive removal from network-operations must not merge until the new
repo is proven working.

## Why a new repo, why now

Decision of record (design PR #190): v2 ultimately lives in a dedicated repo.
Extraction goes last because it is cheap once the shape is stable and expensive
churn mid-refactor. Phases B–F are merged; the shape is stable.

## What moves (history preserved)

`scripts/extract-engineering-loop.sh` keeps exactly these paths, with the full
history of every commit that touched them:

- `src/hyrule_engineering_loop/`
- `tests/test_engineering_graph.py`, `tests/test_phase*.py` (NOT `tests/iac/`
  or `tests/goss/` — those stay)
- `docs/agent-loops/`, `docs/agentic-development-loop.md`,
  `docs/engineering-loop/`
- `skills/`, `integrations/pi/`, `configs/loop/`
- `model-policy.yml`, `engineering-loop-policy.yml`
- `pyproject.toml`, `uv.lock`

The extraction tooling itself (`scripts/extract-engineering-loop.sh`,
`scripts/cutover-remove-loop.sh`, this runbook, `integrations/engineering-loop/`
scaffolding) **stays** in network-operations for provenance.

> The roadmap says "git subtree split"; the script uses `git filter-repo`
> instead because the loop spans many top-level paths and a subset of `tests/`,
> which subtree split cannot select. filter-repo is the modern tool for
> multi-path history extraction and satisfies AC1 (history preserved).

## Prerequisites

- `git filter-repo` (`uv tool install git-filter-repo`, or `uvx git-filter-repo`
  works without installing — the script falls back to it).
- `gh` authenticated to the `AS215932` org with repo-create rights.
- Org admin access to assign the new repo to a runner group.

## Step 1 — extract and verify locally (safe, repeatable)

```bash
scripts/extract-engineering-loop.sh \
  --source /home/svag/Dev/hyrule-infra \
  --ref <ref-with-the-full-loop> \
  --target /tmp/engineering-loop-export \
  --verify
```

`--verify` runs `pytest` + `mypy --strict` in the extracted tree. Also confirm
`uvx ruff check src tests` is clean and `git log --follow
src/hyrule_engineering_loop/graph.py` shows pre-extraction history (AC1).
Re-run freely — it only ever touches a scratch clone and the target dir.

## Step 2 — create the repo and push (operator, irreversible)

```bash
gh repo create AS215932/engineering-loop --private \
  --source /tmp/engineering-loop-export --remote origin
GIT_SSH_COMMAND="ssh -i ~/.ssh/id_servify -o IdentitiesOnly=yes" \
  git -C /tmp/engineering-loop-export push -u origin HEAD:main
```

## Step 3 — runner group and branch protection (AC2)

- Add `engineering-loop` to the **`public-pr`** runner group (label
  `hyrule-public-pr`). Do **NOT** add it to `hyrule-ci` or the privileged
  `hyrule`/`hyrule-infra` runner — see `docs/ci/security-model.md`.
- Protect `main`, requiring the three checks from `.github/workflows/ci.yml`:
  `ruff`, `mypy`, `pytest`. Confirm the first CI run is green on `ci-pr`.

## Step 4 — repoint the Pi extension (in the new repo)

The Pi extension moved with the extraction. In the new repo, update
`integrations/pi/extensions/hyrule-loop/`:

- the install/sync instructions reference `AS215932/engineering-loop`;
- `index.ts` runs `uv run hyrule-engineering-loop` with `cwd` = the
  engineering-loop checkout (the loop CLI/package now lives there), while the
  `--memory-dir` and repo-autodetect roots still point at the
  `network-operations` (a.k.a. `hyrule-infra`) checkout, which is where
  `memory/` for infra work and the sibling `hyrule-*` repos live. These two
  roots diverge after extraction — split the single `infraRepo` config field
  accordingly. Re-sync the installed copy under `~/.pi`.

## Step 5 — sibling-canary from the new repo (AC4)

From the engineering-loop checkout:

```bash
uv run hyrule-engineering-loop sibling-canary \
  --workspace-root /home/svag/Dev \
  --repo-name hyrule-cloud \
  --output-root /tmp/eng-loop-canary
```

It must complete end-to-end (repo adapter → policy → promotion → handoff) and
stop before approval. This proves the loop still drives a sibling repo from its
new home.

## Step 6 — cut over network-operations (AC3, only after 1–5 pass)

```bash
scripts/cutover-remove-loop.sh --dry-run   # review what will be removed
scripts/cutover-remove-loop.sh             # git rm + pointer doc
cd ansible && ansible-playbook playbooks/firewall.yml --tags validate \
  --connection=local --skip-tags snapshot   # sanity: render still works
```

Then commit and open the cutover PR. Confirm network-operations CI stays green
— `lint.yml` (yamllint/ansible-lint/shellcheck/jinja-syntax), `render-check`,
and `iac-tests` are all independent of the loop package (nothing in
network-operations imports `hyrule_engineering_loop`). Merge the cutover PR only
after the new repo's CI is green and Step 5 passed.

## Step 7 — finalize the inventory

Update `docs/ci/org-cicd-inventory.md`: flip the `engineering-loop` row from
"pending extraction" to live, with its required checks and `public-pr` runner
group assignment recorded.

## Rollback

Before Step 6 merges, rollback is trivial — the new repo is additive and
network-operations is untouched. After Step 6, restoring the loop into
network-operations means reverting the cutover commit (the code is intact in
git history); prefer fixing forward in the new repo instead.
