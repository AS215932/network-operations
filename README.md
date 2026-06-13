# AS215932 Network Operations

## Production Deploys: Read This First

This repository is the production deployment record for `noc-agent`,
`hyrule-mcp`, `hyrule-cloud`, and `hyrule-web`. App repos do not deploy
production on merge.

After an app repo's `ci` workflow succeeds on `main`, its
**request-promotion** workflow asks this repo to open or update the promotion PR
that pins exact SHAs in inventory. **Actions -> promote-apps** remains the
manual fallback when a promotion request needs to be replayed or coordinated by
hand. After the promotion PR merges, **app-promotion-deploy** automatically
calls `apply.yml` for the affected playbooks and waits at the GitHub
`production` environment approval gate. The human operator's normal job is to
review the promotion PR, merge it, approve the production gate, and review the
Icinga snapshot diff.

Full runbook: [docs/ci/deploy-runbook.md](docs/ci/deploy-runbook.md).

Public operations repository for **Hyrule Networks (AS215932)** — building a complete Internet Service Provider from scratch.

## About

AS215932 is a solo project to build and operate a full-stack ISP with modern BGP routing, multi-homing, and IPv6-first architecture. This repository tracks infrastructure work, configuration management, and operational decisions.

**Working in public** to share knowledge with the networking community and demonstrate real-world ISP operations.

## Network Status

### Current Infrastructure

- **ASN**: AS215932
- **Network Name**: Hyrule / Servify
- **NOC**: noc@as215932.net
- **Peering Policy**: Open (see PeeringDB)
- **PeeringDB**: [AS215932](https://www.peeringdb.com/asn/215932)

### Upstream Connectivity

- Multiple BGP transit providers
- Presence at multiple Internet Exchange Points (IXPs)
- IPv6-only (for now, hopefully forever unless I can't avoid it)
- RPKI ROA configured
- IRR objects registered (RIPE Database)

## Domain Policy

`AGENTS.md` is the canonical domain-policy reference for this repo. In short:
`hyrule.host` is customer-facing Hyrule Cloud identity, `servify.network` is
infrastructure identity, and `as215932.net` is AS215932 overlay/routing identity
only.

### Internet Exchange Points

*Active IXP presence at multiple locations - see [PeeringDB](https://www.peeringdb.com/asn/215932) for current list*

## Technical Stack

### Routing & Network

- **Core Routers**: FreeBSD + FRRouting (`cr1-nl1`, `cr1-de1`, `cr1-ch1`)
- **Edge Router**: Debian 13 + FRRouting (`rtr` at OVH)
- **Overlay Network**: full-mesh WireGuard tunnels
- **Protocols**: BGP, OSPFv3
- **Virtualization**: XCP-NG w/ Xen Orchestra

### Architecture Highlights

```text
cr1-nl1 ===== cr1-de1
   |  \\       /  |
   |   \\     /   |
   |    cr1-ch1  |
   |      |      |
   +----- rtr ---+
```

- IPv6-first design with IPv4 transition mechanisms (NAT64/DNS64, 464XLAT)
- Multi-homed BGP (with ECMP load balancing)
- Stateless overlay for asymmetric routing
- Distributed routing architecture with OSPF internal connectivity

## Architecture diagrams

> Place rendered diagrams in `docs/img/` and embed them here.
> Suggested file names and contents:

### Physical & logical topology

![Network topology](docs/img/topology.png)

*Full mesh of core routers (`cr1-nl1`, `cr1-de1`, `cr1-ch1`) with OSPFv3 internal
connectivity and the edge router (`rtr`) at OVH. BGP multi-homing provides transit
and IXP peering; WireGuard overlays stitch the stateless fabric together.*

### BGP & peering overview

![BGP overview](docs/img/bgp-overview.png)

*External BGP sessions to transit providers and IXPs; internal BGP policies for
route filtering, RPKI validation, and ECMP load balancing.*

### Deployment flow

![Deployment flow](docs/img/deploy-flow.png)

*App repositories (`noc-agent`, `hyrule-mcp`, `hyrule-cloud`, `hyrule-web`) run
CI on `main`, then request promotion via this repo. A promotion PR pins exact
SHAs in inventory; after merge, `apply.yml` deploys through a GitHub environment
approval gate.*

### How to regenerate

Diagrams are authored as text (Mermaid, DOT/Graphviz, or Draw.io source) so they
stay version-controlled. Sources live in `docs/diagrams/`.

```bash
# Example: render a Mermaid diagram to PNG
npx -y @mermaid-js/mermaid-cli mmdc -i docs/diagrams/topology.mmd -o docs/img/topology.png
```

## Screenshots

> Add production screenshots to `docs/img/screenshots/` and update the table
> below. Keep sensitive data (passwords, keys, full IP ranges) out of frame.

| Screenshot | Description |
|------------|-------------|
| ![Icinga dashboard](docs/img/screenshots/icinga-dashboard.png) | Icinga monitoring overview for core routers and services |
| ![FRRouting CLI](docs/img/screenshots/frr-cli.png) | Sample FRRouting `vtysh` output showing BGP summary |
| ![PeeringDB entry](docs/img/screenshots/peeringdb.png) | AS215932 PeeringDB page and contact details |
| ![Weathermap](docs/img/screenshots/weathermap.png) | Network weathermap from https://as215932.net |

*Want to add one? Open a PR with the image in `docs/img/screenshots/` and a short caption.*

## Repository Structure
```
network-operations/
├── autoinstall/       # OS autoinstall configs and QMP tools
├── configs/           # Configuration templates (Jinja2)
├── docs/              # Architecture and deployment documentation
├── scripts/           # Bootstrap and operational scripts
└── .github/           # Issue templates and workflows
```

## Peering

I maintain an **open peering policy** and welcome peering requests at any of my IXP locations.

**Peering Requirements**:
- Valid entry in PeeringDB
- IRR objects registered
- 24/7 NOC contact
- RPKI ROA configured

Contact via PeeringDB or open an issue in this repository with the `peering` label.

## Documentation

Key documentation:
- [Network Architecture](docs/architecture.md)
- [BGP Routing Policy](docs/bgp-policy.md)
- [Peering Guidelines](docs/peering.md)
- [Agentic Development Loop](docs/agentic-development-loop.md)

## Roadmap & milestones

This is a living list of what we're building. Completed items are checked;
ongoing work is tracked in [GitHub Issues](../../issues).

- [x] Obtain AS number and IP allocations
- [x] Establish BGP transit agreements
- [x] Deploy at Internet Exchange Points
- [x] Complete PeeringDB and IRR registration
- [x] Implement core routing infrastructure (`cr1-nl1`, `cr1-de1`, `cr1-ch1`, `rtr`)
- [x] Expand IXP presence
- [ ] Grow peering relationships
- [ ] Deploy additional services (Tor / I2PD / Yggdrasil relays, public resolvers)
- [ ] Implement x402 one-time service usage (e.g., pay-per-request)
- [x] Automate configuration management
- [ ] Build comprehensive monitoring stack
- [ ] Publish live Looking Glass

### Near-term priorities

1. Stabilize automated promotion and deployment runbooks.
2. Expand IXP peering in Western Europe.
3. Ship public DNS resolvers over IPv6.
4. Open-source reusable FRRouting policy templates.

## Using This Repository

This repository serves as:
- **Issue tracker** for infrastructure work and bugs
- **Configuration library** for reusable network configs
- **Documentation hub** for architecture and procedures
- **Public record** of network operations and decisions

Feel free to:
- Browse issues to see ongoing work
- Learn from configuration examples
- Open issues for peering requests or questions
- Contribute suggestions or report issues

## Resources

- [RIPE Database: AS215932](https://apps.db.ripe.net/db-web-ui/query?searchtext=AS215932)
- [PeeringDB: AS215932](https://www.peeringdb.com/asn/215932)
- [Hurricane Electric BGP Toolkit](https://bgp.he.net/AS215932)

## Related repositories

- [`hyrule-mcp`](https://github.com/AS215932/hyrule-mcp) — Live MCP diagnostic substrate
- [`engineering-loop`](https://github.com/AS215932/engineering-loop) — Autonomous infrastructure change loop
- [`noc-agent`](https://github.com/AS215932/noc-agent) — Alert intake and incident analysis
- [`as215932.net`](https://github.com/AS215932/as215932.net) — Public website and weathermap
- [`hyrule-cloud`](https://github.com/AS215932/hyrule-cloud) — Agentic VPS hosting API with x402 payments
- [`hyrule-web`](https://github.com/AS215932/hyrule-web) — Main branded website

## Contact

- **NOC Email**: noc@as215932.net
- **PeeringDB**: https://www.peeringdb.com/asn/215932
- **Issues**: Use GitHub Issues for operational questions or peering requests

---

**License**: Configuration examples and documentation in this repository are provided as-is for educational purposes.

*Building the Internet, one BGP session at a time.*
