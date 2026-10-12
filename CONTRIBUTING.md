# Contributing to AS215932 network-operations

Thanks for your interest in Hyrule Networks (AS215932). This repository is both
a public record of ISP operations and a place for community contribution.

## Ways to contribute

1. **Peering requests** — Use the `peering` label and the peering issue template.
2. **Bug reports** — Routing anomalies, documentation errors, or broken automation.
3. **Configuration improvements** — Reusable FRRouting, WireGuard, or monitoring templates.
4. **Documentation** — Architecture notes, runbook clarifications, or tutorial sections.
5. **Questions** — Open a discussion issue if something about the network is unclear.

## Peering requests

Before opening a peering request, please ensure your network meets our policy:

- Valid PeeringDB entry for your ASN
- IRR objects registered in the RIPE Database
- 24/7 NOC contact
- RPKI ROA configured for your prefixes

Open an issue with the `peering` label and include:

- Your ASN
- IXP location(s) where you want to peer
- PeeringDB link
- Contact email

## Style guide

- Keep configuration examples as generic and reusable as possible.
- Use Jinja2 variables rather than hard-coded values where applicable.
- Prefer shell scripts that fail fast (`set -euo pipefail`).
- Document new playbooks or scripts with a short header comment.
- Follow the existing 80-column soft target for documentation.

## Repository structure conventions

```
configs/     # Jinja2 templates and generated router configs
docs/        # Architecture, runbooks, and peering docs
scripts/     # Bootstrap and operational helpers
autoinstall/ # OS autoinstall and QMP tooling
.github/     # Issue templates and CI workflows
```

## Automation & CI

- App repos do **not** deploy production on merge.
- After an app repo CI succeeds on `main`, its promotion workflow opens/updates
  a promotion PR in this repository.
- The human operator reviews, merges, and approves the GitHub environment gate.
- Do not commit secrets; the repository is public and CC0-licensed.

## License

By contributing, you agree that your contributions are released under the same
terms as the repository (CC0 / public domain unless otherwise noted).

## Code of conduct

Be respectful, constructive, and patient. ISP operations can be slow and
conservative for good reason. We value safety over speed.
