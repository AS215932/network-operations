# Retention monitoring rollout

Hold this ruleset until the Cloud retention backend and migrations 024/025 are deployed and its authenticated metrics endpoint emits both retention gauges. Retention itself can remain disabled: the exporter must still emit zero values and verify any existing retained records. Merging the rules triggers the normal Prometheus deployment, so a code merge is also a deployment decision here.

The rules warn on persisted verification failure and overdue work after five minutes. Missing either gauge on a healthy target, or a missing scrape target, warns after fifteen minutes. A failed scrape stays owned by the existing HyruleCloudMetricsDown rule. No customer/public-status labels are attached. Existing alert routing remains in use; do not send synthetic messages to real channels.

Validate with promtool test rules tests/prometheus/hyrule-retention.test.yml. After the reviewed main deployment, verify the loaded rules and natural metric/target state via Prometheus. Do not enable retention before independent monitoring, provider write semantics and disposable data/boot recovery are verified. A retention deadline does not authorize deletion. Preserve data, manifests and operation history during investigation; do not purge to clear an alert.
