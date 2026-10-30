"""Add delivery-note movement links without backfill.

Disposable names delivery_note_traceability_test_<12 hex> are accepted.
ferreteria_conejo_tesis is refused unless authorize_thesis is set. That flag
never authorizes another database. Default is a connection-free plan.
"""
import argparse
import os
import re

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL

COLUMNS = ("issue_stock_movement_id", "reversal_stock_movement_id")
THESIS = "ferreteria_conejo_tesis"


def validate_target(database, authorize_thesis=False):
    if database == THESIS and authorize_thesis:
        return
    if database == THESIS:
        raise RuntimeError("Thesis migration requires --authorize-thesis; no connection or write performed.")
    if not re.fullmatch(r"delivery_note_traceability_test_[a-f0-9]{12}", database or ""):
        raise RuntimeError("Only explicitly named disposable delivery-note databases are allowed.")


def engine_for(database, host, port, user, password="", authorize_thesis=False):
    validate_target(database, authorize_thesis=authorize_thesis)
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("Only loopback servers are allowed.")
    return create_engine(URL.create("mysql+pymysql", username=user, password=password,
                                   host=host, port=port, database=database,
                                   query={"charset": "utf8mb4"}))


def _schema(connection):
    inspector = inspect(connection)
    for table in ("delivery_note_items", "stock_movements"):
        if not inspector.has_table(table):
            raise RuntimeError("Required delivery-note/inventory table is missing.")
        if inspector.get_table_options(table).get("mysql_engine", "").lower() != "innodb":
            raise RuntimeError("Foreign keys require InnoDB tables.")
    target = next(c for c in inspector.get_columns("stock_movements") if c["name"] == "id")
    if target["type"].__class__.__name__ != "INTEGER" or getattr(target["type"], "unsigned", False):
        raise RuntimeError("Stock movement primary key must be signed INTEGER.")
    columns = {c["name"]: c for c in inspector.get_columns("delivery_note_items")}
    unique = inspector.get_unique_constraints("delivery_note_items")
    indexes = inspector.get_indexes("delivery_note_items")
    foreign = inspector.get_foreign_keys("delivery_note_items")
    existing = []
    for name in COLUMNS:
        if name not in columns:
            continue
        column = columns[name]
        if (column["type"].__class__.__name__ != "INTEGER" or getattr(column["type"], "unsigned", False)
                or not column["nullable"] or column.get("default") not in (None, "NULL")):
            raise RuntimeError("Incompatible movement-link column; no automatic repair.")
        links = [fk for fk in foreign if name in fk["constrained_columns"]]
        valid_fk = (len(links) == 1 and links[0]["constrained_columns"] == [name]
                    and links[0]["referred_table"] == "stock_movements"
                    and links[0]["referred_columns"] == ["id"]
                    and links[0].get("referred_schema") in (None, connection.engine.url.database)
                    and all(str(links[0].get("options", {}).get(key, "RESTRICT")).upper()
                            in ("RESTRICT", "NO ACTION") for key in ("ondelete", "onupdate")))
        has_unique = any(u["column_names"] == [name] for u in unique) or any(
            idx.get("unique") and idx.get("column_names") == [name] for idx in indexes)
        if not valid_fk or not has_unique:
            raise RuntimeError("Partial/incompatible movement-link constraints; operator review required.")
        existing.append(name)
    if existing and len(existing) != len(COLUMNS):
        raise RuntimeError("Partial movement-link schema; operator review required.")
    return existing


def migrate(engine, database, authorize_thesis=False):
    validate_target(database, authorize_thesis=authorize_thesis)
    if engine.url.database != database or engine.url.host not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("Target mismatch; connection refused.")
    with engine.begin() as connection:
        if connection.execute(text("SELECT DATABASE()")).scalar() != database:
            raise RuntimeError("Connected database mismatch; mutation refused.")
        if _schema(connection):
            return
        additions = []
        for phase, name in zip(("issue", "reversal"), COLUMNS):
            additions.extend((f"ADD COLUMN {name} INT NULL",
                              f"ADD CONSTRAINT uq_delivery_item_{phase}_movement UNIQUE ({name})",
                              f"ADD CONSTRAINT fk_delivery_item_{phase}_movement FOREIGN KEY ({name}) REFERENCES stock_movements(id)"))
        # One additive DDL statement; MySQL DDL is not transactionally reversible.
        connection.execute(text("ALTER TABLE delivery_note_items " + ", ".join(additions)))
        _schema(connection)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--host", choices=("127.0.0.1", "localhost", "::1"))
    parser.add_argument("--port", type=int)
    parser.add_argument("--user")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--authorize-thesis", action="store_true",
                        help="Required together with --apply to alter ferreteria_conejo_tesis.")
    args = parser.parse_args()
    validate_target(args.database, authorize_thesis=args.authorize_thesis)
    if not args.apply:
        print("PLAN ONLY: two nullable unique INTEGER FKs; no connection, UPDATE or backfill.")
        return
    host = args.host or "127.0.0.1"
    port = args.port or int(os.environ.get("DB_PORT", "3306"))
    user = args.user or os.environ.get("DB_USER", "root")
    if args.database != THESIS and not all((args.host, args.port, args.user)):
        parser.error("--apply requires explicit --host, --port and --user for a disposable server")
    password = os.environ.get("DB_PASSWORD", "") if args.database == THESIS else ""
    engine = engine_for(args.database, host, port, user, password, authorize_thesis=args.authorize_thesis)
    try:
        migrate(engine, args.database, authorize_thesis=args.authorize_thesis)
        print("PASS: additive delivery-note links; historical rows unchanged.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
