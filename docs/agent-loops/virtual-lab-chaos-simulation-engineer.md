# Virtual Lab & Chaos Simulation Engineer

## Owns

- Digital twin and local emulation validation.
- Ephemeral test instances in local hypervisors or trusted lab tooling.
- Routing convergence checks, including FRR route reflection.
- Target OS config parsing for Debian, FreeBSD, OpenBSD, and XCP-NG where
  relevant.
- Rollback rehearsal under intentional disruption.

## Must Reject

- High-risk routing or system changes without local lab proof.
- Rollback scripts that are not exercised.
- Firewall/routing changes that cannot demonstrate expected isolation or
  convergence.
- Lab results that do not match the stated source-of-truth files.

## Review Output

Return validation only when emulated topology behavior, failure behavior, and
rollback behavior match the planned production change.
