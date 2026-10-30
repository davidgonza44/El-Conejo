"""Add only nullable movement origin/external reference. No backfill or app startup.

Random disposable conejo_trace_test_<12 hex> databases are permitted for tests.
The thesis database requires the explicit --authorize-thesis opt-in.
Without --apply, prints the plan without connecting. Credentials: process DB_* only.
"""
import argparse
import os
import re

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from sqlalchemy import inspect, text
from migrate_purchase_orders import engine_for

COLUMNS = {"creation_origin": 20, "external_reference": 120}
CHECKS = {
    "ck_movement_creation_origin": "creation_origin IS NULL OR creation_origin IN ('manual','initial_stock')",
    "ck_movement_external_manual": "external_reference IS NULL OR (creation_origin IS NOT NULL AND creation_origin = 'manual')",
}


def validate_target(database, *, authorize_thesis=False):
    if database == "ferreteria_conejo_tesis" and authorize_thesis:
        return
    if not re.fullmatch(r"conejo_trace_test_[a-f0-9]{12}", database):
        raise RuntimeError("Only disposable traceability databases are permitted; thesis requires --authorize-thesis.")


def _normalized(sql):
    return re.sub(r"[\s`()]+", "", sql).lower()


def _validate_columns(columns):
    for name, length in COLUMNS.items():
        if name not in columns:
            continue
        column = columns[name]
        if (column["type"].__class__.__name__ != "VARCHAR" or column["type"].length != length
                or not column["nullable"] or column.get("default") not in (None, "NULL")):
            raise RuntimeError("Existing traceability column is incompatible; no automatic repair.")


def migrate(engine, database, *, authorize_thesis=False):
    validate_target(database, authorize_thesis=authorize_thesis)
    if engine.url.database != database or engine.url.host not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("Target mismatch or non-loopback server; no connection allowed.")
    with engine.begin() as connection:
        if connection.execute(text("SELECT DATABASE()")).scalar() != database:
            raise RuntimeError("Database mismatch; no mutation allowed.")
        inspector = inspect(connection)
        columns = {column["name"]: column for column in inspector.get_columns("stock_movements")}
        _validate_columns(columns)
        checks = {check["name"]: check["sqltext"] for check in inspector.get_check_constraints("stock_movements")}
        for name, sql in CHECKS.items():
            if name in checks and _normalized(checks[name]) != _normalized(sql):
                raise RuntimeError("Existing traceability CHECK is incompatible; no automatic repair.")
        # MySQL DDL auto-commits. Each additive step can be safely resumed.
        for name, length in COLUMNS.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE stock_movements ADD COLUMN {name} VARCHAR({length}) NULL"))
        for name, sql in CHECKS.items():
            if name not in checks:
                connection.execute(text(f"ALTER TABLE stock_movements ADD CONSTRAINT {name} CHECK ({sql})"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--authorize-thesis", action="store_true",
                        help="Explicitly authorize this additive migration on ferreteria_conejo_tesis.")
    args = parser.parse_args()
    validate_target(args.database, authorize_thesis=args.authorize_thesis)
    if not args.apply:
        print("PLAN ONLY: creation_origin VARCHAR(20) NULL; external_reference VARCHAR(120) NULL; two CHECKs.")
        print("No connection, no defaults, no UPDATE, no backfill.")
        return
    engine = engine_for(args.database)
    try:
        migrate(engine, args.database, authorize_thesis=args.authorize_thesis)
        print("PASS: additive movement traceability migration; no business rows changed.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
