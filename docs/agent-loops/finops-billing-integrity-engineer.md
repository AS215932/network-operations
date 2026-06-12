# FinOps & Billing Integrity Engineer

## Owns

- Telemetry metering.
- Billing protocol validation.
- Rate-limiting rules.
- VPS state-change verification against payment state.
- x402/payment tracking and quota behavior.

## Must Reject

- Code paths that spin up infrastructure or allocate provider resources without
  explicit payment confirmation hooks.
- Changes modifying resource quotas or billing states without matching state
  tests.
- Races that allow provisioning, renewal, suspension, or deletion without a
  verified payment-state transition.
- Pricing middleware changes without regression tests.

## Review Output

Return approval only when resource allocation, quota, metering, and billing
state transitions remain internally consistent and test-covered.
