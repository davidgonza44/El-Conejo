"""Additive MySQL/MariaDB migration: replenishment_requests.source_purchase_order_id.

Links a new replenishment request to the closed purchase order whose shortage it repeats.
Nullable; existing requests keep NULL. It never fills historical references by inference.

Default targets: disposable conejo_tutor_test_<12 hex> and ferreteria_conejo_e2e.
The deployment authorized on 17 October 2026 additionally permits conejo_demo_8b867d4b
only with --authorize-demo-5001, matching runtime variables and database identity.
Thesis, production and all other targets remain blocked. DEMO rollback is forbidden;
restore the previous application image while retaining the additive schema.

Usage (connection from DB_HOST, DB_PORT, DB_USER, DB_PASSWORD; no dotenv):
    python scripts/migrate_replenishment_source_order.py --database conejo_tutor_test_<hex>            # plan only
    python scripts/migrate_replenishment_source_order.py --database conejo_tutor_test_<hex> --apply    # repeatable
    python scripts/migrate_replenishment_source_order.py --database conejo_tutor_test_<hex> --rollback

Rollback procedure: deploy the previous application code first (it does not read the column),
then run --rollback. It drops the foreign key, the index and the column, in that order, and
refuses while any request holds a link, so recorded shortage chains are never discarded silently.
DDL auto-commits in MySQL/MariaDB; every step checks the current schema and can be re-run.
"""
import argparse
import os
import re
import sys
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text
from migrate_purchase_orders import engine_for

TABLE, COLUMN = "replenishment_requests", "source_purchase_order_id"
INDEX, FOREIGN_KEY = "ix_replenishment_requests_source_purchase_order_id", "fk_sr_source_purchase_order"


E2E_DATABASE = "ferreteria_conejo_e2e"  # disposable browser/E2E database; its harness guards every write


DEMO_DATABASE = "conejo_demo_8b867d4b"
DEMO_IDENTITY = "8b867d4bbfce4447bf646e1b56330d75"


def validate_target(database, authorize_demo=False):
    if authorize_demo:
        expected = {"DB_NAME": DEMO_DATABASE, "DB_HOST": "db", "DB_PORT": "3306",
                    "ENVIRONMENT_ID": DEMO_IDENTITY, "DOCKER_ENV_KIND": "demo",
                    "APP_BASE_URL": "http://localhost:5001", "CONEJO_CONTAINER": "1"}
        if database != DEMO_DATABASE or any(os.environ.get(k) != v for k, v in expected.items()):
            raise RuntimeError("DEMO authorization does not match the verified deployment.")
        return
    if database != E2E_DATABASE and not re.fullmatch(r"conejo_tutor_test_[a-f0-9]{12}", database or ""):
        raise RuntimeError("Only disposable conejo_tutor_test_<hex> or ferreteria_conejo_e2e databases; "
                           "other targets need separate authorization.")


def _checked(engine, database, authorize_demo=False):
    validate_target(database, authorize_demo)
    if engine.url.database != database:
        raise RuntimeError("Engine target mismatch; no connection or mutation allowed.")


def _state(connection):
    inspector = inspect(connection)
    for name in (TABLE, "purchase_orders"):
        if not inspector.has_table(name):
            raise RuntimeError(f"Existing table {name} required.")
    return ({column["name"] for column in inspector.get_columns(TABLE)},
            {index["name"] for index in inspector.get_indexes(TABLE)},
            {fk["name"] for fk in inspector.get_foreign_keys(TABLE) if fk["constrained_columns"] == [COLUMN]})


def plan(connection):
    columns, indexes, foreign_keys = _state(connection)
    steps = []
    if COLUMN not in columns:
        steps.append(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} INTEGER NULL")
    if INDEX not in indexes:
        steps.append(f"CREATE INDEX {INDEX} ON {TABLE} ({COLUMN})")
    if not foreign_keys:
        steps.append(f"ALTER TABLE {TABLE} ADD CONSTRAINT {FOREIGN_KEY} FOREIGN KEY ({COLUMN}) "
                     "REFERENCES purchase_orders(id) ON DELETE RESTRICT")
    return steps


def verify_demo_identity(connection):
    row = connection.execute(text("SELECT environment_id, kind, ready, demo_ready "
                                  "FROM container_installation WHERE id=1")).one()
    if tuple(row) != (DEMO_IDENTITY, "demo", 1, 1):
        raise RuntimeError("DEMO database identity mismatch.")


def migrate(engine, database, authorize_demo=False):
    _checked(engine, database, authorize_demo)
    with engine.begin() as connection:
        if connection.execute(text("SELECT DATABASE()")).scalar() != database:
            raise RuntimeError("Database mismatch; no mutation allowed.")
        if authorize_demo:
            verify_demo_identity(connection)
        before = connection.execute(text(f"SELECT COUNT(*) FROM {TABLE}")).scalar()
        steps = plan(connection)
        for statement in steps:
            connection.execute(text(statement))
        if plan(connection):
            raise RuntimeError("Incomplete additive schema.")
        linked = connection.execute(text(f"SELECT COUNT(*) FROM {TABLE} WHERE {COLUMN} IS NOT NULL")).scalar()
        if connection.execute(text(f"SELECT COUNT(*) FROM {TABLE}")).scalar() != before:
            raise RuntimeError("Row count changed during an additive migration.")
    return steps, linked


def rollback(engine, database):
    _checked(engine, database)
    with engine.begin() as connection:
        if connection.execute(text("SELECT DATABASE()")).scalar() != database:
            raise RuntimeError("Database mismatch; no mutation allowed.")
        columns, indexes, foreign_keys = _state(connection)
        if COLUMN not in columns:
            return []
        if connection.execute(text(f"SELECT 1 FROM {TABLE} WHERE {COLUMN} IS NOT NULL LIMIT 1")).first():
            raise RuntimeError("Rollback refused: shortage links exist. Export and review them before any removal.")
        steps = [f"ALTER TABLE {TABLE} DROP FOREIGN KEY {name}" for name in sorted(foreign_keys)]
        if INDEX in indexes:
            steps.append(f"DROP INDEX {INDEX} ON {TABLE}")
        steps.append(f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}")
        for statement in steps:
            connection.execute(text(statement))
    return steps


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True)
    parser.add_argument("--authorize-demo-5001", action="store_true",
                        help="Explicit authorization only for the verified 8b867d4b DEMO deployment.")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    validate_target(args.database, args.authorize_demo_5001)
    if args.rollback and args.authorize_demo_5001:
        raise RuntimeError("Destructive rollback is not authorized for DEMO.")
    engine = engine_for(args.database)
    try:
        if args.rollback:
            print(f"ROLLBACK: {len(rollback(engine, args.database))} statements; no request rows changed.")
        elif args.apply:
            steps, linked = migrate(engine, args.database, args.authorize_demo_5001)
            print(f"APPLIED: {len(steps)} additive statements; {linked} linked requests; history unchanged.")
        else:
            with engine.connect() as connection:
                if connection.execute(text("SELECT DATABASE()")).scalar() != args.database:
                    raise RuntimeError("Database mismatch.")
                if args.authorize_demo_5001:
                    verify_demo_identity(connection)
                steps = plan(connection)
            print(f"PLAN: {len(steps)} additive statements pending.")
            for statement in steps:
                print("  " + statement)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
