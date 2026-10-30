"""MariaDB DDL for products.brand. Disposable random database only.

No dotenv, thesis database or backfill. Creates conejo_brand_test_<12 hex>
and drops only that database if this process created it.
"""
import os
import secrets
import unittest
from decimal import Decimal
from unittest.mock import Mock

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from sqlalchemy import create_engine, text
from migrate_product_brand import DISPOSABLE, engine_for, migrate, validate_target


class TargetGuards(unittest.TestCase):
    def test_valuable_databases_never_connect(self):
        engine = Mock()
        for name in ("ferreteria_conejo_tesis", "ferreteria_conejo", "ferreteria_conejo_e2e",
                     "conejo_brand_test_short", "mysql"):
            with self.assertRaises(RuntimeError):
                migrate(engine, name)
        engine.begin.assert_not_called()

    def test_pattern(self):
        self.assertIsNotNone(DISPOSABLE.fullmatch("conejo_brand_test_" + "ab" * 6))
        with self.assertRaises(RuntimeError):
            validate_target("ferreteria_conejo_tesis")


def integration():
    database = "conejo_brand_test_" + secrets.token_hex(6)
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
            connection.execute(text(
                "CREATE TABLE products (id INTEGER PRIMARY KEY, code VARCHAR(20), purchase_price DECIMAL(12,2) NOT NULL, current_stock INTEGER NOT NULL)"))
            connection.execute(text("CREATE TABLE sentinel_keep (id INTEGER PRIMARY KEY, note VARCHAR(20))"))
            connection.execute(text("INSERT INTO products VALUES (1,'P1',10.50,4),(2,'P2',3.00,0)"))
            connection.execute(text("INSERT INTO sentinel_keep VALUES (1,'keep')"))
        migrate(engine, database)
        migrate(engine, database)
        with engine.begin() as connection:
            column = connection.execute(text(
                "SELECT IS_NULLABLE, COLUMN_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='products' AND COLUMN_NAME='brand'")).one()
            assert column == ("YES", "varchar(100)"), column
            rows = connection.execute(text("SELECT id, purchase_price, current_stock, brand FROM products ORDER BY id")).all()
            assert [(row[0], Decimal(row[1]), row[2], row[3]) for row in rows] == [
                (1, Decimal("10.50"), 4, None), (2, Decimal("3.00"), 0, None)], rows
            assert connection.execute(text("SELECT note FROM sentinel_keep")).scalar() == "keep"
            assert connection.execute(text("SELECT COUNT(*) FROM products")).scalar() == 2
        print("PASS: nullable products.brand; existing prices and stock preserved; migration idempotent.")
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
