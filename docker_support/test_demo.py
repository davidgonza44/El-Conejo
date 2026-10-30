"""DEMO tests without a database connection or access to host environment files."""
from collections import Counter
import os
import unittest
from unittest.mock import MagicMock, patch

from docker_support import demo_data as data
from docker_support.guard import validate_demo, wait_database

SAFE = {"DOCKER_ENV_KIND": "demo", "DEMO_MODE": "true", "DB_NAME": "conejo_demo_12345678",
    "DB_HOST": "db", "DB_PORT": "3306", "CONEJO_CONTAINER": "1", "PYTHON_DOTENV_DISABLED": "1",
    "ENVIRONMENT_ID": "a" * 32, "DB_PASSWORD": "x" * 32, "SECRET_KEY": "y" * 32,
    "SECRETS_ENCRYPTION_KEY": "z" * 32, "DB_USER": "conejo_app", "APP_ENV": "demo",
    "APP_BASE_URL": "http://localhost:5001", "DEMO_SEED_VERSION": "2"}


class DemoTests(unittest.TestCase):
    def test_valid_target(self):
        with patch.dict(os.environ, SAFE, clear=True):
            self.assertEqual(validate_demo(), "conejo_demo_12345678")

    def test_dangerous_targets_rejected_before_connection(self):
        changes = ({"DB_NAME": "ferreteria_conejo"}, {"DB_NAME": "conejo_disposable_12345678"},
            {"DEMO_MODE": "false"}, {"DOCKER_ENV_KIND": "production"}, {"DOCKER_ENV_KIND": "disposable"},
            {"APP_ENV": "production"}, {"APP_ENV": "development"}, {"DB_HOST": "localhost"},
            {"DB_PORT": "3307"}, {"DB_USER": "root"}, {"APP_BASE_URL": "http://localhost:5000"},
            {"PYTHON_DOTENV_DISABLED": "0"}, {"DEMO_SEED_VERSION": "1"}, {"FLASK_ENV": "production"})
        for change in changes:
            with self.subTest(change=change), patch.dict(os.environ, SAFE | change, clear=True):
                with patch("docker_support.guard.pymysql.connect") as connect:
                    with self.assertRaises(RuntimeError):
                        wait_database()
                    connect.assert_not_called()

    def test_version_and_incomplete_seed_fail_closed(self):
        from docker_support.demo import preflight
        connection = MagicMock()
        connection.engine.url.host, connection.engine.url.port = "db", 3306
        connection.engine.url.database = SAFE["DB_NAME"]
        connection.engine.url.username, connection.engine.url.drivername = "conejo_app", "mysql+pymysql"
        connection.execute.return_value.scalar_one.return_value = SAFE["DB_NAME"]
        inspector = MagicMock()
        inspector.get_table_names.return_value = ["container_installation", "demo_seed"]
        for row in (None, {"version": 1}, {"version": 2, "manifest": "wrong"},
                    {"version": 2, "manifest": data.manifest_hash(), "status": "building"}):
            connection.execute.return_value.mappings.return_value.one_or_none.return_value = row
            with patch.dict(os.environ, SAFE, clear=True), patch("docker_support.demo.inspect", return_value=inspector), \
                 patch("docker_support.bootstrap.identity"):
                with self.assertRaises(RuntimeError):
                    preflight(connection)

    def test_manifest_reproducible(self):
        self.assertEqual(data.manifest_hash(), data.manifest_hash())
        self.assertEqual(len(data.products()), 50)
        self.assertEqual(len(list(data.historical_rows())), 1863)
        self.assertEqual(Counter(o["state"] for o in data.purchases()),
            {"completed": 10, "partial": 2, "approved": 3, "pending_approval": 3, "rejected": 1, "cancelled": 1})

    def test_stale_application_config_cannot_connect(self):
        from sqlalchemy.engine import URL
        from docker_support.guard import validate_demo_url
        with patch.dict(os.environ, SAFE, clear=True):
            for database in ("ferreteria_conejo", "conejo_disposable_12345678"):
                target = URL.create("mysql+pymysql", username="conejo_app", host="db", port=3306, database=database)
                with self.assertRaises(RuntimeError):
                    validate_demo_url(target)

    def test_timeline_never_needs_negative_stock(self):
        balances = data.initial_stocks()
        self.assertTrue(all(q > 0 for q in balances.values()))
        events = []
        for operation in data.sales() + data.notes():
            events.extend((operation["at"], n, -q) for n, q in operation["items"])
        for order in data.purchases():
            events.extend((order["receive_at"], n, q) for n, q in order["received"])
        events.extend((e["at"], e["number"], e["delta"]) for e in data.adjustments())
        self.assertEqual(len(events) + len(balances), 250)
        for _, n, delta in sorted(events):
            balances[n] += delta
            self.assertGreaterEqual(balances[n], 0)
        self.assertEqual(balances, {p["number"]: p["target"] for p in data.products()})


if __name__ == "__main__":
    unittest.main()
