# Coordinated cloud schema deployment

The cloud role holds both API and worker starts before checkout or dependency
sync. Persistent systemd drop-ins require the absence of per-service
`*.deployment-pending` files in the configured cloud configuration directory.
The role creates these files, reloads systemd, and stops the existing worker
and API. Vault rendering can continue, but restart callbacks cannot start the
held services. The files also survive a host reboot and an interrupted apply.

After dependency sync and secret rendering, migrations must succeed before
the API marker is removed. The API must then start and pass its local HTTP
health check before the worker marker is removed and the worker starts.
This introduces an API maintenance interval during deployment. Customer VMs
are not stopped by these deployment tasks.

If deployment fails, inspect the failed task and rerun the reviewed deployment
through the normal pinned promotion workflow. Do not remove pending files to
force services up against an unknown schema. A failed API health check leaves
the worker held. An interrupted run is recovered by re-establishing both
barriers and repeating the migration and health sequence. Do not run concurrent
manual applies against the same host.

Rollback must use code compatible with the current database. In particular,
lifecycle migration 021 refuses downgrade while deletion claims are pending;
resolve those claims through the application's recovery procedure first.
There is no automatic schema downgrade or unconditional restart on failure.

Validation must include real systemd start/restart attempts while held,
first installation, interrupted deployment, and retry, in addition to task
ordering tests. Local condition evaluation alone does not prove the complete
production rollout. This change must pass review and CI before promotion.

Run the disposable local user-systemd regression with:

```sh
AS215932_SYSTEMD_TEST=1 python3 -m unittest discover -s tests/iac -p test_cloud_quiescence.py -v
```

It uses unique temporary user units and removes them afterward. It tests the
role's rendered condition with actual start/restart requests, manager reload,
separate API/worker release, and retry. It does not reboot a host or execute a
production database migration.
