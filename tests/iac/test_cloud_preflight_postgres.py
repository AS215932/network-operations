"""Opt-in execution of the exact deployment script against disposable PostgreSQL."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ansible/roles/hyrule_cloud/files/provisioning-preflight.py"


@unittest.skipUnless(os.environ.get("HCP_PREFLIGHT_TEST_DATABASE_URL"), "requires disposable PostgreSQL and cloud runtime")
class CloudPreflightPostgresTests(unittest.TestCase):
    def test_empty_inflight_clean_and_unavailable(self):
        from sqlalchemy import text
        from sqlalchemy.engine import make_url
        from sqlalchemy.ext.asyncio import create_async_engine

        url = os.environ["HCP_PREFLIGHT_TEST_DATABASE_URL"]
        parsed = make_url(url)
        self.assertEqual(parsed.database, "cloud_preflight_test")
        self.assertEqual(parsed.drivername, "postgresql+asyncpg")
        self.assertIn(parsed.host, (None, "localhost", "127.0.0.1", "::1"))
        source = Path(os.environ["HCP_PREFLIGHT_CLOUD_SOURCE"]).resolve()
        self.assertTrue((source / "hyrule_cloud/config.py").is_file())

        def run(target=url):
            env = dict(os.environ, HYRULE_DATABASE_URL=target, PYTHONPATH=str(source))
            return subprocess.run([sys.executable, str(SCRIPT)], cwd=source, env=env,
                                  capture_output=True, text=True, timeout=25)

        async def scenario():
            engine = create_async_engine(url)
            try:
                async with engine.begin() as connection:
                    tables = await connection.scalar(text(
                        "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
                    ))
                    self.assertEqual(tables, 0, "fixture must start empty; never replace existing tables")
                result = await asyncio.to_thread(run)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("empty database", result.stdout)
                async with engine.begin() as connection:
                    await connection.execute(text("CREATE TABLE vms (vm_id text PRIMARY KEY, status text)"))
                    await connection.execute(text("INSERT INTO vms VALUES ('fixture-a','provisioning'), ('fixture-b','ready')"))
                result = await asyncio.to_thread(run)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("1 provisioning attempt", result.stdout)
                self.assertNotIn("fixture-a", result.stdout)
                async with engine.begin() as connection:
                    self.assertEqual(await connection.scalar(text("SELECT status FROM vms WHERE vm_id='fixture-a'")), "provisioning")
                    await connection.execute(text("UPDATE vms SET status='failed' WHERE vm_id='fixture-a'"))
                result = await asyncio.to_thread(run)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                async with engine.begin() as connection:
                    await connection.execute(text("ALTER TABLE vms RENAME TO unexpected_table"))
                result = await asyncio.to_thread(run)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, "")
                unreachable = "postgresql+asyncpg://test:never-print-this@127.0.0.1:1/cloud_preflight_test"
                result = await asyncio.to_thread(run, unreachable)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("never-print-this", result.stdout + result.stderr)
                self.assertEqual(result.stderr, "")
            finally:
                await engine.dispose()

        asyncio.run(scenario())
