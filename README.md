# AS215932 Network Operations

Public operations repository for **AS215932** (Hyrule/Servify) - building a complete Internet Service Provider from scratch.

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

### Internet Exchange Points

*Active IXP presence at multiple locations - see [PeeringDB](https://www.peeringdb.com/asn/215932) for current list*

## Technical Stack

### Routing & Network

- **Core Routers**: FreeBSD + FRRouting
- **Gateways/Edge Routers**: OPNsense
- **Overlay Network**: Wireguard tunnels
- **Protocols**: BGP, OSPFv3
- **Virtualization**: XCP-NG w/ Xen Orchestra

### Architecture Highlights

- IPv6-first design with IPv4 transition mechanisms (NAT64/DNS64, 464XLAT)
- Multi-homed BGP (with ECMP load balancing)
- Stateless overlay for asymmetric routing
- Distributed routing architecture with OSPF internal connectivity

## Repository Structure
```
network-operations/
├── docs/              # Architecture documentation
├── configs/           # Sanitized configuration templates
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

## 📚 Documentation

Key documentation:
- [Network Architecture](docs/architecture.md) *(coming soon)*
- [BGP Routing Policy](docs/bgp-policy.md) *(coming soon)*
- [Peering Guidelines](docs/peering.md) *(coming soon)*

## 🎯 Project Goals

- [x] Obtain AS number and IP allocations
- [x] Establish BGP transit agreements
- [x] Deploy at Internet Exchange Points
- [x] Complete PeeringDB and IRR registration
- [x] Implement core routing infrastructure
- [ ] Expand IXP presence
- [ ] Grow peering relationships
- [ ] Deploy additional services (Tor/I2PD/Yggdrasil relays, public resolvers, etc.)
- [ ] Automate configuration management
- [ ] Build comprehensive monitoring stack

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

## Contact

- **NOC Email**: noc@as215932.net
- **PeeringDB**: https://www.peeringdb.com/asn/215932
- **Issues**: Use GitHub Issues for operational questions or peering requests

---

**License**: Configuration examples and documentation in this repository are provided as-is for educational purposes.

*Building the Internet, one BGP session at a time.*
