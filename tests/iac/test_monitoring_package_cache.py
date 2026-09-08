"""An unrelated repository outage must not block an installed exporter."""
import unittest
from pathlib import Path

import yaml
from jinja2.nativetypes import NativeEnvironment


class MonitoringPackageCacheTest(unittest.TestCase):
    def test_refresh_is_required_only_for_missing_exporter(self):
        root = Path(__file__).resolve().parents[2]
        tasks = yaml.safe_load((root / 'ansible/roles/monitoring/tasks/node_exporter.yml').read_text())
        install = next(t['apt'] for t in tasks if t['name'] == 'Install node_exporter (Debian)')
        for state, expected in (({'rc': 1, 'stdout': ''}, (True, 3600)), ({'rc': 0, 'stdout': 'installed'}, (False, 0)), ({'rc': 0, 'stdout': 'config-files'}, (True, 3600))):
            with self.subTest(state=state):
                context = {'monitoring_pkg': 'prometheus-node-exporter', 'monitoring_package_state': state}
                env = NativeEnvironment()
                actual = tuple(env.from_string(str(install[key])).render(context)
                               for key in ('update_cache', 'cache_valid_time'))
                # A positive cache_valid_time itself requests an update, even
                # when update_cache is false. Both must disable refresh.
                self.assertEqual(actual, expected)
