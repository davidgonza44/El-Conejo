"""Migration delta on temporary SQLite and fail-closed DEMO guards (no network)."""
import os
import unittest
from unittest.mock import patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from sqlalchemy import create_engine, inspect, text
from migrate_operational_separation import GRANTS, REVOKES, apply_changes, main
from docker_support.test_demo import SAFE


class MigrationTests(unittest.TestCase):
    def test_additive_idempotent_cash_and_exact_rbac_delta(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            statements = (
                "CREATE TABLE sales (id INTEGER PRIMARY KEY, total DECIMAL(12,2), payment_method TEXT)",
                "CREATE TABLE products (id INTEGER PRIMARY KEY, current_stock INTEGER)",
                "CREATE TABLE users (id INTEGER PRIMARY KEY, password_hash TEXT)",
                "CREATE TABLE roles (id INTEGER PRIMARY KEY, slug TEXT, is_system BOOLEAN)",
                "CREATE TABLE permissions (id INTEGER PRIMARY KEY, code TEXT UNIQUE, module TEXT, label TEXT)",
                "CREATE TABLE role_permissions (role_id INTEGER, permission_id INTEGER, PRIMARY KEY(role_id,permission_id))",
                "INSERT INTO sales VALUES(1,27,'cash'),(2,27,'transfer')",
                "INSERT INTO products VALUES(1,10)",
                "INSERT INTO users VALUES(1,'synthetic-hash')",
                "INSERT INTO roles VALUES(1,'admin',1),(2,'inventario',1),(3,'vendedor',1),(4,'soporte_ti',1),(5,'custom',0)",
            )
            for sql in statements:
                connection.execute(text(sql))
            codes = sorted(set().union(*GRANTS.values(), *REVOKES.values()) | {"custom:keep", "sales:write"})
            for code in codes:
                connection.execute(text("INSERT INTO permissions(code,module,label) VALUES(:code,'test','Test')"), {"code": code})
            connection.execute(text("INSERT INTO role_permissions SELECT r.id,p.id FROM roles r CROSS JOIN permissions p"))
            apply_changes(connection)
            after = connection.execute(text("SELECT * FROM role_permissions ORDER BY role_id,permission_id")).all()
            apply_changes(connection)
            self.assertEqual(after, connection.execute(text("SELECT * FROM role_permissions ORDER BY role_id,permission_id")).all())
            for slug in ("admin", "inventario", "vendedor", "soporte_ti", "custom"):
                actual = set(connection.execute(text("SELECT p.code FROM role_permissions rp JOIN roles r ON r.id=rp.role_id "
                                                       "JOIN permissions p ON p.id=rp.permission_id WHERE r.slug=:slug"), {"slug": slug}).scalars())
                self.assertEqual(actual, set(codes) - REVOKES.get(slug, set()) | GRANTS.get(slug, set()))
            self.assertEqual(connection.execute(text("SELECT id,total,payment_method,cash_received,change_due FROM sales ORDER BY id")).all(),
                             [(1, 27, "cash", None, None), (2, 27, "transfer", None, None)])
            self.assertEqual(connection.execute(text("SELECT current_stock FROM products")).scalar_one(), 10)
            self.assertEqual(connection.execute(text("SELECT password_hash FROM users")).scalar_one(), "synthetic-hash")
            columns = {column["name"]: column for column in inspect(connection).get_columns("sales")}
            for name in ("cash_received", "change_due"):
                self.assertTrue(columns[name]["nullable"])
                self.assertEqual((columns[name]["type"].precision, columns[name]["type"].scale), (12, 2))

    def test_wrong_cash_schema_refused(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            for sql in ("CREATE TABLE sales(cash_received FLOAT)", "CREATE TABLE roles(id INTEGER)",
                        "CREATE TABLE permissions(id INTEGER)", "CREATE TABLE role_permissions(id INTEGER)"):
                connection.execute(text(sql))
            with self.assertRaises(RuntimeError):
                apply_changes(connection)

    def test_protected_environment_refused_before_engine_creation(self):
        for change in ({"DB_NAME": "ferreteria_conejo"}, {"APP_BASE_URL": "http://localhost:5000"},
                       {"DOCKER_ENV_KIND": "disposable"}, {"CONEJO_CONTAINER": "0"}, {"DB_HOST": "localhost"}):
            with self.subTest(change=change), patch.dict(os.environ, SAFE | change, clear=True), \
                 patch("sys.argv", ["migrate_operational_separation.py", "--apply"]), \
                 patch("docker_support.bootstrap.engine") as engine:
                with self.assertRaises(RuntimeError):
                    main()
                engine.assert_not_called()

    def test_default_is_plan_without_connecting(self):
        with patch("sys.argv", ["migrate_operational_separation.py"]), patch("docker_support.bootstrap.engine") as engine:
            main()
            engine.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
