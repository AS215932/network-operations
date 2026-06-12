---
name: implementation-tranche
description: Working contract for the implementation backend producing one engineering-loop tranche inside a guarded worktree.
triggers: [every implementation run]
---

# Implementation tranche

You are producing the smallest useful tranche for the active task spec, in a
branch-backed worktree. The diff is the deliverable; evidence makes it real.

## Workflow

1. **Read the contract.** Open the task spec (`tasks/<change-id>.md`). Note
   `allowed_paths`, acceptance criteria, non-goals, and budget.
   *Checkpoint: restate the acceptance criteria in your plan before editing.*
2. **Read the lessons.** Open `memory/lessons/<repo>.md` if present. Treat
   every entry as a hard constraint.
3. **Explore before editing.** Find the existing pattern for what you're
   about to write (similar module, similar test, similar config block).
   Reuse it; do not invent a parallel convention.
4. **Implement inside `allowed_paths` only.** Touch only what the spec asks
   you to touch. If the right fix lies outside the allowlist, stop and
   report that instead of working around it.
5. **Run the gates yourself after each meaningful edit.** The spec lists
   them; default to the repo's gates in
   `docs/agent-loops/acceptance-gates.md`.
   *Checkpoint: paste the failing output you fixed, not just the final pass.*
6. **Self-review the diff** (`git diff`): no secrets, no commented-out
   code, no test deletions or skips, no drive-by refactors, complete-but-
   minimal.
7. **Report.** Summarize what changed, evidence per acceptance criterion,
   and anything you could not satisfy and why.

## Anti-rationalization

| Excuse | Rebuttal |
|---|---|
| "This task is too small for the spec's criteria" | Criteria still apply; satisfying five lines of criteria is fast. |
| "The test is wrong, I'll delete/skip it" | Never. Removing or skipping tests fails the run. Fix code or report the conflict. |
| "I need to touch a file outside allowed_paths, it's just one line" | Out of scope is out of scope. Report it; the spec gets amended by a human. |
| "Gates pass, ship it" | Gates are evidence, not proof. Walk the acceptance criteria one by one. |
| "A bigger refactor would be cleaner" | Smallest useful tranche. File the refactor as a finding instead. |

## Exit criteria

- Every acceptance criterion has named evidence (gate output, test, or diff
  hunk).
- All spec gates pass in the worktree.
- `git diff --stat` touches only `allowed_paths`.
- The report distinguishes "done with evidence" from "not done, because".
