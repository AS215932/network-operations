## Promotion

App PRs:
- `AS215932/<repo>#<number>` -> `<40-char-sha>`

Pinned versions:
- `noc_agent_version`: unchanged or `<40-char-sha>`
- `hyrule_mcp_version`: unchanged or `<40-char-sha>`
- `hyrule_cloud_version`: unchanged or `<40-char-sha>`
- `hyrule_web_version`: unchanged or `<40-char-sha>`

Deploy impact:
- Affected playbooks:
- Expected service restarts:
- Operator-visible behavior:

Rollback:
- Previous `noc_agent_version`:
- Previous `hyrule_mcp_version`:
- Previous `hyrule_cloud_version`:
- Previous `hyrule_web_version`:

Validation:
- [ ] App CI is green for every promoted SHA.
- [ ] `scripts/ci/iac-static.sh` passes.
- [ ] `apply.yml` dry-run completed for each affected playbook.
- [ ] Production `apply.yml` completed.
- [ ] Icinga pre/post snapshot diff reviewed.
