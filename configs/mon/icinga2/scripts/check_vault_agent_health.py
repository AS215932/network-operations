#!/usr/bin/env python3
"""
check_vault_agent_health.py — Icinga2 plugin: is a Vault Agent actually
delivering secrets?

Background — the hyrule-cloud secrets outage of 2026-07-18 → 2026-07-25.
`vault-agent-hyrule-cloud.service` on the `api` VM failed every AppRole login
with 403 for a week. systemd reported `active (running)` the whole time, so
node-up, the systemd unit-state checks and hyrule-cloud's own HTTP health check
were all green. Meanwhile `/opt/hyrule-cloud/.env` was frozen at its 18 July
content and `/run/vault-agent/hyrule-cloud.token` never appeared, so every
`vault kv patch` silently failed to reach the application. It was found by hand,
seven days later.

The signals below come from the on-host textfile collector installed by
ansible/roles/vault_agent (vault-agent-health-metrics.timer) and scraped by
node_exporter. This plugin evaluates them for one agent on one host:

  UNKNOWN   metrics missing or older than --collector-max-age
            (the collector itself is broken — fix that before trusting the rest)
  CRITICAL  no token sink            → the agent holds no Vault token
  CRITICAL  token sink older than --token-max-age
                                     → re-authentication has stopped
  CRITICAL  Vault auth errors in the agent journal in the recent window
                                     → the 403 loop, caught within minutes
  CRITICAL  agent unit not active
  CRITICAL  a rendered destination is missing
  WARNING   a rendered destination is older than --render-max-age (opt-in;
            Vault Agent only rewrites a destination when the rendered content
            changes, so age alone is not proof of a fault)
  WARNING   single-use SecretID already consumed
            (remove_secret_id_file_after_reading=true and the file is gone —
            the agent is alive on a credential it can never re-read, so the
            next restart puts it straight back into the 403 loop)

Fix procedure for every state above: docs/runbooks/vault-agent-durable-auth.md

Exit codes follow Nagios convention:
  0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

OK, WARNING, CRITICAL, UNKNOWN = 0, 1, 2, 3
STATE_NAMES = {OK: "OK", WARNING: "WARNING", CRITICAL: "CRITICAL", UNKNOWN: "UNKNOWN"}
RUNBOOK = "docs/runbooks/vault-agent-durable-auth.md"


def prom_query(url: str, query: str, timeout: float) -> list:
    """Run an instant query, returning the raw result list."""
    endpoint = url.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode(
        {"query": query}
    )
    with urllib.request.urlopen(endpoint, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("status") != "success":
        raise RuntimeError(payload.get("error", "prometheus returned an error"))
    return payload.get("data", {}).get("result", [])


def sample_value(result_entry) -> float:
    return float(result_entry["value"][1])


def human_age(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 120:
        return "%ds" % seconds
    if seconds < 7200:
        return "%dm" % (seconds // 60)
    if seconds < 172800:
        return "%dh" % (seconds // 3600)
    return "%dd" % (seconds // 86400)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prom-url", default="http://localhost:9090")
    parser.add_argument(
        "--instance", required=True, help='node_exporter instance, e.g. "[2a0c::20]:9100"'
    )
    parser.add_argument("--agent", required=True, help="Vault Agent name, e.g. hyrule-cloud")
    parser.add_argument(
        "--collector-max-age",
        type=float,
        default=900.0,
        help="seconds before the metrics sample itself is considered stale",
    )
    parser.add_argument(
        "--token-max-age",
        type=float,
        default=21600.0,
        help=(
            "seconds before the token sink is considered stale. Must exceed the "
            "AppRole's token_max_ttl: the sink is rewritten on re-authentication, "
            "not on every renewal"
        ),
    )
    parser.add_argument(
        "--render-max-age",
        type=float,
        default=0.0,
        help=(
            "seconds before a rendered destination warns; 0 disables. Only useful "
            "where the secret is known to change on a schedule"
        ),
    )
    parser.add_argument(
        "--ephemeral-state",
        choices=["ok", "warning", "critical"],
        default="warning",
        help="state for a consumed single-use SecretID (default: warning)",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    selector = 'instance="%s"' % args.instance
    agent_selector = '%s,agent="%s"' % (selector, args.agent)

    try:
        collector = prom_query(
            args.prom_url,
            "vault_agent_collector_run_timestamp_seconds{%s}" % selector,
            args.timeout,
        )
        series = prom_query(
            args.prom_url,
            '{__name__=~"vault_agent_.+",%s}' % agent_selector,
            args.timeout,
        )
    except (urllib.error.URLError, OSError, ValueError, RuntimeError) as exc:
        print("UNKNOWN: Prometheus query failed (%s): %s" % (args.prom_url, exc))
        return UNKNOWN

    if not collector:
        print(
            "UNKNOWN: no vault_agent metrics from %s — is "
            "vault-agent-health-metrics.timer running? See %s"
            % (args.instance, RUNBOOK)
        )
        return UNKNOWN

    now = time.time()
    collector_age = now - sample_value(collector[0])
    if collector_age > args.collector_max_age:
        print(
            "UNKNOWN: vault-agent metrics on %s are %s old (> %s) — the health "
            "collector is not running, agent state is unverified. See %s"
            % (
                args.instance,
                human_age(collector_age),
                human_age(args.collector_max_age),
                RUNBOOK,
            )
        )
        return UNKNOWN

    if not series:
        print(
            "UNKNOWN: no metrics for Vault Agent %s on %s — agent not configured "
            "on this host, or its .hcl was removed. See %s"
            % (args.agent, args.instance, RUNBOOK)
        )
        return UNKNOWN

    scalars = {}
    renders = {}
    for entry in series:
        metric = entry.get("metric", {})
        name = metric.get("__name__", "")
        destination = metric.get("destination")
        if destination:
            renders.setdefault(destination, {})[name] = sample_value(entry)
        else:
            scalars[name] = sample_value(entry)

    problems = []
    state = OK

    def escalate(new_state, message):
        nonlocal state
        state = max(state, new_state)
        problems.append(message)

    if scalars.get("vault_agent_unit_active", 1) < 1:
        escalate(CRITICAL, "agent unit is not active")

    auth_errors = scalars.get("vault_agent_auth_errors_recent", 0)
    if auth_errors > 0:
        escalate(
            CRITICAL,
            "%d Vault auth failures in the agent journal (403 loop)" % int(auth_errors),
        )

    token_age = None
    if scalars.get("vault_agent_token_sink_present", 0) < 1:
        escalate(CRITICAL, "no token sink — the agent holds no Vault token")
    elif "vault_agent_token_sink_mtime_seconds" in scalars:
        token_age = now - scalars["vault_agent_token_sink_mtime_seconds"]
        if token_age > args.token_max_age:
            escalate(
                CRITICAL,
                "token sink last written %s ago (> %s) — re-authentication stopped"
                % (human_age(token_age), human_age(args.token_max_age)),
            )

    if (
        scalars.get("vault_agent_secret_id_ephemeral", 0) >= 1
        and scalars.get("vault_agent_secret_id_file_present", 1) < 1
    ):
        ephemeral_state = {"ok": OK, "warning": WARNING, "critical": CRITICAL}[
            args.ephemeral_state
        ]
        if ephemeral_state != OK:
            escalate(
                ephemeral_state,
                "single-use SecretID already consumed — the next restart of this "
                "agent cannot authenticate",
            )

    oldest_render = None
    for destination, values in sorted(renders.items()):
        if values.get("vault_agent_render_present", 0) < 1:
            escalate(CRITICAL, "rendered destination missing: %s" % destination)
            continue
        mtime = values.get("vault_agent_render_mtime_seconds")
        if mtime is None:
            continue
        age = now - mtime
        if oldest_render is None or age > oldest_render:
            oldest_render = age
        if args.render_max_age > 0 and age > args.render_max_age:
            escalate(
                WARNING,
                "%s last rendered %s ago (> %s)"
                % (destination, human_age(age), human_age(args.render_max_age)),
            )

    perfdata = [
        "token_age=%d;;;0" % int(token_age if token_age is not None else -1),
        "auth_errors=%d" % int(auth_errors),
        "renders=%d" % len(renders),
        "collector_age=%d" % int(collector_age),
    ]
    if oldest_render is not None:
        perfdata.append("oldest_render_age=%d" % int(oldest_render))

    if state == OK:
        summary = "vault-agent %s authenticated; token sink %s old, %d destination(s) rendered" % (
            args.agent,
            human_age(token_age) if token_age is not None else "n/a",
            len(renders),
        )
    else:
        summary = "vault-agent %s: %s — fix: %s" % (
            args.agent,
            "; ".join(problems),
            RUNBOOK,
        )

    print("%s: %s | %s" % (STATE_NAMES[state], summary, " ".join(perfdata)))
    return state


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never let a plugin traceback masquerade as OK
        print("UNKNOWN: check_vault_agent_health failed: %s" % exc)
        sys.exit(UNKNOWN)
