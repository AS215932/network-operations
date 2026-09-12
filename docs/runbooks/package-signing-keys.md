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


## HashiCorp rotation, September 10, 2026

Verified September 12 against https://www.hashicorp.com/en/official-packaging-guide:
primary fingerprint `D55C0D1AC78A8D8126CB631CFC9CA96ACA026560`.
The public key from `https://apt.releases.hashicorp.com/gpg` has SHA256
`1df7d66b79352bab45388511079f74987568672a8ed6395053a050b61f6c5430`.
The previous key ended September 10; do not disable signature verification.

If old-key rejection blocks a role's prerequisite APT refresh, use the reviewed
`hashicorp-key` maintenance playbook through `apply.yml`, limited to `noc`. The play itself targets only `noc:!retired`, so an empty or
broad workflow limit cannot include other machines. Extending this repair to
another host requires a separate reviewed scope change. It replaces only the existing scoped keyring, retains
an Ansible backup, and requires strict all-repository APT verification. It installs
no packages and restarts no services. The agent role pin is updated so subsequent
rollouts cannot restore the obsolete key. A dry-run does not prove live APT health.

If validation fails, preserve the failure and backup; do not retry the app deploy.
Restore the recorded backup to the same keyring path if the replacement itself
must be rolled back; the old key cannot validate newly signed repository indexes,
so rollback preserves prior state rather than claiming restored repository health.
