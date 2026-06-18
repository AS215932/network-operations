# NetOps testing strategy

How AS215932 IaC changes are validated, from a cheap stdlib check on every PR
up to full control-plane modelling and a live dynamic lab. The tiers map onto
the two-runner security model (`docs/ci/security-model.md`): untrusted PR code
runs only on the unprivileged `hyrule-public-pr` runner; the heavy labs run on
the privileged `ci` runner on trusted triggers only.

## Tiers

| Tier | Jobs | Runner | Trigger | Gating |
|------|------|--------|---------|--------|
| 0 — static | `static-iac`, `ansible-idempotency` | `hyrule-public-pr` (unprivileged) | PRs with IaC path changes | **required** via `iac-gate` |
| 1 — Batfish | `batfish` | `ci` (privileged) | `workflow_dispatch`, repo var `ENABLE_BATFISH_TESTS`, nightly | advisory / trusted-only |
| 2 — Containerlab | `containerlab-frr` | `ci` (privileged) | `workflow_dispatch`, repo var `ENABLE_CONTAINERLAB_TESTS`, nightly | advisory / trusted-only |
| 2 — NETCONF/YANG lab | `netconf-yang-lab` | `ci` (privileged) | `workflow_dispatch`, repo var `ENABLE_NETCONF_YANG_TESTS`, nightly | advisory / trusted-only |
| 3 — deploy safety | `apply.yml` (manual, `production`, Icinga pre/post + Goss), `drift-detection.yml` (nightly check-mode) | `ci` (privileged) | manual / schedule | deploy-time |

All of Tier 0 is in `.github/workflows/iac-tests.yml`. Tiers 1–2 also live there
(gated off PRs) and run nightly via `.github/workflows/netops-nightly.yml`.

### Tier 0 — static (required)

`static-iac` runs `scripts/ci/iac-static.sh`, which is intentionally
dependency-light: a stdlib `unittest` discovery over `tests/iac/` plus external
validators (`named-checkzone`, `systemd-analyze`, `caddy adapt`,
`unbound-checkconf`, `nft -c`) when the tool is present on the runner.

The `unittest` suite includes the **source-of-truth schema gate**
(`tests/iac/test_inventory_schema.py`): it validates the structure and internal
consistency of `ansible/inventory/{hosts.yml,group_vars/all.yml}` before
anything is rendered — every referenced host resolves to a canonical
`ansible_host`, addresses are valid and unique, infra VMs sit in
`infra_subnet`, the **ci-pr runner sits on the customer segment and never the
infra segment** (the two-runner isolation invariant, pinned at the data layer),
subnets nest under the allocation and are pairwise disjoint, the `peers` map
agrees with `hosts.yml`, router loopbacks live in the loopback subnet, and eBGP
neighbours are global addresses outside our own prefix.

`ansible-idempotency` re-renders and asserts the idempotency contract via
`scripts/ci/ansible-idempotency.sh`. Both are pure local rendering / syntax
checks — no Vault, no `id_ci`, no fleet SSH — which is why they are safe on the
unprivileged runner (see the Wave 4 migration guard in
`docs/ci/security-model.md`).

### Tiers 1–2 — Batfish & Containerlab (trusted-only)

These spin up `batfish/allinone` and Containerlab (Docker + lab infra) and are
**never** run automatically on an arbitrary PR. Enable them on a trusted PR by
setting the repo variable `ENABLE_BATFISH_TESTS` / `ENABLE_CONTAINERLAB_TESTS`
to `true`, or run `iac-tests` / `netops-nightly` via **Run workflow**
(`workflow_dispatch`). Both upload their artifacts (Batfish answer frames /
logs, Containerlab inspect / `show bgp summary json` / logs) on every run for
post-mortem.

Current Batfish assertions (`tests/iac/batfish/batfish_as215932_test.py`): BGP
session compatibility, iBGP full mesh across the three core routers, unique
router-IDs, no undefined references, customer segment cannot reach infra
management, authorized CI can reach management ports. Containerlab
(`tests/iac/containerlab/`) deploys the core topology and checks FRR/BGP comes
up. The trusted NETCONF/YANG lab builds a lab-only FRR image with sysrepo and
Netopeer2, verifies NETCONF capability/schema discovery, exercises candidate
validate/discard/commit/cleanup, and confirms BGP stays established afterwards.
Deeper assertions (no unexpected eBGP, prefix-export hygiene,
default-route/failover, topology-from-source-of-truth, advertise/withdraw sims)
are tracked as a follow-up — they need the lab to develop and verify.

## The `iac-gate` and branch protection

`iac-tests.yml` intentionally is **not** workflow-level path-filtered. GitHub
does not create check runs for a workflow skipped by `paths`, so a required
context from that workflow can sit at "Expected" forever on a docs-only PR.
Instead, the workflow always starts and does path detection inside the workflow.

The fix is the **`iac-gate`** aggregator job (`if: always()`, `needs:` the
internal `changes` job plus all tiers). It is the single required status
context:

- It **always reports** because the workflow always runs.
- On PRs with no IaC-relevant changes, the internal `changes` job reports
  `iac_changed=false`, the tier jobs are skipped, and `iac-gate` passes.
- On IaC PRs, `iac-gate` passes only when the required tiers (`static-iac`,
  `ansible-idempotency`) succeed and the trusted lab tiers are *success or
  skipped* — never failed.

Therefore branch protection requires **`iac-gate`** (plus the lint/render
contexts from `lint.yml` / `render-check.yml` and `semgrep`), **not** the
individual tier jobs. Verify after any change with a docs-only PR that touches
none of the IaC paths: `iac-tests` must run, the tier jobs should be skipped,
and `iac-gate` must report success rather than a stuck "Expected" check.

## Adding a check

1. Static contract (preferred): add a `tests/iac/test_*.py` `unittest` case —
   it runs in Tier 0 automatically, on every IaC PR, for free.
2. Control-plane property: add a Batfish assertion (Tier 1).
3. Dynamic/runtime behaviour: extend the Containerlab topology/checks (Tier 2).

Keep Tier 0 stdlib-only (`unittest` + PyYAML); push anything needing Docker or
the lab into Tiers 1–2 so the unprivileged runner stays dependency-light.
