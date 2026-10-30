"""Run existing purchase/reception assertions on a NEW disposable local database.

Create the current schema directly; legacy purchase migrations are out of scope.
Only PDF checks are excluded, per the operator's no-PDF restriction.
"""
import os
import secrets
import tempfile
from unittest.mock import patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from sqlalchemy import create_engine, inspect, text
from migrate_movement_traceability import engine_for, validate_target
import purchase_e2e_support as support
from app.extensions import db


def run(suite_name):
    import importlib

    database = "conejo_trace_test_" + secrets.token_hex(6)
    validate_target(database)
    engine = engine_for(database)
    if engine.url.host not in ("localhost", "127.0.0.1", "::1"):
        raise RuntimeError("Disposable local database required.")
    control = create_engine(engine.url._replace(database=None))
    created = False
    apps = []
    original_factory = support.create_app

    def schema_assertion(target_engine, target):
        assert target == database and target_engine.url.database == database
        with target_engine.connect() as connection:
            assert connection.execute(text("SELECT DATABASE()")).scalar_one() == database
            inspector = inspect(connection)
            for table in db.metadata.sorted_tables:
                assert set(table.columns.keys()) == {column['name'] for column in inspector.get_columns(table.name)}

    with tempfile.TemporaryDirectory(prefix="conejo-purchase-regression-") as folder:
        def factory(config):
            assert config.SQLALCHEMY_DATABASE_URI == engine.url.render_as_string(hide_password=False)
            app = original_factory(config)
            app.instance_path = folder
            apps.append(app)
            return app

        try:
            with control.begin() as connection:
                connection.execute(text(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4"))
                created = True
            support.DATABASE = database
            support.guard_engine(engine)
            db.metadata.create_all(engine)
            # The existing assertions still check the actual connection database,
            # inventory, transactions, locks, permissions, exports and reference writes.
            with patch.object(support, "create_app", side_effect=factory), \
                    patch.object(support, "migrate", side_effect=schema_assertion), \
                    patch.object(support, "migrate_approval", side_effect=schema_assertion):
                suite = importlib.import_module(suite_name)
                suite.DATABASE = database
                if suite_name == "test_receptions_core":
                    suite.run(no_pdf=True)
                else:
                    suite.run()
        finally:
            for app in apps:
                with app.app_context():
                    db.session.remove()
                    db.engine.dispose()
            engine.dispose()
            if created:
                validate_target(database)
                with control.begin() as connection:
                    connection.execute(text(f"DROP DATABASE `{database}`"))
                print("PASS: dropped only the random disposable database created for " + suite_name)
            control.dispose()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=("test_purchases_core", "test_receptions_core"))
    run(parser.parse_args().suite)
