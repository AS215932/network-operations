# Hyrule Repo Map

## hyrule-infra

- Purpose: AS215932 infrastructure, Ansible, network configuration, CI/deploy
  workflows, and operating docs.
- Common classes: `infra_ansible`, `routing_bgp_frr`, `firewall_policy`, `dns`,
  `vault_secret_plane`, `monitoring_logging`, `mixed`.
- Source of truth: `AGENTS.md`, `docs/network-flows.md`, `docs/architecture.md`,
  Ansible inventory, CI workflow docs.
- Gates: `git diff --check` for docs; Ansible validate and existing CI workflows
  for config/infra changes.

## hyrule-cloud

- Purpose: Hyrule Cloud API, VPS provisioning, x402/payment flows, quota and
  metering behavior.
- Common classes: `cloud_api`, `app_feature`, `app_bugfix`, `mixed`.
- Required roles: Systems, DevOps/NetOps, FinOps for billing/provisioning
  paths, Security for tenant isolation or secret handling.
- Gates: pytest, ruff, mypy.

## hyrule-web

- Purpose: customer-facing web frontend for Hyrule Cloud.
- Common classes: `frontend`, `app_feature`, `app_bugfix`.
- Required roles: Systems and DevOps/NetOps; FinOps when pricing/payment UI
  affects state semantics.
- Gates: pytest and `npm run check`.

## hyrule-mcp

- Purpose: diagnostic MCP server exposing AS215932 infrastructure tools.
- Common classes: `mcp_diagnostic_tooling`, `monitoring_logging`, `mixed`.
- Required roles: Systems and DevOps/NetOps; Security when output may expose
  secrets, tenant data, or privileged diagnostics.
- Gates: pytest; live smoke opt-in only.

## hyrule-noc-agent

- Purpose: production NOC/SOC runtime incident responder.
- Common classes: `noc_runtime`, `mcp_diagnostic_tooling`,
  `monitoring_logging`.
- Required roles: Systems, DevOps/NetOps, Security.
- Boundary: this repo can receive normal PRs, but it is not the development
  orchestrator and must not spawn coding agents.
- Gates: pytest; live smoke opt-in only.

## hyrule-business

- Purpose: business analysis and planning documents.
- Common classes: documentation-only support for `mixed` or product planning.
- Gates: `git diff --check`.
