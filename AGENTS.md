# AS215932 Infrastructure Agent Guide

## Domain Policy

- `hyrule.host` is customer-facing Hyrule Cloud/product identity. Use it for the product site, public Hyrule Cloud API, and customer VM subdomains.
- `servify.network` is infrastructure identity for nameservers, underlay and management references, provider relationships, internal UIs, and partner-facing hostnames.
- `as215932.net` is AS215932 overlay/routing identity only. DNS records in this zone must point only at prefixes owned by AS215932.

Do not blindly replace `servify.network`: nameservers, monitoring, Xen Orchestra, router hostnames, reverse DNS, Openprovider examples, and partner-facing infrastructure references are intentionally kept there.
