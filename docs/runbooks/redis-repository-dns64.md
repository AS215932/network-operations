# Redis repository connectivity

The NOC's APT refresh fails on packages.redis.io over native CloudFront IPv6. Requests to both tested IPv4 endpoints through the existing 64:ff9b::/96 NAT64 path return HTTP 200 with normal TLS verification. This is the same class of transit failure already documented for the Docker CloudFront hostname in the router's DNS64 configuration.

Install only `configs/rtr/unbound/redis-repository-dns64.conf` as `/etc/unbound/unbound.conf.d/redis-repository-dns64.conf` on rtr after CI and review. Preserve an existing destination before replacement, validate the full configuration with `unbound-checkconf`, and reload Unbound only after validation passes. Clear the cached packages.redis.io answer using `unbound-control flush_zone packages.redis.io`.

Verify that a fresh AAAA lookup through rtr produces a 64:ff9b::/96 address, fetch the Redis InRelease file with normal TLS verification from noc, and run a strict `apt-get update -o APT::Update::Error-Mode=any` on noc. Check normal public and internal DNS resolution after the reload. This drop-in neither changes the NAT64 translator nor removes the Redis repository or package-signing checks.

If validation or post-reload probes fail, restore the saved destination (or remove only this newly added file if none existed), validate the full configuration, reload Unbound, and flush the same name again. Record the unresolved repository outage. Remove this exception only after native IPv6 repository probes remain successful for 24 hours.
