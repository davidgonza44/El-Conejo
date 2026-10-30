"""Real DDL twice on a NEW random local database, with synthetic legacy rows only."""
import os
import secrets
import unittest
from unittest.mock import Mock

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError
from migrate_movement_traceability import migrate, validate_target, engine_for


class Guards(unittest.TestCase):
    def test_thesis_opt_in_is_exact_and_does_not_bypass_engine_guard(self):
        validate_target("ferreteria_conejo_tesis", authorize_thesis=True)
        for target in ("ferreteria_conejo", "ferreteria_conejo_e2e", "mysql"):
            with self.assertRaises(RuntimeError):
                validate_target(target, authorize_thesis=True)
        engine = Mock()
        engine.url.database = "ferreteria_conejo"
        with self.assertRaises(RuntimeError):
            migrate(engine, "ferreteria_conejo_tesis", authorize_thesis=True)
        engine.begin.assert_not_called()

    def test_valuable_targets_denied_before_connection(self):
        for target in ("ferreteria_conejo_tesis", "ferreteria_conejo", "ferreteria_conejo_e2e", "mysql"):
            engine = Mock()
            with self.assertRaises(RuntimeError):
                migrate(engine, target)
            engine.begin.assert_not_called()

    def test_engine_mismatch_denied(self):
        engine = Mock()
        engine.url.database = "ferreteria_conejo_tesis"
        with self.assertRaises(RuntimeError):
            migrate(engine, "conejo_trace_test_0123456789ab")
        engine.begin.assert_not_called()


def integration():
    database = "conejo_trace_test_" + secrets.token_hex(6)
    validate_target(database)
    engine = engine_for(database)
    if engine.url.host not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("Disposable suite requires loopback.")
    control = create_engine(engine.url._replace(database=None), connect_args={"connect_timeout": 5})
    created = False
    try:
        with control.begin() as connection:
            connection.execute(text(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4"))
            created = True
        with engine.begin() as connection:
            assert connection.execute(text("SELECT DATABASE()")).scalar() == database
            connection.execute(text("CREATE TABLE stock_movements (id INT PRIMARY KEY, product_id INT NOT NULL, "
                                    "movement_type VARCHAR(20), quantity INT, previous_stock INT, new_stock INT, "
                                    "reason VARCHAR(255), user_id INT, created_at DATETIME)"))
            connection.execute(text("INSERT INTO stock_movements VALUES "
                                    "(1,1,'entrada',10,0,10,'Stock inicial',1,'2025-01-01'),"
                                    "(2,1,'salida',2,10,8,'Nota de entrega #NE-000001',1,'2025-01-02')"))
            before = connection.execute(text("SELECT * FROM stock_movements ORDER BY id")).all()
        migrate(engine, database)
        with engine.connect() as connection:
            first_columns = [(c["name"], str(c["type"]), c["nullable"], c["default"])
                             for c in inspect(connection).get_columns("stock_movements")]
        migrate(engine, database)
        with engine.begin() as connection:
            assert connection.execute(text("SELECT DATABASE()")).scalar() == database
            assert [(c["name"], str(c["type"]), c["nullable"], c["default"])
                    for c in inspect(connection).get_columns("stock_movements")] == first_columns
            rows = connection.execute(text("SELECT * FROM stock_movements ORDER BY id")).all()
            assert [tuple(row[:-2]) for row in rows] == [tuple(row) for row in before]
            assert all(row[-2:] == (None, None) for row in rows)
            assert len(inspect(connection).get_check_constraints("stock_movements")) == 2
            for value in (None, "manual", "initial_stock"):
                connection.execute(text("INSERT INTO stock_movements (id,product_id,creation_origin) VALUES (:id,1,:origin)"),
                                   {"id": 10 + (0 if value is None else len(value)), "origin": value})
            for origin, external in (("sale", None), ("", None), (None, "X"), ("initial_stock", "X")):
                try:
                    connection.execute(text("INSERT INTO stock_movements (id,product_id,creation_origin,external_reference) "
                                            "VALUES (99,1,:origin,:external)"), {"origin": origin, "external": external})
                except (IntegrityError, OperationalError):
                    pass
                else:
                    raise AssertionError("Database accepted an invalid origin/reference")
            connection.execute(text("INSERT INTO stock_movements (id,product_id,creation_origin,external_reference) "
                                    "VALUES (98,1,'manual','VTA-001572')"))
        print("PASS: real MySQL/MariaDB DDL twice; exactly two nullable columns; CHECKs enforced; historical values unchanged.")
    finally:
        engine.dispose()
        if created:
            validate_target(database)
            with control.begin() as connection:
                connection.execute(text(f"DROP DATABASE `{database}`"))
            print("PASS: removed only the disposable database created by this run; thesis never selected.")
        control.dispose()


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Guards))
    if not result.wasSuccessful():
        raise SystemExit(1)
    integration()
