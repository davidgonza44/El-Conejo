"""Real MySQL/MariaDB DDL test, only a newly created disposable random database.

No dotenv, valuable database, application startup, file storage or user data.
Creates conejo_approval_test_<12 hex>; drops ONLY that name if this run created it.
"""
import os
import secrets
import unittest
from unittest.mock import Mock

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from sqlalchemy import create_engine, inspect, text
from migrate_purchase_approval_reconciliation import migrate, validate_target, engine_for, GRANTS


class TargetGuards(unittest.TestCase):
    def test_thesis_denied_before_connect(self):
        engine = Mock()
        with self.assertRaises(RuntimeError):
            migrate(engine, "ferreteria_conejo_tesis")
        engine.begin.assert_not_called()

    def test_unknown_and_engine_mismatch(self):
        for name in ("mysql", "production", "conejo_approval_test_bad", "ferreteria_conejo_tesis;DROP"):
            with self.assertRaises(RuntimeError):
                validate_target(name)
        engine = Mock()
        engine.url.database = "ferreteria_conejo_tesis"
        with self.assertRaises(RuntimeError):
            migrate(engine, "ferreteria_conejo_e2e")
        engine.begin.assert_not_called()


def integration():
    database = "conejo_approval_test_" + secrets.token_hex(6)
    validate_target(database)
    engine = engine_for(database)
    if engine.url.host not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("Disposable migration suite requires loopback server.")
    control = create_engine(engine.url._replace(database=None), connect_args={"connect_timeout":5})
    created = False
    try:
        with control.begin() as connection:
            connection.execute(text(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4"))
            created = True
        with engine.begin() as connection:
            assert connection.execute(text("SELECT DATABASE()")).scalar() == database
            for statement in (
                "CREATE TABLE users (id INTEGER PRIMARY KEY)",
                "CREATE TABLE products (id INTEGER PRIMARY KEY, current_stock INTEGER NOT NULL)",
                "CREATE TABLE stock_movements (id INTEGER PRIMARY KEY)",
                "CREATE TABLE roles (id INTEGER PRIMARY KEY, slug VARCHAR(20) UNIQUE)",
                "CREATE TABLE permissions (id INTEGER PRIMARY KEY AUTO_INCREMENT, code VARCHAR(64) UNIQUE, module VARCHAR(40), label VARCHAR(120))",
                "CREATE TABLE role_permissions (role_id INTEGER, permission_id INTEGER, PRIMARY KEY(role_id,permission_id))",
                "CREATE TABLE purchase_orders (id INTEGER PRIMARY KEY, status VARCHAR(20) NOT NULL, "
                "CONSTRAINT ck_po_status CHECK (status IN ('pending','partial','completed','cancelled')))",
                "INSERT INTO users VALUES (1)",
                "INSERT INTO products VALUES (1,10)",
                "INSERT INTO roles VALUES (1,'admin'),(2,'inventario'),(3,'custom')",
                "INSERT INTO permissions (code,module,label) VALUES ('custom:keep','custom','Keep')",
                "INSERT INTO role_permissions VALUES (3,1)",
                "INSERT INTO role_permissions VALUES (1,1),(2,1)",
                "INSERT INTO purchase_orders VALUES (1,'pending'),(2,'partial'),(3,'completed'),(4,'cancelled')",
            ):
                connection.execute(text(statement))
        migrate(engine, database)
        migrate(engine, database)
        with engine.begin() as connection:
            assert connection.execute(text("SELECT DATABASE()")).scalar() == database
            assert connection.execute(text("SELECT id,status FROM purchase_orders ORDER BY id")).all() == [(1,"pending"),(2,"partial"),(3,"completed"),(4,"cancelled")]
            assert connection.execute(text("SELECT COUNT(*) FROM purchase_orders WHERE approved_at IS NOT NULL OR rejected_at IS NOT NULL")).scalar() == 0
            assert connection.execute(text("SELECT current_stock FROM products WHERE id=1")).scalar() == 10
            assert connection.execute(text("SELECT COUNT(*) FROM stock_movements")).scalar() == 0
            for slug, codes in GRANTS.items():
                actual = set(connection.execute(text("SELECT p.code FROM permissions p JOIN role_permissions rp ON rp.permission_id=p.id JOIN roles r ON r.id=rp.role_id WHERE r.slug=:slug"), {"slug":slug}).scalars())
                assert actual == set(codes) | {"custom:keep"}, (slug, actual)
            assert connection.execute(text("SELECT COUNT(*) FROM role_permissions WHERE role_id=3 AND permission_id=1")).scalar() == 1
            assert len(inspect(connection).get_foreign_keys("purchase_orders")) == 2
            for i, status in enumerate(("pending_approval", "approved", "rejected"), 5):
                connection.execute(text("INSERT INTO purchase_orders (id,status) VALUES (:id,:status)"), {"id":i, "status":status})
            assert inspect(connection).has_table("reconciliation_runs")
            assert inspect(connection).has_table("reconciliation_lines")
        print("PASS: real DDL twice; legacy states/stock preserved; nullable audit fields; new states; exact grants; custom role untouched.")
    finally:
        engine.dispose()
        if created:
            validate_target(database)
            with control.begin() as connection:
                connection.execute(text(f"DROP DATABASE `{database}`"))
            print("Cleanup: only the random disposable database created by this process.")
        control.dispose()


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(TargetGuards))
    if not result.wasSuccessful():
        raise SystemExit(1)
    integration()
