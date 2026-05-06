# Application logging — contract for AS215932 apps

This is the convention every application running on AS215932 follows so its
logs land cleanly in the centralized stack (Vector → Loki → Grafana, see
[deployment.md](deployment.md) and [network-flows.md](network-flows.md)).
A new repo or service should not invent its own logging strategy — copy the
language snippet below and you are done.

## TL;DR

- Write **newline-delimited JSON** to **stdout**. Nothing else.
- systemd-journald captures stdout automatically; the host-local Vector
  agent reads journald and ships to the log VM.
- Required fields per event: `ts`, `level`, `event`, `service`.
- Don't log secrets. Use conventional field names so the aggregator's
  redaction can find them.

## Where logs go

- **Always** to **stdout** as **newline-delimited JSON** (one event per line).
- **Never** to files. **Never** to syslog directly. **Never** to a custom
  log file rotated by `logrotate`.

systemd-journald captures the stdout of every systemd-managed service with
rich metadata (`_SYSTEMD_UNIT`, `SYSLOG_IDENTIFIER`, `PRIORITY`, `_HOSTNAME`,
`_PID`). That is the entire shipping path on the app side. The Vector agent
on the host has a `journald` source that picks it up. Nothing in app code
talks to Loki, the aggregator, or the network — apps emit events; the
infrastructure routes them.

## Required JSON fields

Every log line must be a JSON object with at least these four fields:

| Field | Type | Example | Purpose |
|---|---|---|---|
| `ts` | RFC3339 string (UTC) | `"2026-05-06T12:34:56.789Z"` | Event timestamp. |
| `level` | string | `"info"` | One of: `debug`, `info`, `warn`, `error`, `crit`. Maps to the Loki `level` label. |
| `event` | string (snake_case) | `"vm_provision_started"` | Short event name, NOT a sentence. The searchable identifier for the kind of thing that happened. |
| `service` | string | `"hyrule-cloud"` | Short service name. Must match the systemd unit basename (i.e. without `.service`). Maps to the Loki `service` label. |

## Encouraged optional fields

- `request_id` — UUID propagated from upstream. FastAPI middleware should
  set/read `X-Request-ID`.
- `error` — for `error`/`crit` events, an object
  `{ "type": "...", "message": "...", "stack": "..." }`. Truncate `stack`
  to 4 KB.
- Any flat top-level event-specific fields (e.g. `vm_uuid`, `domain`,
  `duration_ms`). Loki's `| json` query is at its best on flat structures;
  avoid nesting more than one level deep.

## Forbidden values

Apps must **NOT** log:

- Plaintext secrets — API keys, passwords, tokens, signing keys.
- x402 payment headers (`X-PAYMENT`, `payment-signature`) — strip in the
  middleware before logging the request.
- Full wallet addresses — log a hash, or omit.
- PII when not strictly required.

If you must log a field that may contain a secret, **use a conventional
field name** so the Vector aggregator's redaction step removes it for free:

```
.password   .token   .api_key   .secret
.X_PAYMENT  ."X-PAYMENT"   .payment_signature
```

`del(.password)`, `del(.token)`, `del(.api_key)` are applied to every event
before it reaches Loki. The aggregator will *not* log-search for arbitrary
field names you invent — stick to the conventions above when possible.

## systemd unit conventions

Every app's systemd unit must include:

```ini
[Service]
StandardOutput=journal
StandardError=journal
SyslogIdentifier=<service-name>     # e.g. hyrule-cloud
```

This makes journald entries identifiable via both
`_SYSTEMD_UNIT=<service>.service` and `SYSLOG_IDENTIFIER=<service>`. Vector
maps either to the `service` Loki label, so dropping the `.service` suffix
in `SyslogIdentifier` is intentional — it is what queries match against.

## Reference implementations

### Python — `structlog`

This is the snippet for current and future Python apps (`hyrule-cloud`,
`hyrule-web`, `noc-agent`, `hyrule-mcp`). Copy verbatim into the app's
startup; replace the `service=` value:

```python
import logging
import sys

import structlog

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        structlog.contextvars.merge_contextvars,
        structlog.processors.dict_tracebacks,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
    cache_logger_on_first_use=True,
)

log = structlog.get_logger().bind(service="hyrule-cloud")
```

Usage:

```python
log.info("vm_provision_started", vm_uuid=uuid, domain=domain, duration_ms=42)
log.error("dns_update_failed", domain=domain, error={"type": exc_type, "message": str(exc)})
```

### Go — `log/slog`

```go
import (
    "log/slog"
    "os"
)

logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
    Level: slog.LevelInfo,
})).With("service", "your-service-name")

logger.Info("vm_provision_started", "vm_uuid", uuid, "domain", domain)
```

`slog`'s default keys are `time`, `level`, `msg`. To match the contract
exactly: rename `time` → `ts` and `msg` → `event` via a `ReplaceAttr` hook
(or accept the defaults if a follow-up PR will normalize at the Vector
layer — current default is to require `ts`/`event`/`level`/`service`).

### Node — `pino`

```js
import pino from 'pino';

export const log = pino({
  base: { service: 'your-service-name' },
  timestamp: () => `,"ts":"${new Date().toISOString()}"`,
  formatters: {
    level: (label) => ({ level: label }),
  },
  messageKey: 'event',
});

log.info({ vm_uuid: uuid, domain }, 'vm_provision_started');
```

### Rust — `tracing`

```rust
use tracing_subscriber::fmt;

fmt()
    .json()
    .with_current_span(false)
    .with_target(false)
    .flatten_event(true)
    .init();
```

Use `tracing::info!(event = "vm_provision_started", vm_uuid = %uuid, domain = %domain)`.
Add a layer that injects `service = "your-service"` on every event.

## How the events become Loki streams

The Vector agent's `journald` source provides each event with `MESSAGE`,
`_SYSTEMD_UNIT`, `SYSLOG_IDENTIFIER`, `PRIORITY`, etc. A VRL transform on
the host parses `MESSAGE` as JSON when it is a JSON object, then merges
those fields up:

```vrl
parsed, err = parse_json(.message)
if err == null && is_object(parsed) {
    . = merge(., parsed)
}
.service = .service ?? to_string(._SYSTEMD_UNIT) ?? to_string(.SYSLOG_IDENTIFIER) ?? "unknown"
.level   = .level   ?? syslog_priority_to_level(.PRIORITY)
```

Apps following this contract end up with rich, queryable fields. OS daemons
and third-party services that emit plaintext still flow through with a
plaintext `.message` and metadata-derived labels — they just lose the
structured-search benefit.

## Verifying your app

After deploy:

1. `journalctl -u <service> -n 5 --output=json | jq .MESSAGE` — confirm each
   line is a JSON object with `ts`, `level`, `event`, `service`.
2. In Grafana Explore: `{service="<service>"}` returns recent events.
3. `{service="<service>"} | json | level="error"` returns parsed events with
   typed fields visible.
4. Trigger an event that includes a secret-looking value (e.g. log a fake
   `X-PAYMENT` header). In Grafana, the value should appear as `[REDACTED]`.
   If it does not, escalate — the redaction layer needs a new rule.

## Why this shape

- **stdout-only** so the deployment is uniform: every systemd unit can be
  ingested without per-app config in Vector.
- **JSON first** so queries don't depend on regex against free-form
  English. `level="error"` works regardless of language or framework.
- **Field-name conventions for secrets** so redaction is O(1)
  (`del(.field)`) instead of expensive regex against every line.
- **One contract for all languages** so the operator's mental model
  carries between repos. `{service="X"} | json | level="error"` is the
  same query whether X is Python, Go, Node, or Rust.

## Migration of existing apps

| App | Status | Required change |
|---|---|---|
| `hyrule-cloud` | uses structlog with `ConsoleRenderer` | Swap to `JSONRenderer` per the snippet above. Verify `SyslogIdentifier=hyrule-cloud` in the systemd unit. |
| `hyrule-web` | `print()` / bare exception strings | Add structlog config; replace prints with `log.info("event_name", ...)` calls. |
| `noc-agent` | `print()` calls | Same as `hyrule-web`. |
| `hyrule-mcp` | `print()` calls | Same. Critical: SSH command stdout must go in a separate `stdout` field (not concatenated into `event`/`msg`) so the aggregator's `del`/length-cap redaction can act on it. |

Each migration is one small per-repo PR. Deploys can land before, alongside,
or after the host-side Vector agent rollout — Vector handles plaintext too,
JSON renderers just give cleaner queries.
