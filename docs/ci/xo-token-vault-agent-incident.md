# Incident Note: `xo_token` Missing On CI Deploys

The failed Hyrule Cloud deploys were not caused by a dead Vault Agent on the
`ci` runner. The runner-side agent rendered `/etc/github-runner/secrets.env`,
but that template only included runner-scoped secrets such as Discord and
Icinga values. It did not render `XO_TOKEN`/`xo_token`.

The old deploy path allowed `hyrule-cloud` to reach the Ansible env template
with an undefined `xo_token`, so the failure appeared during deploy instead of
as an explicit preflight error.

The fixed contract is:

- `kv/ci-runner` contains only runner-scoped CI/apply secrets.
- `kv/hyrule-cloud` contains Hyrule Cloud runtime secrets, including
  `xo_token`.
- `vault-agent-hyrule-cloud.service` on the `api` VM renders
  `/opt/hyrule-cloud/.env`.
- `scripts/ci/deploy-preflight.sh` fails if `XO_TOKEN` is added back to the
  runner secret bundle or if the cloud role depends on runner-side `xo_token`.

Related DNS gap: `ci.as215932.net` and its PTR were missing even though
`peers.ci.ipv6` and `hosts.yml` already declared `2a0c:b641:b50:2::d0`.
The DNS/inventory tests now cover that class of fault.
