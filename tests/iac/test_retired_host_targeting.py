"""Resolve real Ansible host patterns without connecting to infrastructure."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml
from ansible.inventory.manager import InventoryManager
from ansible.parsing.dataloader import DataLoader

REPO = Path(__file__).resolve().parents[2]


class RetiredHostTargetingTest(unittest.TestCase):
    def setUp(self):
        self.inventory = InventoryManager(
            loader=DataLoader(), sources=[str(REPO / 'ansible/inventory/hosts.yml')]
        )

    def test_routine_plays_preserve_active_hosts_and_exclude_retired(self):
        for name in ('firewall', 'networkd_resolved', 'monitoring', 'logs',
                     'ci-runner-key', 'noc_mcp_key'):
            plays = yaml.safe_load((REPO / f'ansible/playbooks/{name}.yml').read_text())
            for play in plays:
                pattern = play['hosts']
                old_hosts = {h.name for h in self.inventory.get_hosts(pattern.replace(':!retired', ''))}
                new_hosts = {h.name for h in self.inventory.get_hosts(pattern)}
                self.assertEqual(new_hosts, old_hosts - {'loop'}, name)
                self.assertNotIn('loop', new_hosts, name)

    def test_retirement_entrypoint_still_resolves_preserved_host(self):
        play = yaml.safe_load((REPO / 'ansible/playbooks/retire-loop.yml').read_text())[0]
        self.assertEqual([h.name for h in self.inventory.get_hosts(play['hosts'])], ['loop'])
        self.assertTrue(self.inventory.get_host('loop').vars.get('ansible_host'))

    def test_sweep_excludes_retired_even_with_explicit_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / 'ansible-playbook'
            executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            executable.chmod(0o755)
            result = subprocess.run(
                ['bash', str(REPO / 'scripts/ci/check-drift.sh'),
                 str(root / 'logs'), 'firewall'],
                env={**os.environ, 'PATH': f'{root}:' + os.environ['PATH'],
                     'CHECK_DRIFT_LIMIT': 'loop:rtr'},
                text=True, capture_output=True, check=True,
            )
            self.assertIn('--limit\nloop:rtr:!retired\n', result.stdout)

    def test_narrowed_verification_cannot_select_retired_host(self):
        for pattern in ('all:!ci-pr:!retired', 'loop:rtr:!retired', 'loop:!retired'):
            self.assertNotIn('loop', {h.name for h in self.inventory.get_hosts(pattern)})
