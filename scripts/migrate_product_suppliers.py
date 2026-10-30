"""Additive empty product_suppliers table. No backfill and no seed.

Disposable names conejo_ps_test_<12 hex> are accepted. ferreteria_conejo and
ferreteria_conejo_e2e are refused. ferreteria_conejo_tesis is refused unless the
operator passes --authorize-thesis together with --apply. Uses DB_* from the
process environment and does not read .env.

    python -B scripts/migrate_product_suppliers.py --database conejo_ps_test_<12 hex> --apply
    python -B scripts/migrate_product_suppliers.py --database ferreteria_conejo_tesis --apply --authorize-thesis
"""
import argparse
import os
import re
import sys
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text
from app.models.purchase import ProductSupplier
from migrate_purchase_orders import engine_for

DISPOSABLE = re.compile(r"conejo_ps_test_[a-f0-9]{12}")


def validate_target(database, authorize_thesis=False):
    if isinstance(database, str) and DISPOSABLE.fullmatch(database):
        return
    if database == "ferreteria_conejo_tesis" and authorize_thesis:
        return
    raise RuntimeError("Explicit disposable database required. Thesis requires --authorize-thesis; shared databases are refused.")


def migrate(engine, database, *, authorize_thesis=False):
    validate_target(database, authorize_thesis)
    if engine.url.database != database:
        raise RuntimeError("Engine target mismatch; no connection or mutation allowed.")
    with engine.begin() as connection:
        if connection.execute(text("SELECT DATABASE()")).scalar() != database:
            raise RuntimeError("Database mismatch; no mutation allowed.")
        inspector = inspect(connection)
        if not inspector.has_table("products") or not inspector.has_table("suppliers"):
            raise RuntimeError("products and suppliers tables are required.")
        table = ProductSupplier.__table__
        table.dialect_options["mysql"]["engine"] = "InnoDB"
        table.dialect_options["mysql"]["charset"] = "utf8mb4"
        table.create(connection, checkfirst=True)
        actual = {column["name"] for column in inspect(connection).get_columns(table.name)}
        if set(table.columns.keys()) - actual:
            raise RuntimeError("Existing product_suppliers schema is incompatible; operator review required.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--authorize-thesis", action="store_true",
                        help="Required together with --apply to write ferreteria_conejo_tesis")
    args = parser.parse_args()
    validate_target(args.database, args.authorize_thesis)
    if not args.apply:
        print("PLAN ONLY: empty product_suppliers table. No connection, backfill or seed.")
        return
    engine = engine_for(args.database)
    try:
        migrate(engine, args.database, authorize_thesis=args.authorize_thesis)
        print("PASS: product_suppliers ready; no business rows inserted.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
