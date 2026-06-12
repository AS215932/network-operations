# Hyrule Loop Pi Extension

This directory contains the repo-owned source for the Pi `/loop` extension that
fronts the Hyrule Engineering Loop.

The extension is intentionally thin:

- turns a prompt or Plan Mode handoff into a request markdown file;
- calls `uv run hyrule-engineering-loop feature ...` from this repo;
- records state, trace, NOC handoff, model, diff, sign-off, and failure
  summaries in the Pi session;
- provides `/loop status`, `/loop trace`, `/loop cleanup`, and `/loop approve`
  helpers for the latest run.

## Install / Sync

Until Pi has a packaged extension installer for this repo, sync the source into
the local Pi extension directory:

```bash
mkdir -p ~/.pi/agent/extensions/hyrule-loop
cp integrations/pi/extensions/hyrule-loop/index.ts \
  ~/.pi/agent/extensions/hyrule-loop/index.ts
```

Run Pi from a `hyrule-*` checkout and invoke:

```text
/loop Add X in the order flow
/loop --repo hyrule-web Add X in the order flow
/loop --plan
/loop status
/loop trace
```

## Configuration

The extension currently autodetects the active `hyrule-*` repo from the current
working directory and falls back to `hyrule-cloud`.

Default paths are defined in `index.ts`:

```text
workspaceRoot: /home/svag/Dev
infraRepo: /home/svag/Dev/hyrule-infra
outputRoot: /tmp/hyrule-loop
defaultRepo: hyrule-cloud
defaultAllow: docs
defaultSources: README.md
```

A project-local override is still supported at `.pi/hyrule-loop.json`, but the
preferred default is autodetection rather than one config file per repo.

## Source of Truth

Treat this directory as the canonical source. The copy under `~/.pi` is an
installed working copy and should be refreshed from here after changes.
