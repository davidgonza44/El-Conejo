"""Add only missing columns to the canonical suppliers table; explicit database required."""
import argparse

from sqlalchemy import inspect, text
from migrate_purchase_orders import engine_for

COLUMNS = {
    "contact_title": "VARCHAR(150) NULL", "address": "VARCHAR(300) NULL",
    "city": "VARCHAR(100) NULL", "state_province": "VARCHAR(100) NULL",
    "category": "VARCHAR(100) NULL", "is_preferred": "BOOLEAN NOT NULL DEFAULT 0",
    "notes": "VARCHAR(300) NULL",
}


def migrate(engine, database):
    if database not in ("ferreteria_conejo", "ferreteria_conejo_e2e"):
        raise RuntimeError("Explicit supported database required.")
    added = []
    with engine.begin() as connection:
        if connection.execute(text("SELECT DATABASE()")).scalar() != database:
            raise RuntimeError("Database mismatch; no writes allowed.")
        existing = {column["name"] for column in inspect(connection).get_columns("suppliers")}
        for name, definition in COLUMNS.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE suppliers ADD COLUMN {name} {definition}"))
                added.append(name)
    return added


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, choices=("ferreteria_conejo", "ferreteria_conejo_e2e"))
    args = parser.parse_args()
    engine = engine_for(args.database)
    try:
        print("PASS: added columns: " + (", ".join(migrate(engine, args.database)) or "none (idempotent)"))
    finally:
        engine.dispose()
