import re
import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]
JOURNAL_GROUP = "systemd-journal"


class VaultAndRunnerContractsTest(unittest.TestCase):
    def test_runner_labels_cover_all_workflows(self):
        defaults = yaml.safe_load((REPO / "ansible/roles/github_runner/defaults/main.yml").read_text())
        labels = {str(label).replace("{{ github_runner_arch }}", defaults["github_runner_arch"]) for label in defaults["github_runner_labels"]}
        self.assertTrue({"self-hosted", "linux", "x64", "hyrule", "hyrule-infra"} <= labels)

    def test_pull_request_jobs_use_the_unprivileged_runner(self):
        # Two-runner model (Wave 4): every job reachable on a `pull_request`
        # event must run on the unprivileged ci-pr runner (hyrule-public-pr),
        # never on a privileged self-hosted label (hyrule / hyrule-infra). The
        # heavy labs (batfish, containerlab-frr) may keep the privileged label
        # ONLY because they are if-gated off pull_request (workflow_dispatch /
        # repo var). Privileged deploy workflows (apply, drift-detection) are not
        # pull_request-triggered, so they legitimately stay on hyrule-infra.
        privileged = {"hyrule", "hyrule-infra"}
        for workflow in (REPO / ".github/workflows").glob("*.yml"):
            spec = yaml.safe_load(workflow.read_text())
            triggers = spec.get("on", spec.get(True))  # PyYAML maps `on:` -> True
            if not _triggers_on_pull_request(triggers):
                continue
            for job_name, job in (spec.get("jobs") or {}).items():
                runs_on = job.get("runs-on")
                labels = set(runs_on) if isinstance(runs_on, list) else {runs_on}
                offending = labels & privileged
                if not offending:
                    continue
                cond = str(job.get("if", ""))
                self.assertTrue(
                    "workflow_dispatch" in cond or "vars." in cond,
                    f"{workflow.name}:{job_name} uses privileged label {offending} on a "
                    f"pull_request workflow without an if-gate restricting it off PRs",
                )

    def test_privileged_deploy_workflows_stay_on_ci_runner(self):
        # apply/drift/post-merge must keep the privileged runner and must NOT
        # leak onto the unprivileged ci-pr runner (they carry Vault + id_ci).
        for name in ("apply.yml", "drift-detection.yml", "post-merge-apply.yml"):
            text = (REPO / ".github/workflows" / name).read_text()
            self.assertIn("hyrule-infra", text, name)
            self.assertNotIn("hyrule-public-pr", text, name)

    def test_post_merge_apply_is_gated_and_serialized(self):
        # The auto-apply job must pause on the production environment and share
        # the live-apply concurrency lane with apply.yml — losing either means
        # unattended or overlapping production applies.
        spec = yaml.safe_load((REPO / ".github/workflows/post-merge-apply.yml").read_text())
        apply_job = spec["jobs"]["apply"]
        self.assertEqual(apply_job.get("environment"), "production")
        self.assertEqual(apply_job.get("concurrency", {}).get("group"), "production-infra-live-v2")
        self.assertFalse(apply_job.get("concurrency", {}).get("cancel-in-progress"))

    def test_required_render_check_reports_on_every_pull_request(self):
        workflow = yaml.safe_load((REPO / ".github/workflows/render-check.yml").read_text())
        pull_request = workflow.get("on", workflow.get(True))["pull_request"]

        self.assertNotIn(
            "paths",
            pull_request,
            "render is a required branch-protection context, so it must not be skipped by PR path filters",
        )

    def test_apply_workflow_can_gate_ci_runner_key_bootstrap(self):
        workflow = (REPO / ".github/workflows/apply.yml").read_text()

        self.assertIn("- ci-runner-key", workflow)
        self.assertIn("bootstrap_ci_runner_key:", workflow)
        self.assertIn("Connect as inventory users for first-time ci-runner-key bootstrap", workflow)
        self.assertIn("CI_KEY_PATH: /var/lib/github-runner/.ssh/id_ci", workflow)
        self.assertRegex(
            workflow,
            r"""case "\$playbook" in
\s+cloud\)
\s+apply_var="hyrule_cloud_apply=true"
\s+expected_apply_var="hyrule_cloud_apply=true"
\s+;;
\s+web\)
\s+apply_var="hyrule_web_apply=true"
\s+expected_apply_var="hyrule_web_apply=true"
\s+;;
\s+network-proxy\)
\s+apply_var="hyrule_network_proxy_apply=true"
\s+expected_apply_var="hyrule_network_proxy_apply=true"
\s+;;
\s+engineering-loop\)
\s+apply_var="engineering_loop_apply=true"
\s+expected_apply_var="engineering_loop_apply=true"
\s+extra_apply_vars="knowledge_mcp_apply=true knowledge_loop_apply=true agent_core_collector_apply=true agentic_observatory_apply=true"
\s+;;
\s+soc\)
\s+# role gate is soc_agent_apply \(role name != playbook name\)
\s+apply_var="soc_agent_apply=true"
\s+expected_apply_var="soc_agent_apply=true"
\s+;;
\s+\*\)
\s+apply_var="\$\{playbook//-/_\}_apply=true"
\s+expected_apply_var="\$\{playbook//-/_\}_apply=true"
\s+;;
\s+esac""",
        )
        self.assertIn('printf \'APPLY_VAR=%s\\n\' "$apply_var" >> "$GITHUB_ENV"', workflow)
        self.assertIn('printf \'APPLY_EXTRA_VARS=%s\\n\' "$extra_apply_vars" >> "$GITHUB_ENV"', workflow)
        self.assertIn('-e "${APPLY_VAR}"', workflow)
        self.assertIn('"${extra_var_args[@]}"', workflow)
        self.assertNotIn('-e "${apply_var}"', workflow)
        self.assertIn('user_args=(-e ansible_user=ci)', workflow)
        self.assertIn(
            'if [ "$playbook" = "ci-runner-key" ] && [ "$BOOTSTRAP_CI_RUNNER_KEY" = "true" ]; then',
            workflow,
        )
        self.assertIn('user_args=()', workflow)
        self.assertNotIn('${{ inputs.playbook }}_apply=true', workflow)

    def test_knowledge_mcp_does_not_usermod_shared_loop_home(self):
        tasks = yaml.safe_load((REPO / "ansible/roles/knowledge_mcp/tasks/apply.yml").read_text())
        user_task = next(task for task in tasks if task["name"] == "Ensure Knowledge MCP user exists")
        user_args = user_task["ansible.builtin.user"]

        self.assertEqual(user_args["name"], "{{ knowledge_mcp_user }}")
        self.assertNotIn("home", user_args)
        self.assertEqual(user_args["create_home"], False)

    def test_knowledge_loop_uses_dedicated_vault_scope(self):
        workflow = (REPO / ".github/workflows/apply.yml").read_text()
        playbook = (REPO / "ansible/playbooks/engineering-loop.yml").read_text()
        env_template = (REPO / "ansible/roles/vault_agent/templates/knowledge-loop.env.ctmpl.j2").read_text()
        key_template = (REPO / "ansible/roles/vault_agent/templates/knowledge-loop-github-app-key.pem.ctmpl.j2").read_text()
        defaults = yaml.safe_load((REPO / "ansible/roles/knowledge_loop/defaults/main.yml").read_text())
        runner_policy = (REPO / "configs/vault/policies/github-runner.hcl").read_text()

        self.assertIn("role: knowledge_loop", playbook)
        self.assertIn("vault_agent_name: knowledge-loop", playbook)
        self.assertIn("VAULT_KNOWLEDGE_LOOP_WRAPPED_SECRET_ID", playbook)
        self.assertIn("Mint knowledge-loop Vault bootstrap", workflow)
        self.assertIn('auth/approle/role/knowledge-loop/role-id', workflow)
        self.assertIn('path "auth/approle/role/knowledge-loop/role-id"', runner_policy)
        self.assertIn('path "auth/approle/role/knowledge-loop/secret-id"', runner_policy)
        self.assertIn('secret "kv/data/knowledge-loop"', env_template)
        self.assertIn('secret "kv/data/knowledge-loop"', key_template)
        self.assertIn("OPENROUTER_API_KEY", env_template)
        self.assertNotIn('secret "kv/data/engineering-loop"', env_template)
        self.assertNotIn("ENGINEERING_LOOP_GITHUB", env_template)
        self.assertNotIn("kv/data/knowledge-loop", runner_policy)
        self.assertEqual(defaults["knowledge_loop_timer_enabled"], False)
        self.assertEqual(defaults["knowledge_loop_max_openrouter_calls_per_day"], 0)

    def test_agent_core_collector_uses_dedicated_vault_scope(self):
        workflow = (REPO / ".github/workflows/apply.yml").read_text()
        playbook = (REPO / "ansible/playbooks/engineering-loop.yml").read_text()
        env_template = (
            REPO / "ansible/roles/vault_agent/templates/agent-core-collector.env.ctmpl.j2"
        ).read_text()
        collector_policy = (REPO / "configs/vault/policies/agent-core-collector.hcl").read_text()
        runner_policy = (REPO / "configs/vault/policies/github-runner.hcl").read_text()
        host_vars = yaml.safe_load((REPO / "ansible/inventory/host_vars/loop.yml").read_text())

        self.assertIn("role: agent_core_collector", playbook)
        self.assertIn("vault_agent_name: agent-core-collector", playbook)
        self.assertIn("VAULT_AGENT_CORE_COLLECTOR_WRAPPED_SECRET_ID", playbook)
        self.assertIn("Mint agent-core-collector Vault bootstrap", workflow)
        self.assertIn("auth/approle/role/agent-core-collector/role-id", workflow)
        self.assertIn('path "auth/approle/role/agent-core-collector/role-id"', runner_policy)
        self.assertIn('path "auth/approle/role/agent-core-collector/secret-id"', runner_policy)
        self.assertIn('secret "kv/data/agent-core-collector"', env_template)
        self.assertIn('path "kv/data/agent-core-collector"', collector_policy)
        self.assertNotIn("kv/data/agent-core-collector", runner_policy)
        self.assertRegex(str(host_vars["agent_core_collector_version"]), r"^[0-9a-f]{40}$")
        self.assertEqual(host_vars["agent_core_collector_bind"], "{{ peers.loop.ipv6 }}")
        self.assertEqual(host_vars["agent_core_collector_port"], 8770)

    def test_reliability_governor_is_managed_with_safe_default_and_loop_enabled(self):
        defaults = yaml.safe_load((REPO / "ansible/roles/engineering_loop/defaults/main.yml").read_text())
        host_vars = yaml.safe_load((REPO / "ansible/inventory/host_vars/loop.yml").read_text())
        apply_tasks = (REPO / "ansible/roles/engineering_loop/tasks/apply.yml").read_text()
        validate_tasks = (REPO / "ansible/roles/engineering_loop/tasks/main.yml").read_text()
        handlers = (REPO / "ansible/roles/engineering_loop/handlers/main.yml").read_text()
        wrapper = (
            REPO / "ansible/roles/engineering_loop/templates/run-reliability-governor.sh.j2"
        ).read_text()
        service = (
            REPO / "ansible/roles/engineering_loop/templates/hyrule-reliability-governor.service.j2"
        ).read_text()
        timer = (
            REPO / "ansible/roles/engineering_loop/templates/hyrule-reliability-governor.timer.j2"
        ).read_text()
        runbook = (REPO / "docs/runbooks/bootstrap-engineering-loop-vault.md").read_text()

        self.assertEqual(defaults["engineering_loop_governor_timer_enabled"], False)
        self.assertEqual(host_vars["engineering_loop_governor_timer_enabled"], True)
        self.assertEqual(
            defaults["engineering_loop_governor_state_dir"],
            "{{ engineering_loop_state_dir }}/reliability-governor",
        )
        self.assertEqual(len(defaults["engineering_loop_governor_repos"]), 8)
        self.assertEqual(defaults["engineering_loop_governor_timer_calendar"], "*:0/15")
        self.assertRegex(str(host_vars["engineering_loop_version"]), r"^[0-9a-f]{40}$")

        self.assertIn("Install Reliability Governor wrapper", apply_tasks)
        self.assertIn("Install Reliability Governor systemd service", apply_tasks)
        self.assertIn("Install Reliability Governor systemd timer", apply_tasks)
        self.assertIn("Set Reliability Governor timer state", apply_tasks)
        self.assertIn("engineering_loop_governor_state_dir.startswith('/')", validate_tasks)
        self.assertIn("engineering_loop_governor_limit | int <= 20", validate_tasks)
        self.assertIn("restart reliability-governor timer", handlers)

        self.assertIn("args=(reliability-governor --once)", wrapper)
        self.assertIn("--registry \"{{ engineering_loop_install_dir }}/configs/loop/capability-registry.yml\"", wrapper)
        self.assertIn("--state-dir-path \"{{ engineering_loop_governor_state_dir }}\"", wrapper)
        self.assertIn("--knowledge-mcp-url \"{{ engineering_loop_knowledge_mcp_url }}\"", wrapper)
        self.assertIn('exec "$loop_bin" "${args[@]}" "$@"', wrapper)

        self.assertIn("ExecStart={{ engineering_loop_governor_wrapper_path }}", service)
        self.assertIn("SyslogIdentifier=reliability-governor", service)
        self.assertIn("EnvironmentFile={{ engineering_loop_env_file }}", service)
        self.assertIn("hyrule-knowledge-mcp.service", service)
        self.assertIn("Unit=hyrule-reliability-governor.service", timer)
        self.assertIn("OnCalendar={{ engineering_loop_governor_timer_calendar }}", timer)
        self.assertIn("run-reliability-governor --dry-run", runbook)

    def test_agentic_observatory_uses_dedicated_vault_scope(self):
        workflow = (REPO / ".github/workflows/apply.yml").read_text()
        playbook = (REPO / "ansible/playbooks/engineering-loop.yml").read_text()
        env_template = (
            REPO / "ansible/roles/vault_agent/templates/agentic-observatory.env.ctmpl.j2"
        ).read_text()
        policy = (REPO / "configs/vault/policies/agentic-observatory.hcl").read_text()
        runner_policy = (REPO / "configs/vault/policies/github-runner.hcl").read_text()
        apply_tasks = (REPO / "ansible/roles/agentic_observatory/tasks/apply.yml").read_text()
        service_template = (
            REPO / "ansible/roles/agentic_observatory/templates/agentic-observatory.service.j2"
        ).read_text()
        host_vars = yaml.safe_load((REPO / "ansible/inventory/host_vars/loop.yml").read_text())

        self.assertIn("Ensure Agentic Observatory group exists before Vault Agent", playbook)
        self.assertNotIn("when: agentic_observatory_apply | default(false) | bool", playbook)
        self.assertIn("role: agentic_observatory", playbook)
        self.assertIn("vault_agent_name: agentic-observatory", playbook)
        self.assertIn("VAULT_AGENTIC_OBSERVATORY_WRAPPED_SECRET_ID", playbook)
        self.assertIn("Mint agentic-observatory Vault bootstrap", workflow)
        self.assertIn("auth/approle/role/agentic-observatory/role-id", workflow)
        self.assertIn('path "auth/approle/role/agentic-observatory/role-id"', runner_policy)
        self.assertIn('path "auth/approle/role/agentic-observatory/secret-id"', runner_policy)
        self.assertIn('secret "kv/data/agentic-observatory"', env_template)
        self.assertIn('path "kv/data/agentic-observatory"', policy)
        self.assertNotIn("kv/data/agentic-observatory", runner_policy)
        noc_env_template = (
            REPO / "ansible/roles/vault_agent/templates/noc-agent.env.ctmpl.j2"
        ).read_text()
        self.assertIn("NOC_LOOP_CONSOLE_SECRET", noc_env_template)
        self.assertIn("Install temporary GitHub netrc", apply_tasks)
        self.assertIn("GIT_TERMINAL_PROMPT", apply_tasks)
        self.assertIn("OBSERVATORY_GITHUB_TOKEN", apply_tasks)
        self.assertIn("ReadWritePaths={{ agentic_observatory_state_dir }}", service_template)
        self.assertNotIn(
            "ReadWritePaths={{ agentic_observatory_state_dir }} {{ agentic_observatory_install_dir }}",
            service_template,
        )
        self.assertRegex(str(host_vars["agentic_observatory_version"]), r"^[0-9a-f]{40}$")
        self.assertEqual(host_vars["agentic_observatory_port"], 8780)
        # Stage 1 live: writes on, low-risk case actions only. The gated
        # revision was deployed first (PR #329) before this flip. Expanding the
        # allowlist beyond feedback,ack must be a deliberate change that also
        # updates this guardrail.
        self.assertEqual(host_vars["agentic_observatory_read_only"], False)
        self.assertEqual(host_vars["agentic_observatory_actions_enabled"], True)
        self.assertEqual(host_vars["agentic_observatory_enabled_actions"], "feedback,ack")
        runbook = (REPO / "docs/runbooks/bootstrap-agentic-observatory-vault.md").read_text()
        self.assertIn(
            "vault policy write github-runner configs/vault/policies/github-runner.hcl",
            runbook,
        )
        self.assertIn("required for the private runtime checkout", runbook)

    def test_knowledge_loop_checkout_is_pinned_and_runner_policy_documented(self):
        host_vars = yaml.safe_load((REPO / "ansible/inventory/host_vars/loop.yml").read_text())
        # apply.yml forces knowledge_loop_apply for engineering-loop, so the loop
        # checkout must be a reviewed 40-char commit, never floating `main`. The
        # live host may enable the reviewed daily canary, but role defaults remain off.
        self.assertRegex(str(host_vars["knowledge_loop_version"]), r"^[0-9a-f]{40}$")
        self.assertEqual(host_vars["knowledge_loop_timer_enabled"], True)
        self.assertEqual(host_vars["knowledge_loop_max_openrouter_calls_per_day"], 0)
        self.assertEqual(host_vars["knowledge_loop_max_prs_per_day"], 1)
        self.assertEqual(host_vars["knowledge_loop_agent_core_trace_enabled"], True)
        self.assertIn("/v1/trace", host_vars["knowledge_loop_agent_core_trace_collector_url"])

        runbook = (REPO / "docs/runbooks/bootstrap-knowledge-loop-vault.md").read_text()
        # The runner needs the refreshed github-runner policy before the first apply
        # mints the knowledge-loop SecretID, or the apply fails permission denied.
        self.assertIn(
            "vault policy write github-runner configs/vault/policies/github-runner.hcl",
            runbook,
        )

    def test_knowledge_loop_runs_in_workspace_not_pinned_install_dir(self):
        defaults = yaml.safe_load((REPO / "ansible/roles/knowledge_loop/defaults/main.yml").read_text())
        run_loop = (REPO / "ansible/roles/knowledge_loop/templates/run-loop.sh.j2").read_text()
        service = (REPO / "ansible/roles/knowledge_loop/templates/hyrule-knowledge-loop.service.j2").read_text()
        apply = (REPO / "ansible/roles/knowledge_loop/tasks/apply.yml").read_text()
        apply_tasks = yaml.safe_load(apply)

        # The mutable repo clone lives under the state dir, separate from install_dir.
        self.assertEqual(defaults["knowledge_loop_workspace_dir"], "{{ knowledge_loop_state_dir }}/workspace")
        self.assertEqual(defaults["knowledge_loop_repo_workspace"], "{{ knowledge_loop_workspace_dir }}/knowledge")

        # The loop mutates the workspace clone, not the pinned runtime checkout, but
        # still runs the CLI from the install_dir venv.
        self.assertIn("--repo-path {{ knowledge_loop_repo_workspace }}", run_loop)
        self.assertNotIn("--repo-path {{ knowledge_loop_install_dir }}", run_loop)
        self.assertIn("{{ knowledge_loop_install_dir }}/.venv/bin/hyrule-knowledge", run_loop)

        # install_dir stays read-only at runtime; only the state dir is writable.
        self.assertIn("ReadWritePaths={{ knowledge_loop_state_dir }}", service)
        self.assertNotIn("ReadWritePaths={{ knowledge_loop_install_dir }}", service)

        # apply clones the Knowledge repo into the workspace for loop runs.
        self.assertIn('dest: "{{ knowledge_loop_repo_workspace }}"', apply)
        runtime_checkout = next(task for task in apply_tasks if task.get("name") == "Checkout Knowledge Loop runtime")
        workspace_stat = next(task for task in apply_tasks if task.get("name") == "Check existing Knowledge repo workspace checkout")
        workspace_clean = next(task for task in apply_tasks if task.get("name") == "Clean untracked Knowledge repo workspace artifacts")
        workspace_checkout = next(task for task in apply_tasks if task.get("name") == "Checkout Knowledge repo workspace for loop runs")
        self.assertLess(apply_tasks.index(workspace_clean), apply_tasks.index(workspace_checkout))
        self.assertEqual(runtime_checkout["ansible.builtin.git"].get("force", False), False)
        self.assertEqual(workspace_stat["ansible.builtin.stat"]["path"], "{{ knowledge_loop_repo_workspace }}/.git")
        self.assertEqual(workspace_clean["ansible.builtin.command"], "git clean -fdx")
        self.assertEqual(workspace_clean["args"]["chdir"], "{{ knowledge_loop_repo_workspace }}")
        self.assertEqual(workspace_clean["when"], "knowledge_loop_workspace_git_dir.stat.exists")
        self.assertEqual(workspace_checkout["ansible.builtin.git"].get("force", False), True)

    def test_knowledge_loop_timer_starts_only_after_secrets_render(self):
        apply = (REPO / "ansible/roles/knowledge_loop/tasks/apply.yml").read_text()
        handlers = (REPO / "ansible/roles/knowledge_loop/handlers/main.yml").read_text()
        # The role runs before the knowledge-loop vault_agent; with Persistent=true a
        # premature start would fire the service with no env file / key. Gate both the
        # start task and the restart handler on the rendered secrets existing.
        self.assertIn("Check Knowledge Loop runtime secrets are rendered", apply)
        self.assertIn("knowledge_loop_secret_files.results", apply)
        self.assertIn("map(attribute='stat.exists') | min", apply)
        self.assertIn("knowledge_loop_secret_files.results | map(attribute='stat.exists') | min", handlers)

    def test_vault_agent_restarts_in_role_not_via_shared_handler(self):
        handlers = (REPO / "ansible/roles/vault_agent/handlers/main.yml").read_text()
        tasks = (REPO / "ansible/roles/vault_agent/tasks/main.yml").read_text()
        # engineering-loop.yml includes vault_agent twice (engineering-loop +
        # knowledge-loop). A handler name (shared or templated) is resolved at play
        # load and could restart the wrong instance, so the restart is done in-role
        # where vault_agent_name binds at task-execution time.
        self.assertNotIn("notify: restart vault agent", tasks)
        self.assertNotIn("- name: restart vault agent", handlers)
        self.assertIn("Enable and (re)start Vault Agent", tasks)
        self.assertIn("'restarted'", tasks)
        self.assertIn("vault_agent_config_state is changed", tasks)

    def test_vault_agent_allows_no_restart_steady_state(self):
        tasks = yaml.safe_load((REPO / "ansible/roles/vault_agent/tasks/main.yml").read_text())
        task_text = (REPO / "ansible/roles/vault_agent/tasks/main.yml").read_text()

        self.assertIsNotNone(_task_by_name(tasks, "Check for existing Vault Agent rendered destinations"))
        self.assertIsNotNone(_task_by_name(tasks, "Check existing Vault Agent service state"))
        self.assertIsNotNone(_task_by_name(tasks, "Resolve Vault Agent steady-state facts"))

        self.assertIn("vault_agent_has_bootstrap_material", task_text)
        self.assertIn("vault_agent_has_running_rendered_state", task_text)
        self.assertIn("vault_agent_restart_needed", task_text)
        self.assertIn("and not (vault_agent_restart_needed | bool)", task_text)

        resolve_task = _task_by_name(tasks, "Resolve Vault Agent steady-state facts")
        self.assertNotIn(
            "vault_agent_existing_token_sink",
            resolve_task["set_fact"]["vault_agent_has_bootstrap_material"],
        )
        assert_task = _task_by_name(tasks, "Assert Vault AppRole bootstrap credentials are present")
        self.assertIn(
            "not sufficient restart bootstrap material",
            assert_task["assert"]["fail_msg"],
        )

        restart_task = _task_by_name(tasks, "Enable and (re)start Vault Agent")
        self.assertIn(
            "vault_agent_has_bootstrap_material | bool or vault_agent_has_running_rendered_state | bool",
            restart_task["when"],
        )
        self.assertIn(
            "vault_agent_restart_needed | bool and vault_agent_has_bootstrap_material | bool",
            restart_task["systemd"]["state"],
        )

    def test_vault_agent_preserves_wrapped_approle_mode_on_repeat_apply(self):
        tasks = yaml.safe_load((REPO / "ansible/roles/vault_agent/tasks/main.yml").read_text())
        names = [task["name"] for task in tasks]
        template = (REPO / "ansible/roles/vault_agent/templates/vault-agent.hcl.j2").read_text()

        check_index = names.index("Check existing Vault Agent configuration")
        read_index = names.index("Read existing Vault Agent response wrapping path")
        resolve_index = names.index("Resolve Vault Agent response wrapping path")
        render_index = names.index("Render Vault Agent configuration")
        self.assertLess(check_index, read_index)
        self.assertLess(read_index, render_index)
        self.assertLess(resolve_index, render_index)

        read_task = _task_by_name(tasks, "Read existing Vault Agent response wrapping path")
        self.assertEqual(read_task["when"], "vault_agent_existing_config.stat.exists")
        self.assertIn("secret_id_response_wrapping_path", " ".join(read_task["command"]["argv"]))

        resolve_expr = _task_by_name(tasks, "Resolve Vault Agent response wrapping path")["set_fact"][
            "vault_agent_effective_secret_id_response_wrapping_path"
        ]
        self.assertIn("vault_agent_existing_response_wrapping_path.stdout", resolve_expr)
        self.assertIn("vault_agent_secret_id | length > 0", resolve_expr)
        self.assertIn("vault_agent_secret_id_response_wrapping_path | length == 0", resolve_expr)

        self.assertIn("vault_agent_effective_secret_id_response_wrapping_path", template)
        self.assertIn("secret_id_response_wrapping_path = \"{{ response_wrapping_path }}\"", template)

    def test_knowledge_loop_lets_vault_openrouter_budget_win(self):
        run_loop = (REPO / "ansible/roles/knowledge_loop/templates/run-loop.sh.j2").read_text()
        # The Vault-rendered budget must win, so the wrapper only passes the Ansible
        # default when the env var is unset (the CLI reads it as the argparse default).
        self.assertIn('if [ -z "${HYRULE_KNOWLEDGE_LOOP_MAX_OPENROUTER_CALLS_PER_DAY:-}" ]; then', run_loop)
        # the flag must not be passed unconditionally in the base argv
        self.assertNotIn(
            "--max-openrouter-calls-per-day {{ knowledge_loop_max_openrouter_calls_per_day }} \\",
            run_loop,
        )

    def test_cloud_apply_mints_wrapped_vault_bootstrap(self):
        workflow = (REPO / ".github/workflows/apply.yml").read_text()
        runner_policy = (REPO / "configs/vault/policies/github-runner.hcl").read_text()
        runner_template = (REPO / "ansible/roles/vault_agent/templates/github-runner.env.ctmpl.j2").read_text()

        self.assertIn("Mint hyrule-cloud Vault bootstrap", workflow)
        self.assertIn("inputs.playbook == 'cloud'", workflow)
        self.assertIn("!inputs.dry_run", workflow)
        self.assertIn('vault read -field=role_id auth/approle/role/hyrule-cloud/role-id', workflow)
        self.assertIn(
            'vault write -wrap-ttl=10m -field=wrapping_token -f auth/approle/role/hyrule-cloud/secret-id',
            workflow,
        )
        self.assertIn("is not readable by the runner user", workflow)
        self.assertIn("VAULT_HYRULE_CLOUD_WRAPPED_SECRET_ID", workflow)
        self.assertNotIn("sudo cat", workflow)

        self.assertIn('path "auth/approle/role/hyrule-cloud/role-id"', runner_policy)
        self.assertIn('path "auth/approle/role/hyrule-cloud/secret-id"', runner_policy)
        self.assertIn('capabilities = ["read"]', runner_policy)
        self.assertIn('capabilities = ["update"]', runner_policy)

        self.assertNotIn("kv/data/hyrule-cloud", runner_policy)
        self.assertNotIn("XCPNG_XO_TOKEN", runner_template)
        self.assertNotIn("VAULT_HYRULE_CLOUD_SECRET_ID", runner_template)

    def test_hyrule_cloud_flushes_vault_agent_before_render_waits(self):
        tasks = yaml.safe_load((REPO / "ansible/roles/hyrule_cloud/tasks/vault.yml").read_text())
        names = [task.get("name") for task in tasks]

        state_index = names.index("Capture current hyrule-cloud Vault Agent state")
        setup_index = names.index("Set up Vault Agent for hyrule-cloud secret delivery")
        flush_index = names.index("Flush handlers so Vault Agent uses updated AppRole/template inputs")
        wait_index = names.index("Wait for Vault Agent to render hyrule-cloud env file")

        state_task = _task_by_name(tasks, "Capture current hyrule-cloud Vault Agent state")
        self.assertEqual(state_task["register"], "hyrule_cloud_vault_agent_service")
        self.assertFalse(state_task["changed_when"])
        self.assertFalse(state_task["failed_when"])

        flush_task = _task_by_name(tasks, "Flush handlers so Vault Agent uses updated AppRole/template inputs")
        self.assertEqual(flush_task["ansible.builtin.meta"], "flush_handlers")
        self.assertEqual(
            flush_task["when"],
            'hyrule_cloud_vault_agent_service.status.ActiveState | default("inactive") == "active"',
        )
        self.assertLess(state_index, setup_index)
        self.assertLess(setup_index, flush_index)
        self.assertLess(flush_index, wait_index)

    def test_monero_wallet_rpc_restore_uses_json_helper(self):
        unit = (REPO / "configs/monero-wallet-rpc.service").read_text()
        helper = (REPO / "configs/hyrule-cloud-monero-restore-wallet").read_text()
        runtime_tasks = yaml.safe_load((REPO / "ansible/roles/hyrule_cloud/tasks/runtime.yml").read_text())
        names = [task.get("name") for task in runtime_tasks]

        helper_index = names.index("Install Monero wallet RPC restore helper")
        service_index = names.index("Install /etc/systemd/system/monero-wallet-rpc.service")
        helper_task = _task_by_name(runtime_tasks, "Install Monero wallet RPC restore helper")

        self.assertIn("ExecStartPre=/usr/local/sbin/hyrule-cloud-monero-restore-wallet", unit)
        self.assertNotIn("--generate-from-view-key", unit)
        for unsupported_flag in ("--address", "--view-key", "--non-interactive"):
            self.assertNotRegex(unit, re.compile(rf"(?:^|\s){re.escape(unsupported_flag)}(?:\s|$)", re.MULTILINE))

        self.assertIn("--generate-from-json", helper)
        self.assertIn("json.dump(payload, handle)", helper)
        self.assertIn('"password": password_file.read_text().rstrip("\\n")', helper)
        self.assertIn('"scan_from_height": restore_height', helper)
        self.assertNotIn('"restore_height": restore_height', helper)
        self.assertIn('required_env("MONERO_WALLET_RPC_WALLET_ADDRESS")', helper)
        self.assertIn('required_env("MONERO_WALLET_RPC_VIEW_KEY")', helper)
        self.assertIn("os.unlink(restore_json)", helper)

        self.assertEqual(
            helper_task["ansible.builtin.copy"]["src"],
            "{{ playbook_dir }}/../../configs/hyrule-cloud-monero-restore-wallet",
        )
        self.assertEqual(helper_task["ansible.builtin.copy"]["dest"], "/usr/local/sbin/hyrule-cloud-monero-restore-wallet")
        self.assertEqual(helper_task["ansible.builtin.copy"]["mode"], "0755")
        self.assertEqual(helper_task["when"], "hyrule_cloud_monero_wallet_rpc_enabled | bool")
        self.assertLess(helper_index, service_index)

    def test_github_runner_vault_token_sink_is_runner_readable(self):
        defaults = yaml.safe_load((REPO / "ansible/roles/vault_agent/defaults/main.yml").read_text())
        vault_tasks = yaml.safe_load((REPO / "ansible/roles/vault_agent/tasks/main.yml").read_text())
        vault_template = (REPO / "ansible/roles/vault_agent/templates/vault-agent.hcl.j2").read_text()
        service_template = (REPO / "ansible/roles/vault_agent/templates/vault-agent.service.j2").read_text()
        runner_tasks = yaml.safe_load((REPO / "ansible/roles/github_runner/tasks/main.yml").read_text())

        self.assertEqual(defaults["vault_agent_service_group"], "root")
        self.assertEqual(defaults["vault_agent_token_sink_group"], "root")
        self.assertEqual(defaults["vault_agent_token_sink_mode"], "0600")
        self.assertEqual(defaults["vault_agent_run_dir_group"], "root")
        self.assertEqual(defaults["vault_agent_run_dir_mode"], "0750")
        self.assertIn("mode = {{ vault_agent_token_sink_mode }}", vault_template)
        self.assertIn("Group={{ vault_agent_service_group }}", service_template)
        self.assertIn("RuntimeDirectoryMode={{ vault_agent_run_dir_mode }}", service_template)

        token_permission_task = _task_by_name(vault_tasks, "Ensure Vault Agent token sink permissions")
        self.assertIsNotNone(token_permission_task)
        self.assertEqual(token_permission_task["file"]["group"], "{{ vault_agent_token_sink_group }}")
        self.assertEqual(token_permission_task["file"]["mode"], "{{ vault_agent_token_sink_mode }}")
        self.assertIn(
            "vault_agent_has_bootstrap_material | bool or vault_agent_existing_token_sink.stat.exists",
            token_permission_task["when"],
        )

        runner_vault_task = _task_by_name(runner_tasks, "Set up Vault Agent for runner secret delivery")
        self.assertIsNotNone(runner_vault_task)
        runner_vars = runner_vault_task["vars"]
        self.assertEqual(runner_vars["vault_agent_service_group"], "{{ github_runner_group }}")
        self.assertEqual(runner_vars["vault_agent_run_dir_group"], "{{ github_runner_group }}")
        self.assertEqual(runner_vars["vault_agent_run_dir_mode"], "2750")
        self.assertEqual(runner_vars["vault_agent_token_sink_group"], "{{ github_runner_group }}")
        self.assertEqual(runner_vars["vault_agent_token_sink_mode"], "0640")

    def test_app_roles_restart_deterministically_on_apply(self):
        for role in ("hyrule_cloud", "hyrule_web"):
            health_tasks = yaml.safe_load((REPO / "ansible/roles" / role / "tasks/health.yml").read_text())
            restart_task = _task_by_name(health_tasks, f"Restart {role.replace('_', '-')} (deterministic on every apply)")

            self.assertIsNotNone(restart_task, role)
            self.assertEqual(restart_task["ansible.builtin.systemd"]["state"], "restarted")

    def test_hyrule_cloud_runs_migrations_before_restart(self):
        health_tasks = yaml.safe_load((REPO / "ansible/roles/hyrule_cloud/tasks/health.yml").read_text())
        task_names = [task["name"] for task in health_tasks]
        migration_task = _task_by_name(health_tasks, "Run hyrule-cloud database migrations")

        self.assertIsNotNone(migration_task)
        self.assertLess(
            task_names.index("Run hyrule-cloud database migrations"),
            task_names.index("Restart hyrule-cloud (deterministic on every apply)"),
        )
        command = migration_task["ansible.builtin.command"]
        self.assertEqual(command["cmd"], "/usr/local/bin/uv run alembic upgrade head")
        self.assertEqual(command["chdir"], "{{ hyrule_cloud_install_dir }}")
        self.assertEqual(migration_task["environment"]["PYTHONPATH"], "{{ hyrule_cloud_install_dir }}")

    def test_noc_action_signing_secret_has_no_empty_fallback(self):
        vault_template = (REPO / "ansible/roles/vault_agent/templates/noc-agent.env.ctmpl.j2").read_text()
        noc_env = (REPO / "configs/noc-agent.env.j2").read_text()
        mcp_env = (REPO / "configs/hyrule-mcp.env.j2").read_text()
        vault_put = (REPO / "scripts/vault-put-noc-agent-secrets.sh").read_text()
        noc_service = (REPO / "configs/noc-agent.service").read_text()
        bot_service = (REPO / "configs/noc-agent-bot.service").read_text()
        mcp_service = (REPO / "configs/hyrule-mcp.service").read_text()

        for text in (vault_template, noc_env, mcp_env):
            self.assertNotIn('noc_approval_signing_secret ""', text)
            self.assertNotIn("noc_approval_signing_secret | default('')", text)

        self.assertIn("NOC_APPROVAL_SIGNING_SECRET is required", vault_put)
        self.assertNotIn("NOC_ACTION_ALLOWED_HOSTS:-noc,mon,cr1-nl1,cr1-de1", vault_put)
        self.assertNotIn("NOC_ACTION_ALLOWED_SERVICES:-node_exporter,noc-agent,noc-agent-bot,hyrule-mcp", vault_put)
        for text in (noc_service, bot_service):
            self.assertIn('^NOC_APPROVAL_SIGNING_SECRET=.{32,}$', text)
        for text in (noc_service, bot_service, mcp_service):
            self.assertIn('^HYRULE_MCP_ACTION_SIGNING_SECRET=.{32,}$', text)

    def test_noc_agent_model_defaults_come_from_toml_not_env(self):
        vault_template = (REPO / "ansible/roles/vault_agent/templates/noc-agent.env.ctmpl.j2").read_text()
        noc_env = (REPO / "configs/noc-agent.env.j2").read_text()
        vault_put = (REPO / "scripts/vault-put-noc-agent-secrets.sh").read_text()
        playbook = (REPO / "ansible/playbooks/noc.yml").read_text()

        for text in (vault_template, noc_env):
            self.assertNotIn("google-gla:gemini-3.1-pro-preview", text)
            self.assertNotIn("google-gla:gemini-2.5-flash", text)
            self.assertIn("OPENROUTER_API_KEY", text)
            self.assertIn("OPENROUTER_MANAGEMENT_API_KEY", text)

        self.assertIn("OPENROUTER_API_KEY is required", vault_put)
        self.assertIn('openrouter_api_key="${OPENROUTER_API_KEY}"', vault_put)
        self.assertIn("openrouter_api_key", playbook)
        self.assertIn("openrouter_management_api_key", playbook)

    def test_noc_agent_trace_sink_is_configured_in_both_env_backends(self):
        vault_template = (REPO / "ansible/roles/vault_agent/templates/noc-agent.env.ctmpl.j2").read_text()
        noc_env = (REPO / "configs/noc-agent.env.j2").read_text()
        defaults = yaml.safe_load((REPO / "ansible/roles/noc_agent/defaults/main.yml").read_text())
        host_vars = yaml.safe_load((REPO / "ansible/inventory/host_vars/noc.yml").read_text())

        for text in (vault_template, noc_env):
            self.assertIn("HYRULE_NOC_AGENT_CORE_TRACE=", text)
            self.assertIn("HYRULE_NOC_AGENT_CORE_TRACE_COLLECTOR_URL=", text)

        self.assertFalse(defaults["noc_agent_core_trace_enabled"])
        self.assertEqual(defaults["noc_agent_core_trace_collector_url"], "")
        self.assertTrue(host_vars["noc_agent_core_trace_enabled"])
        self.assertEqual(
            host_vars["noc_agent_core_trace_collector_url"],
            "http://[{{ peers.loop.ipv6 }}]:8770/v1/trace",
        )

    def test_freebsd_playbooks_can_opt_into_become(self):
        freebsd_vars = yaml.safe_load((REPO / "ansible/inventory/group_vars/freebsd.yml").read_text())

        self.assertNotIn("ansible_become", freebsd_vars)
        self.assertEqual(freebsd_vars["ansible_become_method"], "doas")

    def test_ci_runner_deploy_user_uses_portable_shell(self):
        defaults = yaml.safe_load((REPO / "ansible/roles/ci_runner_key/defaults/main.yml").read_text())

        self.assertEqual(defaults["ci_runner_user_shell"], "/bin/sh")

    def test_freebsd_router_inventory_uses_loopback_addresses(self):
        inventory = yaml.safe_load((REPO / "ansible/inventory/hosts.yml").read_text())
        freebsd_hosts = inventory["all"]["children"]["freebsd"]["hosts"]

        self.assertEqual(freebsd_hosts["cr1-nl1"]["ansible_host"], "2a0c:b641:b50::a")
        self.assertEqual(freebsd_hosts["cr1-de1"]["ansible_host"], "2a0c:b641:b50::b")

    def test_runner_known_hosts_is_seeded_without_controller_key_path(self):
        tasks = yaml.safe_load((REPO / "ansible/roles/github_runner/tasks/main.yml").read_text())

        seed_task = _task_by_name(tasks, "Seed runner known_hosts with the infra fleet host keys")
        self.assertIsNotNone(seed_task)
        self.assertNotIn("when", seed_task)

        ownership_task = _task_by_name(tasks, "Fix runner known_hosts ownership")
        self.assertIsNotNone(ownership_task)
        self.assertNotIn("when", ownership_task)

    def test_hyrule_cloud_policy_is_dedicated(self):
        policy = (REPO / "configs/vault/policies/hyrule-cloud.hcl").read_text()
        self.assertIn('path "kv/data/hyrule-cloud"', policy)
        self.assertNotIn("kv/data/ci-runner", policy)
        self.assertNotIn("kv/data/noc-agent", policy)

    def test_cloud_role_no_longer_renders_secret_env_from_ansible(self):
        role_text = "\n".join(path.read_text() for path in (REPO / "ansible/roles/hyrule_cloud/tasks").glob("*.yml"))
        self.assertNotIn("configs/hyrule-cloud.env.j2", role_text)
        self.assertNotRegex(role_text, re.compile(r"lookup\(['\"]env['\"],\s*['\"]XO_TOKEN['\"]"))

    def test_runner_unit_reaps_orphans_on_restart(self):
        # KillMode=process orphaned Runner.Listener on a mid-job restart: it held
        # the GitHub session (next start → SessionConflict crash-loop) and ran in a
        # torn-down PrivateTmp /tmp (mktemp failures). mixed reaps the whole cgroup.
        unit = (REPO / "ansible/roles/github_runner/templates/github-runner.service.j2").read_text()
        self.assertIn("KillMode=mixed", unit)
        self.assertNotIn("KillMode=process", unit)

    def test_runner_staging_unmount_removes_fstab_entry(self):
        # state: unmounted left the staging mountpoint in /etc/fstab, duplicating
        # the runner-home device entry and racing two mounts of /dev/xvdiN on boot.
        tasks = (REPO / "ansible/roles/github_runner/tasks/main.yml").read_text()
        self.assertNotRegex(tasks, re.compile(r"state:\s*unmounted"))

    def test_vault_agent_supports_response_wrapped_secret_id(self):
        hcl = (REPO / "ansible/roles/vault_agent/templates/vault-agent.hcl.j2").read_text()
        self.assertIn("secret_id_response_wrapping_path", hcl)
        self.assertIn("remove_secret_id_file_after_reading = true", hcl)

    def test_hyrule_mcp_users_can_read_systemd_journals(self):
        service = (REPO / "configs/hyrule-mcp.service").read_text()
        hyrule_mcp_tasks = yaml.safe_load((REPO / "ansible/roles/hyrule_mcp/tasks/main.yml").read_text())
        noc_mcp_key_tasks = yaml.safe_load((REPO / "ansible/roles/noc_mcp_key/tasks/main.yml").read_text())

        self.assertIn(f"SupplementaryGroups={JOURNAL_GROUP}", service)
        self.assertEqual(
            _task_by_name(hyrule_mcp_tasks, "Ensure systemd journal reader group exists")["group"]["name"],
            JOURNAL_GROUP,
        )
        self.assertIn(
            JOURNAL_GROUP,
            _groups_for(_task_by_name(hyrule_mcp_tasks, "Ensure noc-agent system user exists")["user"]["groups"]),
        )
        self.assertEqual(
            _task_by_name(noc_mcp_key_tasks, "Ensure systemd journal reader group exists")["group"]["name"],
            JOURNAL_GROUP,
        )
        self.assertIn(
            JOURNAL_GROUP,
            _groups_for(
                _task_by_name(noc_mcp_key_tasks, "Grant MCP SSH user read access to systemd journals")["user"]["groups"]
            ),
        )


def _task_by_name(tasks, name):
    for task in tasks:
        if task.get("name") == name:
            return task
    raise AssertionError(f"task not found: {name}")


def _groups_for(value):
    if isinstance(value, list):
        return {str(item) for item in value}
    return {part.strip() for part in str(value).replace(",", " ").split() if part.strip()}


def _triggers_on_pull_request(triggers):
    # `on:` may be a string ("pull_request"), a list, or a mapping
    # ({pull_request: {...}, push: {...}}); PyYAML also turns the bare key `on`
    # into the boolean True, which the caller resolves before passing here.
    if triggers is None:
        return False
    if isinstance(triggers, str):
        return triggers == "pull_request"
    if isinstance(triggers, dict):
        return "pull_request" in triggers
    if isinstance(triggers, (list, tuple, set)):
        return "pull_request" in triggers
    return False


if __name__ == "__main__":
    unittest.main()
