# Retention verification monitoring

Deploy these rules only after AS215932/hyrule-cloud PR119 has passed CI/review, been SHA-promoted, migrated through 025, and exposed both verification gauges on the authenticated metrics endpoint. Keep this infrastructure PR draft until that prerequisite is live. Merging rule changes schedules the normal monitoring deployment. Leave expiry retention disabled until alert delivery and disposable data/boot recovery are verified.

`HyruleRetentionVerificationFailed` reports failed protection/inventory checks after two minutes. `HyruleRetentionVerificationOverdue` reports outstanding checks after five minutes; the application gauge already includes a five-minute overdue allowance. Both can fire when distinct retained guests have different problems; neither masks the other. These are aggregate operational alerts with no customer identifiers or automatic public incident labels.

`HyruleRetentionTelemetryMissing` reports a successful scrape missing either required gauge for ten minutes, matched by job and instance. The existing `HyruleCloudMetricsDown` covers failed scrapes and an absent scrape job after five minutes, avoiding an additional retention-specific scrape-down alert. Existing Alertmanager grouping and repeat policies apply.

Inspect the private admin retention view for last success, last attempt, generic failure and next due time. Check the cloud worker if verification is overdue. Preserve the retention record and original VM/disks while reconciling drift; these alerts do not authorize deletion or automatic repair. A healthy API alone does not prove verification is running.

Before enabling retention, inspect live rule loading, both gauge families, scrape health and the existing NOC delivery monitor. The local promtool fixture exercises healthy, failed, overdue, missing, recovery, target isolation and absent-job scenarios without sending real channel notifications.
