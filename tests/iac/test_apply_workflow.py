import re
import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]

# Cross-cutting roles that every playbook pulls in. They take their gate as an
# explicit role var from the playbook (firewall_apply: "{{ <thing>_apply }}"),
# never from the workflow's apply_var, so they say nothing about which gate the
# workflow must pass.
SHARED_ROLES = {"firewall", "monitoring", "logs"}

APPLY_WORKFLOW = REPO / ".github/workflows/apply.yml"


def _playbook_choices(workflow_text):
    options = re.search(r"options:\n((?:\s+- \S+\n)+)", workflow_text).group(1)
    return [line.strip()[2:] for line in options.strip().splitlines()]


def _explicit_gate_cases(workflow_text):
    """playbook -> apply_var, for the explicit arms of the gate case statement."""
    block = workflow_text.split('case "$playbook" in', 1)[1].split("esac", 1)[0]
    return dict(
        re.findall(
            r'^\s+([a-z0-9_-]+)\)\n(?:\s+#.*\n)*\s+apply_var="([a-z0-9_]+)=true"',
            block,
            re.M,
        )
    )


def _playbook_roles(playbook_path):
    roles = set()
    for play in yaml.safe_load(playbook_path.read_text()) or []:
        for role in play.get("roles") or []:
            roles.add(role["role"] if isinstance(role, dict) else role)
    return roles


def _role_gate_variables(role):
    tasks = REPO / f"ansible/roles/{role}/tasks/main.yml"
    if not tasks.exists():
        return set()
    return set(re.findall(r"(\w+_apply)\s*\|\s*bool", tasks.read_text()))


class ApplyWorkflowTest(unittest.TestCase):
    def test_apply_run_and_job_names_include_target(self):
        workflow = yaml.safe_load(APPLY_WORKFLOW.read_text())

        expected = "${{ inputs.dry_run == true && 'Dry-run' || 'Apply' }} playbook ${{ inputs.playbook }} to target(s) ${{ inputs.limit }}"
        self.assertEqual(workflow["run-name"], expected)
        self.assertEqual(workflow["jobs"]["apply"]["name"], expected)

    def test_every_playbook_resolves_the_gate_its_roles_actually_read(self):
        # The gate case statement falls through to `${playbook//-/_}_apply` for
        # anything without an explicit arm. When a role's gate variable is not
        # simply its playbook name — hyrule_prober_apply for `prober`, mail_apply
        # for `mail_openbsd`, soc_agent_apply for `soc` — that default resolves a
        # variable nothing reads. The apply run then goes green having deployed
        # NOTHING, which is the worst failure mode a deploy workflow has: it looks
        # like a successful production change. `prober` and `mail_openbsd` both
        # shipped in that state until 2026-07-28.
        workflow_text = APPLY_WORKFLOW.read_text()
        explicit = _explicit_gate_cases(workflow_text)

        for playbook in _playbook_choices(workflow_text):
            path = REPO / f"ansible/playbooks/{playbook}.yml"
            if not path.exists():
                continue

            resolved = explicit.get(playbook, playbook.replace("-", "_") + "_apply")
            gates = set()
            for role in _playbook_roles(path) - SHARED_ROLES:
                gates |= _role_gate_variables(role)

            if not gates:
                # Playbook applies unconditionally under --tags apply; there is
                # no gate for the workflow to get wrong.
                continue

            self.assertIn(
                resolved,
                gates,
                f"apply.yml resolves {resolved!r} for playbook {playbook!r}, but its "
                f"roles gate on {sorted(gates)} — the apply would run green and "
                f"deploy nothing. Add an explicit case to 'Resolve apply gate variable'.",
            )


    def test_drift_sweep_playbooks_resolve_their_own_gate(self):
        # check-drift.sh and post-merge-apply.yml both derive the gate as a bare
        # `${playbook}_apply=true` with no case mapping at all, so the sweep can
        # only ever carry playbooks whose gate IS their name. Today's list
        # happens to satisfy that; adding `prober`, `cloud`, `soc` or
        # `mail_openbsd` to it would silently check-and-apply nothing — and in
        # the post-merge path that means an approved production auto-apply that
        # changes nothing while reporting success.
        sweep = (REPO / "scripts/ci/check-drift.sh").read_text()
        default_playbooks = re.search(
            r"default_playbooks=\(([^)]*)\)", sweep
        ).group(1).split()

        self.assertIn(
            '-e "${playbook}_apply=true"',
            sweep,
            "if check-drift.sh grows a gate mapping, this test needs to read it",
        )

        for playbook in default_playbooks:
            path = REPO / f"ansible/playbooks/{playbook}.yml"
            self.assertTrue(path.exists(), f"drift sweep names a missing playbook: {playbook}")

            gates = set()
            for role in _playbook_roles(path) - SHARED_ROLES:
                gates |= _role_gate_variables(role)
            if not gates:
                continue

            self.assertIn(
                playbook.replace("-", "_") + "_apply",
                gates,
                f"drift sweep runs {playbook!r} with '{playbook}_apply=true', but its "
                f"roles gate on {sorted(gates)} — the sweep would report no drift and "
                f"the post-merge auto-apply would change nothing.",
            )


if __name__ == "__main__":
    unittest.main()
