"""Resolve real Ansible host patterns without connecting to infrastructure."""
import json
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
        for name in ('firewall', 'networkd_resolved', 'logs',
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
                 str(root / 'logs'), 'firewall', 'monitoring'],
                env={**os.environ, 'PATH': f'{root}:' + os.environ['PATH'],
                     'CHECK_DRIFT_LIMIT': 'loop:rtr'},
                text=True, capture_output=True, check=True,
            )
            self.assertIn('--limit\nloop:rtr:!retired\n', result.stdout)
            self.assertIn('--limit\nloop:rtr\n', result.stdout)

    def test_narrowed_verification_cannot_select_retired_host(self):
        for pattern in ('all:!ci-pr:!retired', 'loop:rtr:!retired', 'loop:!retired'):
            self.assertNotIn('loop', {h.name for h in self.inventory.get_hosts(pattern)})

    def test_ci_host_key_seeding_skips_retired_inventory(self):
        inventory = {
            '_meta': {'hostvars': {
                'loop': {'ansible_host': '2a0c:b641:b50:2::f0'},
                'rtr': {'ansible_host': '2a0c:b641:b50:2::1'},
            }},
            'retired': {'hosts': ['loop']},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'ansible-inventory').write_text(
                '#!/bin/sh\nprintf \'%s\\n\' ' + repr(json.dumps(inventory)) + '\n'
            )
            (root / 'ssh-keygen').write_text('#!/bin/sh\nexit 1\n')
            (root / 'ssh-keyscan').write_text(
                '#!/bin/sh\ntarget=""\nfor arg in "$@"; do target="$arg"; done\n'
                'printf \'%s\\n\' "$target" >> "$SCAN_LOG"\n'
                'printf \'%s ssh-ed25519 AAAATEST\\n\' "$target"\n'
            )
            for executable in ('ansible-inventory', 'ssh-keygen', 'ssh-keyscan'):
                (root / executable).chmod(0o755)
            scan_log = root / 'scans'
            known_hosts = root / 'known_hosts'
            result = subprocess.run(
                ['bash', str(REPO / 'scripts/ci/seed-missing-host-keys.sh')],
                env={
                    **os.environ,
                    'PATH': f'{root}:' + os.environ['PATH'],
                    'KNOWN_HOSTS_FILE': str(known_hosts),
                    'SCAN_LOG': str(scan_log),
                },
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(scan_log.read_text().splitlines(), ['2a0c:b641:b50:2::1'])
            self.assertNotIn('2a0c:b641:b50:2::f0', known_hosts.read_text())
            self.assertIn('1 new host', result.stdout)

    def test_runner_role_key_seeding_filters_retired_hosts_and_peers(self):
        tasks = yaml.safe_load(
            (REPO / 'ansible/roles/github_runner/tasks/main.yml').read_text()
        )
        seed = next(
            task for task in tasks
            if task.get('name') == 'Seed runner known_hosts with the infra fleet host keys'
        )
        command = seed['shell']['cmd']
        self.assertIn("groups['retired'] | default([])", command)
        self.assertIn('host not in retired_hosts', command)
        self.assertIn('for peer_name, p in peers.items()', command)
        self.assertIn('peer_name not in retired_hosts', command)

    def test_retired_host_has_no_current_network_flows(self):
        flows = yaml.safe_load(
            (REPO / 'ansible/inventory/network_flows.yml').read_text()
        )
        self.assertIn('loop', flows['all_excludes'])
        for flow in flows['cross_cutting_flows']:
            self.assertNotEqual(flow['from'], 'loop', flow)
            self.assertNotEqual(flow['to'], 'loop', flow)
        self.assertIn('retired loop', flows['network_externals']['all-infra']['note'])
        self.assertIn('excluding retired loop', flows['network_externals']['all-linux']['note'])

        for host in ('log', 'mon', 'vault'):
            text = (REPO / f'ansible/inventory/host_vars/{host}.yml').read_text()
            self.assertNotIn('peers.loop', text, host)
        rtr_text = (REPO / 'ansible/inventory/host_vars/rtr.yml').read_text()
        self.assertNotIn('loop_docker_subnet', rtr_text)

        loop_vars = yaml.safe_load(
            (REPO / 'ansible/inventory/host_vars/loop.yml').read_text()
        )
        self.assertTrue(loop_vars['loop_retired'])
        self.assertEqual(loop_vars['firewall_extra_rules'], [])
        self.assertEqual(loop_vars['firewall_forward_extra_raw_nft'], '')
        self.assertEqual(loop_vars['network_flows_outbound'], [])
        self.assertIn('Retired', loop_vars['host_meta']['role'])
        rendered = (REPO / 'docs/network-flows.md').read_text()
        loop_section = rendered.split('### loop ', 1)[1].split('\n### ', 1)[0]
        self.assertIn('No current inbound flow is modelled', loop_section)
        self.assertIn('No current outbound flow is modelled', loop_section)
        self.assertNotIn('SSH-only', loop_section)
        dom0_section = rendered.split('### dom0 ', 1)[1].split('\n### ', 1)[0]
        self.assertNotIn('No current inbound flow is modelled', dom0_section)
        self.assertNotIn('No current outbound flow is modelled', dom0_section)


    def test_monitoring_retains_tombstones_but_never_contacts_retired_host(self):
        play = yaml.safe_load((REPO / 'ansible/playbooks/monitoring.yml').read_text())[0]
        self.assertIn('loop', {h.name for h in self.inventory.get_hosts(play['hosts'])})
        tasks = yaml.safe_load((REPO / 'ansible/roles/monitoring/tasks/main.yml').read_text())
        for task in tasks:
            conditions = task.get('when', [])
            if 'monitoring_apply | default(false) | bool' in conditions:
                self.assertIn('not (monitoring_retired | default(false) | bool)', conditions, task['name'])
        registration = next(t for t in tasks if t.get('include_tasks') == 'register.yml')
        self.assertEqual(registration['when'], 'monitoring_register | bool')
