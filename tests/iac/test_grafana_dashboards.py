"""Contracts for the file-provisioned Grafana dashboards on mon.

configs/mon/grafana-dashboards/*.json is published verbatim to
/var/lib/grafana/dashboards by ansible/roles/monitoring/tasks/grafana.yml. The
file provisioner is silent about dashboards it cannot load — a bad uid or an
unresolved ${DS_*} import placeholder just means the panels render "Datasource
not found" with nothing failing — so the checks that would otherwise happen at
import time have to happen here.
"""

import json
import re
import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]
DASHBOARD_DIR = REPO / "configs" / "mon" / "grafana-dashboards"
MONITORING_DEFAULTS = REPO / "ansible" / "roles" / "monitoring" / "defaults" / "main.yml"

# Panel types that render without querying a datasource. Deliberately an
# allowlist of known-inert types rather than "anything without targets" — the
# latter would excuse a timeseries panel that forgot its query, which is the
# defect these contracts exist to catch.
NON_QUERY_PANEL_TYPES = frozenset(
    {"row", "text", "dashlist", "news", "welcome", "alertlist", "annolist"}
)

# `uid: "{{ monitoring_grafana_prometheus_uid }}"` in the role defaults — a
# whole-value reference to another default, which is all this needs to resolve.
_VAR_REFERENCE = re.compile(r"^\{\{\s*([a-zA-Z0-9_]+)\s*\}\}$")


def _dashboards():
    return sorted(DASHBOARD_DIR.glob("*.json"))


def _resolve(value, defaults):
    """Resolve a bare `{{ var }}` reference against the role defaults."""
    match = _VAR_REFERENCE.match(str(value))
    return defaults[match.group(1)] if match else value


def _provisioned_uids(defaults):
    """The uids the role publishes, straight from its datasource list.

    Read from monitoring_grafana_datasources rather than named one by one, so
    adding a third datasource to the role does not also require editing a
    hidden allowlist here to keep dashboards referencing it legal.
    """
    return {_resolve(ds["uid"], defaults) for ds in defaults["monitoring_grafana_datasources"]}


def _query_panels(dashboard):
    """Every panel that actually runs a query, rows flattened away.

    A row is a layout container: it legitimately has no datasource and no
    targets, and when collapsed it carries its children in its own `panels`
    key where a naive top-level loop would never see them. Yield the children
    and drop the row itself, so a nested panel cannot smuggle in a bad
    datasource reference. Other inert panel types (text, dashlist, …) are
    dropped outright — they have no children and no query.
    """
    for panel in dashboard.get("panels", []):
        if panel.get("type") == "row":
            yield from (
                child
                for child in panel.get("panels", [])
                if child.get("type") not in NON_QUERY_PANEL_TYPES
            )
            continue
        if panel.get("type") in NON_QUERY_PANEL_TYPES:
            continue
        yield panel


class GrafanaDashboardContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with MONITORING_DEFAULTS.open(encoding="utf-8") as handle:
            cls.defaults = yaml.safe_load(handle)
        cls.datasource_uids = _provisioned_uids(cls.defaults)

    def test_dashboard_dir_is_not_empty(self):
        self.assertTrue(_dashboards(), f"no dashboards under {DASHBOARD_DIR}")

    def test_dashboards_are_valid_json_with_uid_and_title(self):
        uids = {}
        for path in _dashboards():
            with self.subTest(dashboard=path.name):
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertTrue(data.get("uid"), "dashboard needs a stable uid")
                self.assertTrue(data.get("title"), "dashboard needs a title")
                # Provisioning refuses a dashboard carrying an id from another
                # Grafana's database.
                self.assertIsNone(data.get("id"), "strip `id` before committing")
                self.assertNotIn(
                    data["uid"], uids, f"uid collides with {uids.get(data['uid'])}"
                )
                uids[data["uid"]] = path.name

    def test_no_import_wizard_placeholders_remain(self):
        """__inputs/${DS_*} only resolve in the UI import flow, not in files."""
        for path in _dashboards():
            with self.subTest(dashboard=path.name):
                raw = path.read_text(encoding="utf-8")
                self.assertNotIn("${DS_", raw)
                self.assertNotIn("__inputs", raw)

    def test_panel_datasources_match_provisioned_uids(self):
        for path in _dashboards():
            data = json.loads(path.read_text(encoding="utf-8"))
            for panel in _query_panels(data):
                with self.subTest(dashboard=path.name, panel=panel.get("title")):
                    datasource = panel.get("datasource")
                    self.assertIsInstance(
                        datasource, dict, "panel must pin a datasource by uid"
                    )
                    self.assertIn(
                        datasource.get("uid"),
                        self.datasource_uids,
                        "panel references a datasource the monitoring role does "
                        "not provision",
                    )

    def test_panels_have_targets(self):
        for path in _dashboards():
            data = json.loads(path.read_text(encoding="utf-8"))
            for panel in _query_panels(data):
                with self.subTest(dashboard=path.name, panel=panel.get("title")):
                    targets = panel.get("targets")
                    self.assertTrue(targets, "panel has no query")
                    for target in targets:
                        self.assertTrue(
                            target.get("expr"), "target has no expr"
                        )


class QueryPanelFlattening(unittest.TestCase):
    """The panel walk itself, since the contracts above are only as good as it."""

    DASHBOARD = {
        "panels": [
            {"type": "timeseries", "title": "top-level"},
            {"type": "text", "title": "runbook note"},
            {
                "type": "row",
                "title": "collapsed row",
                "panels": [
                    {"type": "stat", "title": "nested"},
                    {"type": "text", "title": "nested note"},
                ],
            },
            {"type": "row", "title": "expanded row"},
        ]
    }

    def test_only_query_panels_survive_the_walk(self):
        """Rows and inert panels out, their queryable children in."""
        self.assertEqual(
            [p["title"] for p in _query_panels(self.DASHBOARD)],
            ["top-level", "nested"],
        )


class GrafanaProvisioningDefaults(unittest.TestCase):
    """The role's paths are what the deploy actually writes — keep them honest."""

    @classmethod
    def setUpClass(cls):
        with MONITORING_DEFAULTS.open(encoding="utf-8") as handle:
            cls.defaults = yaml.safe_load(handle)

    def test_datasource_definitions_are_complete(self):
        datasources = self.defaults["monitoring_grafana_datasources"]
        self.assertTrue(datasources)
        for entry in datasources:
            with self.subTest(datasource=entry.get("name")):
                for key in ("file", "name", "type", "uid", "url"):
                    self.assertTrue(entry.get(key), f"missing {key}")
                self.assertTrue(entry["file"].endswith(".yaml"))

    def test_every_datasource_uid_resolves_to_a_literal(self):
        """A uid the resolver cannot flatten would be compared as raw Jinja,
        silently making every dashboard reference invalid (or, worse, making
        the literal string `{{ ... }}` a legal uid)."""
        for uid in _provisioned_uids(self.defaults):
            with self.subTest(uid=uid):
                self.assertIsInstance(uid, str)
                self.assertNotIn("{{", uid)
                self.assertTrue(uid)

    def test_exactly_one_default_datasource(self):
        datasources = self.defaults["monitoring_grafana_datasources"]
        defaults = [d for d in datasources if d.get("is_default")]
        self.assertEqual(len(defaults), 1)
        self.assertEqual(defaults[0]["type"], "prometheus")

    def test_absent_datasources_do_not_collide_with_provisioned_ones(self):
        """deleteDatasources runs before every create — a name in both lists
        would drop and recreate the datasource on every start."""
        provisioned = {d["name"] for d in self.defaults["monitoring_grafana_datasources"]}
        absent = set(self.defaults.get("monitoring_grafana_absent_datasources", []))
        self.assertEqual(provisioned & absent, set())

    def test_dashboards_repo_path_points_at_the_committed_dashboards(self):
        self.assertTrue(
            self.defaults["monitoring_grafana_dashboards_repo"].endswith(
                "configs/mon/grafana-dashboards"
            )
        )


if __name__ == "__main__":
    unittest.main()
