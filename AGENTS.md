# AS215932 Infrastructure Agent Guide

## Branch Hygiene - Read Before Editing or Pushing

- Before making changes, run `git branch --show-current` and `git status --short`.
- Unless the user explicitly says to continue work on the current branch, create a fresh task branch from up-to-date `main` before editing:
  - `git fetch origin main`
  - `git switch -c <type>/<short-task-name> origin/main`
- Never add unrelated work to an existing feature branch or PR just because it is currently checked out.
- If a task is about CI/CD, docs, runner config, or general maintenance, it almost always needs its own branch from `main`; do not build it on a feature branch such as NETCONF/YANG, app promotion, or routing changes.
- Before committing and before pushing, re-check `git status --short`, `git branch --show-current`, and `gh pr list --head "$(git branch --show-current)"` to confirm the branch matches the task.
- If you discover changes were made on the wrong branch, stop and split them onto a new branch from `main` before pushing.

## Pull Request Hygiene - Do Not Hand Off Red PRs

- After opening or updating a PR, wait for automated CI and AI agent reviews to complete before leaving it for a human reviewer.
- Inspect failing checks, AI review comments, and normal review comments; fix real issues in follow-up commits on the same PR branch.
- Respond to review comments that you address, and briefly explain if a comment is intentionally not changed.
- Re-run or wait for CI after fixes, then confirm the required checks are green before asking for human review or saying the PR is ready.
- If CI or an AI review is still pending when you must stop, say so explicitly and include the PR URL plus the pending/failing contexts.

## Deployment Rules - Read Before Touching App Pins

- Production deploys for `noc-agent`, `hyrule-mcp`, `hyrule-cloud`,
  `hyrule-web`, `hyrule-network-proxy`, and `hyrule-seo-agent` are controlled
  from this repository, not from app repos.
- App repositories may merge code, but they must not be treated as production
  deployment records.
- Production app versions are the pinned 40-character SHAs in:
  - `ansible/inventory/host_vars/noc.yml`
  - `ansible/inventory/host_vars/api.yml`
  - `ansible/inventory/host_vars/web.yml`
  - `ansible/inventory/host_vars/netproxy.yml`
  - `ansible/inventory/host_vars/loop.yml`
- Normal promotion path:
  1. Merge app PRs after app CI is green.
  2. Run the `promote-apps` workflow in this repo with the merged app SHAs.
  3. Review and merge the generated promotion PR.
  4. Let `app-promotion-deploy` start automatically from `main`.
  5. The only intended manual step is approving the `production` environment
     gate before `apply.yml` touches live hosts.
- Do not manually edit app pins unless the automation is unavailable. If you
  must, use the promotion PR template and keep rollback SHAs in the PR body.
- `apply.yml` runs only when explicitly dispatched or called by another
  workflow. After this automation, app pin changes merged to `main` cause
  `app-promotion-deploy` to call `apply.yml` automatically.

## Domain Policy

- `hyrule.host` is customer-facing Hyrule Cloud/product identity. Use it for the product site, public Hyrule Cloud API, and customer VM subdomains.
- `servify.network` is infrastructure identity for nameservers, underlay and management references, provider relationships, internal UIs, and partner-facing hostnames.
- `as215932.net` is AS215932 overlay/routing identity only. DNS records in this zone must point only at prefixes owned by AS215932.

Do not blindly replace `servify.network`: nameservers, monitoring, Xen Orchestra, router hostnames, reverse DNS, Openprovider examples, and partner-facing infrastructure references are intentionally kept there.
