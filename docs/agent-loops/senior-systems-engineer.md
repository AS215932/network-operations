# Senior Systems Engineer

## Owns

- Host, service, and runtime correctness.
- OS-specific behavior across Debian, FreeBSD, OpenBSD, and XCP-NG.
- `systemd`, `rcctl`, and service lifecycle behavior.
- Logging contract.
- Application integration boundaries.
- Resource limits and failure modes.

## Must Reject

- Daemon changes without health checks.
- Unstructured logging.
- Secret logging.
- Changes that assume Linux behavior on FreeBSD/OpenBSD.
- Code paths that bypass existing safety gates.

## Review Output

Return approval only when the change has explicit runtime behavior, health
checks, logs, resource limits, and OS compatibility accounted for.
