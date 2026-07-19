import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]
MON = REPO / "configs" / "mon"


def _load_yaml(path: Path):
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


class PrometheusConfigContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prometheus = _load_yaml(MON / "prometheus.yml")
        cls.mon_blackbox = _load_yaml(MON / "blackbox.yml")
        cls.extmon_blackbox = _load_yaml(
            REPO / "ansible" / "generated" / "extmon" / "blackbox.yml"
        )

    def test_core_config_has_well_formed_unique_scrape_jobs(self):
        self.assertIsInstance(self.prometheus, dict)
        self.assertIsInstance(self.prometheus.get("global"), dict)
        self.assertIsInstance(self.prometheus.get("alerting"), dict)
        self.assertIsInstance(self.prometheus.get("rule_files"), list)
        self.assertTrue(self.prometheus["rule_files"])

        jobs = self.prometheus.get("scrape_configs")
        self.assertIsInstance(jobs, list)
        self.assertTrue(jobs)
        names = []
        for job in jobs:
            self.assertIsInstance(job, dict)
            name = job.get("job_name")
            self.assertIsInstance(name, str)
            self.assertTrue(name)
            names.append(name)
            static_configs = job.get("static_configs")
            self.assertIsInstance(static_configs, list, name)
            self.assertTrue(static_configs, name)
            for static in static_configs:
                self.assertIsInstance(static, dict, name)
                targets = static.get("targets")
                self.assertIsInstance(targets, list, name)
                self.assertTrue(targets, name)
                self.assertTrue(
                    all(isinstance(target, str) and target for target in targets)
                )
        self.assertEqual(len(names), len(set(names)))

    def test_blackbox_module_documents_are_well_formed(self):
        for source, document in (
            ("mon", self.mon_blackbox),
            ("extmon", self.extmon_blackbox),
        ):
            self.assertIsInstance(document, dict, source)
            modules = document.get("modules")
            self.assertIsInstance(modules, dict, source)
            self.assertTrue(modules, source)
            for name, module in modules.items():
                self.assertIsInstance(name, str)
                self.assertIsInstance(module, dict, name)
                prober = module.get("prober")
                self.assertIn(prober, {"http", "tcp", "dns", "icmp", "grpc"}, name)
                self.assertIsInstance(module.get(prober), dict, name)
                self.assertRegex(
                    str(module.get("timeout", "")), r"^[1-9][0-9]*(ms|s|m)$"
                )
                if prober == "http":
                    codes = module["http"].get("valid_status_codes")
                    self.assertIsInstance(codes, list, name)
                    self.assertTrue(codes, name)
                    self.assertTrue(
                        all(
                            isinstance(code, int) and 100 <= code <= 599
                            for code in codes
                        )
                    )

    def test_probe_jobs_reference_modules_on_their_selected_exporter(self):
        module_sets = {
            "[::1]:9115": set(self.mon_blackbox["modules"]),
            "[2001:19f0:7402:0cd5:5400:06ff:fe40:7112]:9115": set(
                self.extmon_blackbox["modules"]
            ),
        }
        probe_jobs = [
            job
            for job in self.prometheus["scrape_configs"]
            if job.get("metrics_path") == "/probe"
        ]
        self.assertTrue(probe_jobs)
        for job in probe_jobs:
            name = job["job_name"]
            params = job.get("params")
            self.assertIsInstance(params, dict, name)
            selected = params.get("module")
            self.assertIsInstance(selected, list, name)
            self.assertEqual(len(selected), 1, name)
            replacements = [
                relabel.get("replacement")
                for relabel in job.get("relabel_configs", [])
                if isinstance(relabel, dict)
                and relabel.get("target_label") == "__address__"
            ]
            self.assertEqual(len(replacements), 1, name)
            exporter = replacements[0]
            self.assertIn(exporter, module_sets, name)
            self.assertIn(selected[0], module_sets[exporter], name)

    def test_public_ipv4_dns_jobs_probe_both_authorities(self):
        jobs = {
            job["job_name"]: job for job in self.prometheus["scrape_configs"]
        }
        expected = {"46.105.40.223:53", "54.38.14.218:53"}
        for name in (
            "blackbox-dns-hyrule-ipv4",
            "blackbox-dns-hyrule-deploy-ipv4",
        ):
            targets = set(jobs[name]["static_configs"][0]["targets"])
            self.assertEqual(targets, expected, name)

    def test_rule_files_have_valid_group_and_rule_structure(self):
        alert_names = []
        paths = sorted((MON / "prometheus-rules").glob("*.yml"))
        self.assertTrue(paths)
        for path in paths:
            document = _load_yaml(path)
            self.assertIsInstance(document, dict, path.name)
            groups = document.get("groups")
            self.assertIsInstance(groups, list, path.name)
            self.assertTrue(groups, path.name)
            for group in groups:
                self.assertIsInstance(group, dict, path.name)
                self.assertIsInstance(group.get("name"), str, path.name)
                rules = group.get("rules")
                self.assertIsInstance(rules, list, path.name)
                self.assertTrue(rules, path.name)
                for rule in rules:
                    self.assertIsInstance(rule, dict, path.name)
                    self.assertTrue(
                        bool(rule.get("alert")) ^ bool(rule.get("record")), path.name
                    )
                    self.assertIsInstance(rule.get("expr"), str, path.name)
                    self.assertTrue(rule["expr"].strip(), path.name)
                    if rule.get("alert"):
                        alert_names.append(rule["alert"])
                    for field in ("labels", "annotations"):
                        if field in rule:
                            self.assertIsInstance(rule[field], dict, path.name)
        self.assertEqual(len(alert_names), len(set(alert_names)))


if __name__ == "__main__":
    unittest.main()
