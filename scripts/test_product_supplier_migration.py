"""MariaDB DDL for product_suppliers. Disposable random database only.

No dotenv, thesis database, seed or backfill. Creates conejo_ps_test_<12 hex>
and drops only that database if this process created it.
"""
import os
import secrets
import unittest
from unittest.mock import Mock

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from migrate_product_suppliers import DISPOSABLE, engine_for, migrate, validate_target


class TargetGuards(unittest.TestCase):
    def test_valuable_databases_never_connect(self):
        engine = Mock()
        for name in ("ferreteria_conejo_tesis", "ferreteria_conejo", "ferreteria_conejo_e2e",
                     "conejo_ps_test_short", "mysql", "ferreteria_conejo_tesis;DROP"):
            with self.assertRaises(RuntimeError):
                migrate(engine, name)
        engine.begin.assert_not_called()

    def test_pattern(self):
        self.assertIsNotNone(DISPOSABLE.fullmatch("conejo_ps_test_" + "ab" * 6))
        with self.assertRaises(RuntimeError):
            validate_target("ferreteria_conejo_tesis")


def integration():
    database = "conejo_ps_test_" + secrets.token_hex(6)
    validate_target(database)
    engine = engine_for(database)
    if engine.url.host not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("Disposable migration suite requires loopback.")
    control = create_engine(engine.url._replace(database=None), connect_args={"connect_timeout": 5})
    created = False
    try:
        with control.begin() as connection:
            connection.execute(text(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4"))
            created = True
        with engine.begin() as connection:
            assert connection.execute(text("SELECT DATABASE()")).scalar() == database
            connection.execute(text("CREATE TABLE products (id INTEGER PRIMARY KEY, code VARCHAR(20))"))
            connection.execute(text("CREATE TABLE suppliers (id INTEGER PRIMARY KEY, name VARCHAR(80))"))
            connection.execute(text("CREATE TABLE sentinel_keep (id INTEGER PRIMARY KEY, note VARCHAR(20))"))
            connection.execute(text("INSERT INTO products VALUES (1,'P1'),(2,'P2')"))
            connection.execute(text("INSERT INTO suppliers VALUES (1,'A'),(2,'B'),(3,'C')"))
            connection.execute(text("INSERT INTO sentinel_keep VALUES (1,'keep')"))
        migrate(engine, database)
        migrate(engine, database)
        with engine.begin() as connection:
            assert inspect(connection).has_table("product_suppliers")
            assert connection.execute(text("SELECT note FROM sentinel_keep")).scalar() == "keep"
            assert connection.execute(text("SELECT COUNT(*) FROM products")).scalar() == 2
            connection.execute(text(
                "INSERT INTO product_suppliers (product_id, supplier_id, purchase_price, is_preferred, is_active, created_at, updated_at) "
                "VALUES (1,1,10.00,0,1,UTC_TIMESTAMP(),UTC_TIMESTAMP()), (1,2,12.50,0,1,UTC_TIMESTAMP(),UTC_TIMESTAMP())"))
            preferred = connection.execute(text(
                "SELECT preferred_product_id FROM product_suppliers WHERE supplier_id=1")).scalar()
            assert preferred is None
        with engine.begin() as connection:
            connection.execute(text("UPDATE product_suppliers SET is_preferred=1 WHERE product_id=1 AND supplier_id=1"))
            assert connection.execute(text(
                "SELECT preferred_product_id FROM product_suppliers WHERE supplier_id=1")).scalar() == 1
        failed = False
        try:
            with engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO product_suppliers (product_id, supplier_id, purchase_price, is_preferred, is_active, created_at, updated_at) "
                    "VALUES (1,3,9.00,1,1,UTC_TIMESTAMP(),UTC_TIMESTAMP())"))
        except IntegrityError:
            failed = True
        assert failed, "a second preferred supplier must be rejected"
        failed = False
        try:
            with engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO product_suppliers (product_id, supplier_id, purchase_price, is_preferred, is_active, created_at, updated_at) "
                    "VALUES (1,1,8.00,0,1,UTC_TIMESTAMP(),UTC_TIMESTAMP())"))
        except IntegrityError:
            failed = True
        assert failed, "duplicate product_id + supplier_id must be rejected"
        with engine.begin() as connection:
            connection.execute(text("UPDATE product_suppliers SET is_preferred=0 WHERE product_id=1 AND supplier_id=1"))
            connection.execute(text("UPDATE product_suppliers SET is_preferred=1 WHERE product_id=1 AND supplier_id=2"))
            rows = connection.execute(text(
                "SELECT supplier_id, is_preferred, preferred_product_id FROM product_suppliers WHERE product_id=1 ORDER BY supplier_id")).all()
            assert rows == [(1, 0, None), (2, 1, 1)], rows
        migrate(engine, database)
        with engine.begin() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM product_suppliers")).scalar() == 2
            assert connection.execute(text("SELECT code FROM products WHERE id=2")).scalar() == "P2"
            assert connection.execute(text("SELECT note FROM sentinel_keep")).scalar() == "keep"
        print("PASS: MariaDB product_suppliers DDL, one preferred, idempotent migration, unrelated rows preserved.")
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
