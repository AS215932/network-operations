"""An unrelated repository outage must not block an installed exporter."""
import unittest
from pathlib import Path

import yaml
from jinja2.nativetypes import NativeEnvironment


class MonitoringPackageCacheTest(unittest.TestCase):
    def test_refresh_is_required_only_for_missing_exporter(self):
        root = Path(__file__).resolve().parents[2]
        tasks = yaml.safe_load((root / 'ansible/roles/monitoring/tasks/node_exporter.yml').read_text())
        task = next(t for t in tasks if t['name'] == 'Install node_exporter (Debian)')
        install = task['apt']
        for state, expected in (({'rc': 1, 'stdout': ''}, (True, 3600)), ({'rc': 0, 'stdout': 'installed'}, (False, 0)), ({'rc': 0, 'stdout': 'config-files'}, (True, 3600))):
            with self.subTest(state=state):
                context = {'monitoring_pkg': 'prometheus-node-exporter', 'monitoring_package_state': state, 'ansible_os_family': 'Debian'}
                env = NativeEnvironment()
                actual = tuple(env.from_string(str(install[key])).render(context)
                               for key in ('update_cache', 'cache_valid_time'))
                # A positive cache_valid_time itself requests an update, even
                # when update_cache is false. Both must disable refresh.
                self.assertEqual(actual, expected)
                runs = all(env.from_string('{{ ' + condition + ' }}').render(context)
                           for condition in task['when'])
                # Even loading apt can bootstrap python3-apt and refresh indexes.
                self.assertEqual(runs, expected[0])
