# Hyrule Engineering Loop — moved

The Hyrule Engineering Loop now lives in its own repository:
**[AS215932/engineering-loop](https://github.com/AS215932/engineering-loop)**.

This includes the LangGraph runtime, the `AgentBackend`, the senior-role
skills, the task-spec / two-phase-review / memory / intake / operations-lane
machinery, the Pi `/loop` extension, and the design docs (`docs/engineering-loop/`,
`docs/agent-loops/`, and the runtime reference formerly at this path).

History was preserved via `git filter-repo` (Phase G of the v2 refactor; see
that repo and `AS215932/network-operations#196`). The extraction tooling that
produced it remains here at `scripts/extract-engineering-loop.sh` and
`docs/ci/engineering-loop-extraction.md` for provenance.

Nothing in network-operations imports the loop; it operates on this repo (and
the other `hyrule-*` repos) from the outside, opening draft PRs that humans
review and merge.
