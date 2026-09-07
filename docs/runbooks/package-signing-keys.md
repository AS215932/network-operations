# Package signing keys

The Vault Agent role pins the SHA-256 of the HashiCorp package signing key.
Ansible verifies a matching installed key locally and skips the download.
A missing or different key must be downloaded over verified HTTPS and match
the configured checksum. This prevents an unrelated CDN outage from blocking
an app deployment that already has the reviewed key installed.

Verified on 2026-09-07 against https://apt.releases.hashicorp.com/gpg and the
installed NOC key:

- SHA-256: `cafb01beac341bf2a9ba89793e6dd2468110291adfbb6c62ed11a0cde6c09029`
- Primary fingerprint: `798AEC654E5C15428C8E42EEAA16FCBCA621E701`
- Signing subkey: `EB0AF5E2994969596F99873E706E668369C085E9`

For rotation, obtain the replacement from the official HTTPS endpoint, verify
the fingerprint against HashiCorp's published rotation guidance, and review the
key, URL and checksum together in a PR. Overrides of
`vault_agent_hashicorp_gpg_url` must supply the corresponding
`vault_agent_hashicorp_gpg_checksum`. Never remove checksum or TLS verification
to work around download failures.

The Redis key follows the same pattern in the NOC role; its fingerprint and
rotation notes are in [loop retirement](loop-retirement.md). These checks do not
solve the underlying IPv6 CDN reachability incident, nor do they bypass package
repository signature validation.
