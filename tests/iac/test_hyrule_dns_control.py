from __future__ import annotations

import fcntl
import hashlib
import hmac
import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "ansible/roles/knot/files/hyrule_dns_control.py"
SPEC = importlib.util.spec_from_file_location("hyrule_dns_control", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
dns_control = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dns_control
SPEC.loader.exec_module(dns_control)


class FakeRunner:
    def __init__(self, fail_once: tuple[str, ...] | None = None) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.options: list[dict[str, object]] = []
        self.checked: list[tuple[str, Path]] = []
        self.fail_once = fail_once
        self.failed = False

    def knot(self, *args: str, **kwargs: object) -> str:
        self.commands.append(args)
        self.options.append(kwargs)
        if args == self.fail_once and not self.failed:
            self.failed = True
            raise dns_control.CommandError("simulated Knot interruption")
        if args[:3] == ("zone-read", "example.dev", "@"):
            return "example.dev. 3600 DNSKEY 257 3 13 AABBCC==\n"
        return ""

    def check_zonefile(self, zone: str, path: Path) -> None:
        self.checked.append((zone, path))


def payload(revision: int, address: str = "192.0.2.10") -> dict[str, object]:
    return {
        "revision": revision,
        "nameservers": ["ns1.hyrule.host", "ns2.hyrule.host"],
        "soa_mname": "ns1.hyrule.host",
        "soa_rname": "hostmaster.hyrule.host",
        "dnssec": True,
        "records": [{"name": "www", "type": "A", "ttl": 300, "values": [address]}],
    }


class DNSControlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.settings = dns_control.Settings(
            secret="s" * 32,
            state_file=root / "state/state.json",
            generated_config=root / "customer-zones.conf",
            zones_dir=root / "zones",
            knot_config=root / "knot.conf",
        )
        self.runner = FakeRunner()
        self.store = dns_control.ZoneStore(self.settings, self.runner)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_create_update_dnskey_and_delete(self) -> None:
        created = self.store.apply("EXAMPLE.DEV.", payload(1))
        self.assertTrue(created["created"])
        self.assertIn("domain: example.dev", self.settings.generated_config.read_text())
        zonefile = (self.settings.zones_dir / "example.dev.zone").read_text()
        self.assertIn("NS ns1.hyrule.host.", zonefile)
        self.assertIn("NS ns2.hyrule.host.", zonefile)
        self.assertIn(("reload",), self.runner.commands)
        self.assertIn(("zone-sign", "example.dev"), self.runner.commands)

        # Same revision and state is idempotent; a newer one is a Knot zone
        # transaction and does not rewrite catalog membership.
        self.assertFalse(self.store.apply("example.dev", payload(1))["created"])
        self.store.apply("example.dev", payload(2, "192.0.2.20"))
        self.assertIn(("zone-begin", "example.dev", "+benevolent"), self.runner.commands)
        self.assertIn(("zone-unset", "example.dev", "www", "A"), self.runner.commands)
        self.assertIn(
            ("zone-set", "example.dev", "www", "300", "A", "192.0.2.20"),
            self.runner.commands,
        )
        self.assertIn(("zone-commit", "example.dev"), self.runner.commands)

        keys = self.store.dnskeys("example.dev")["dnskey"]
        self.assertEqual(keys[0]["flags"], 257)
        self.assertEqual(keys[0]["algorithm"], 13)
        self.assertEqual(keys[0]["pub_key"], "AABBCC==")

        self.assertTrue(self.store.delete("example.dev")["deleted"])
        self.assertNotIn("domain: example.dev", self.settings.generated_config.read_text())

    def test_revision_and_zone_validation_fail_closed(self) -> None:
        self.store.apply("example.dev", payload(2))
        with self.assertRaisesRegex(dns_control.APIError, "stale"):
            self.store.apply("example.dev", payload(1))
        changed = payload(2, "192.0.2.99")
        with self.assertRaisesRegex(dns_control.APIError, "reused"):
            self.store.apply("example.dev", changed)
        with self.assertRaises(dns_control.APIError):
            self.store.apply("nested.example.dev", payload(1))

    def test_hmac_binds_timestamp_method_path_and_body(self) -> None:
        body = b'{"revision":1}'
        timestamp = str(int(time.time()))
        path = "/v1/zones/example.dev"
        digest = hashlib.sha256(body).hexdigest()
        signing_input = "\n".join([timestamp, "PUT", path, digest]).encode()
        signature = hmac.new(
            self.settings.secret.encode(), signing_input, hashlib.sha256
        ).hexdigest()
        dns_control.verify_signature(
            self.settings, timestamp, f"sha256={signature}", "PUT", path, body
        )
        with self.assertRaises(dns_control.APIError):
            dns_control.verify_signature(
                self.settings, timestamp, f"sha256={signature}", "DELETE", path, body
            )

    def test_txt_values_are_quoted_before_zonefile_rendering(self) -> None:
        desired = payload(1)
        desired["records"] = [
            {
                "name": "_dmarc",
                "type": "TXT",
                "ttl": 300,
                "values": ["v=DMARC1; p=none"],
            }
        ]

        self.store.apply("example.dev", desired)

        zonefile = (self.settings.zones_dir / "example.dev.zone").read_text()
        self.assertIn('_dmarc 300 IN TXT "v=DMARC1; p=none"', zonefile)
        state = json.loads(self.settings.state_file.read_text())
        self.assertEqual(
            state["zones"]["example.dev"]["records"][0]["values"],
            ['"v=DMARC1; p=none"'],
        )

    def test_content_length_validation_rejects_malformed_values(self) -> None:
        self.assertEqual(dns_control.parse_content_length(None), 0)
        self.assertEqual(dns_control.parse_content_length("12"), 12)
        for value in ("not-a-number", "-1"):
            with self.subTest(value=value), self.assertRaises(dns_control.APIError) as caught:
                dns_control.parse_content_length(value)
            self.assertEqual(caught.exception.status, 400)

    def test_customer_zone_include_follows_template_and_catalog_definition(self) -> None:
        template = (REPO / "ansible/roles/knot/templates/knot.conf.j2").read_text()
        member_template = "  - id: {{ knot_customer_member_template }}"
        catalog_zone = "  - domain: {{ knot_customer_catalog_zone }}"
        customer_include = 'include: "{{ knot_customer_zones_config }}"'

        self.assertLess(template.index(member_template), template.index(customer_include))
        self.assertLess(template.index(catalog_zone), template.index(customer_include))

    def test_online_backup_holds_dns_mutation_lock_through_control_state_copy(self) -> None:
        helper = (REPO / "ansible/roles/knot/templates/knot-online-backup.j2").read_text()
        service = (
            REPO / "ansible/roles/knot/templates/knot-online-backup.service.j2"
        ).read_text()

        lock = "/usr/bin/flock --exclusive 9"
        snapshot = "zone-backup"
        state_copy = 'cp {{ knot_dns_control_state_dir }}/state.json'
        include_copy = 'cp {{ knot_customer_zones_config }}'
        unlock = "/usr/bin/flock --unlock 9"
        self.assertLess(helper.index(lock), helper.index(snapshot))
        self.assertLess(helper.index(snapshot), helper.index(state_copy))
        self.assertLess(helper.index(state_copy), helper.index(include_copy))
        self.assertLess(helper.index(include_copy), helper.index(unlock))
        self.assertIn("{{ knot_dns_control_state_dir }}", service)

    def test_control_store_uses_backup_lock_for_mutations_and_recovery(self) -> None:
        lock_path = self.settings.state_file.parent / "mutation.lock"
        completed = threading.Event()
        errors: list[BaseException] = []

        def mutate() -> None:
            try:
                self.store.apply("example.dev", payload(1))
            except BaseException as exc:
                errors.append(exc)
            finally:
                completed.set()

        self.assertTrue(lock_path.exists())
        with lock_path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            worker = threading.Thread(target=mutate)
            worker.start()
            self.assertFalse(completed.wait(0.05))

        self.assertTrue(completed.wait(2))
        worker.join()
        self.assertEqual(errors, [])

    def test_pending_create_is_replayed_after_restart(self) -> None:
        interrupted = FakeRunner(("zone-sign", "example.dev"))
        store = dns_control.ZoneStore(self.settings, interrupted)
        with self.assertRaisesRegex(dns_control.CommandError, "interruption"):
            store.apply("example.dev", payload(1))

        state = json.loads(self.settings.state_file.read_text())
        self.assertEqual(state["zones"], {})
        self.assertEqual(state["pending"]["example.dev"]["action"], "upsert")

        recovered_runner = FakeRunner()
        recovered = dns_control.ZoneStore(self.settings, recovered_runner)
        state = json.loads(self.settings.state_file.read_text())
        self.assertEqual(state["pending"], {})
        self.assertEqual(state["zones"]["example.dev"]["revision"], 1)
        self.assertIn(("reload",), recovered_runner.commands)
        self.assertIn(("zone-sign", "example.dev"), recovered_runner.commands)
        self.assertFalse(recovered.apply("example.dev", payload(1))["created"])

    def test_pending_update_and_delete_are_convergent(self) -> None:
        self.store.apply("example.dev", payload(1))
        self.runner.fail_once = ("zone-sign", "example.dev")
        with self.assertRaisesRegex(dns_control.CommandError, "interruption"):
            self.store.apply("example.dev", payload(2, "192.0.2.20"))

        update_runner = FakeRunner()
        recovered = dns_control.ZoneStore(self.settings, update_runner)
        state = json.loads(self.settings.state_file.read_text())
        self.assertEqual(state["zones"]["example.dev"]["revision"], 2)
        self.assertEqual(state["pending"], {})
        self.assertIn(
            ("zone-set", "example.dev", "www", "300", "A", "192.0.2.20"),
            update_runner.commands,
        )

        update_runner.fail_once = ("reload",)
        with self.assertRaisesRegex(dns_control.CommandError, "interruption"):
            recovered.delete("example.dev")
        self.assertNotIn("domain: example.dev", self.settings.generated_config.read_text())

        delete_runner = FakeRunner()
        dns_control.ZoneStore(self.settings, delete_runner)
        state = json.loads(self.settings.state_file.read_text())
        self.assertNotIn("example.dev", state["zones"])
        self.assertEqual(state["pending"], {})
        self.assertIn(("reload",), delete_runner.commands)
        self.assertIn(
            (
                "zone-purge",
                "example.dev",
                "+orphan",
                "+journal",
                "+timers",
                "+kaspdb",
            ),
            delete_runner.commands,
        )
        purge_index = delete_runner.commands.index(
            (
                "zone-purge",
                "example.dev",
                "+orphan",
                "+journal",
                "+timers",
                "+kaspdb",
            )
        )
        self.assertEqual(
            delete_runner.options[purge_index],
            {"blocking": True, "force": True},
        )


if __name__ == "__main__":
    unittest.main()
