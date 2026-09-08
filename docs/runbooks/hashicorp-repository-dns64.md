# HashiCorp repository connectivity

After the Redis repair, strict APT refresh on noc still failed because native IPv6 connections to apt.releases.hashicorp.com timed out. Both tested IPv4 endpoints (143.204.55.125 and 143.204.55.13) returned HTTP 200 through the existing NAT64 path with TLS hostname verification.

After green CI and review, install only configs/rtr/unbound/hashicorp-repository-dns64.conf at /etc/unbound/unbound.conf.d/hashicorp-repository-dns64.conf on rtr. Preserve any existing destination. Validate the full configuration with unbound-checkconf before reloading only Unbound; flush the hostname with unbound-control flush_zone apt.releases.hashicorp.com. Do not change routing or the NAT64 translator.

Use Python socket.getaddrinfo (dig is absent on rtr) to verify that fresh IPv6 answers use 64:ff9b::/96 and public/internal DNS still resolves. On noc, verify normal HTTPS access to the trixie InRelease file and require apt-get update -o APT::Update::Error-Mode=any to exit zero. Retain repository signatures and TLS verification.

If DNS or TLS verification fails, restore the previous destination or remove only the newly installed file, validate, reload Unbound, and flush the name. Other failing repositories must be diagnosed separately. Remove this exception after native IPv6 probes succeed continuously for 24 hours.
