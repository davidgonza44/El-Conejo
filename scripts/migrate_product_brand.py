"""Additive nullable products.brand. No backfill and no seed.

Disposable names conejo_brand_test_<12 hex> are accepted. ferreteria_conejo and
ferreteria_conejo_e2e are refused. ferreteria_conejo_tesis is refused unless the
operator passes --authorize-thesis together with --apply. Uses DB_* from the
process environment and does not read .env.

    python -B scripts/migrate_product_brand.py --database conejo_brand_test_<12 hex> --apply
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

DISPOSABLE = re.compile(r"conejo_brand_test_[a-f0-9]{12}")


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
        if not inspector.has_table("products"):
            raise RuntimeError("products table is required.")
        columns = {column["name"]: column for column in inspector.get_columns("products")}
        if "brand" not in columns:
            connection.execute(text("ALTER TABLE products ADD COLUMN brand VARCHAR(100) NULL"))
            columns = {column["name"]: column for column in inspect(connection).get_columns("products")}
        brand = columns.get("brand")
        if brand is None or brand.get("nullable") is not True:
            raise RuntimeError("products.brand must be a nullable VARCHAR(100); operator review required.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--authorize-thesis", action="store_true",
                        help="Required together with --apply to write ferreteria_conejo_tesis")
    args = parser.parse_args()
    validate_target(args.database, args.authorize_thesis)
    if not args.apply:
        print("PLAN ONLY: nullable products.brand. No connection, backfill or seed.")
        return
    engine = engine_for(args.database)
    try:
        migrate(engine, args.database, authorize_thesis=args.authorize_thesis)
        print("PASS: products.brand ready; no product rows updated.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
