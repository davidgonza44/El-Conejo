"""Additive MySQL/MariaDB migration; explicit target, no dotenv, seed or status backfill.

DDL may auto-commit: every step is restartable. Running on the thesis database
requires a separate operator authorization AFTER isolated validation results.
"""
import argparse
import os
import re
import sys
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text
from app.models.reconciliation import ReconciliationRun, ReconciliationLine
from app.utils.permissions import permission_label
from migrate_purchase_orders import engine_for

STATES = "'pending','partial','completed','cancelled','pending_approval','approved','rejected'"
COLUMNS = {
    "approved_by_user_id": "INTEGER NULL", "approved_at": "DATETIME NULL",
    "rejected_by_user_id": "INTEGER NULL", "rejected_at": "DATETIME NULL",
    "rejection_reason": "VARCHAR(1000) NULL",
}
GRANTS = {
    "admin": ("purchases:read", "purchases:approve", "purchases:cancel",
              "reconciliation:read", "reconciliation:create", "reconciliation:resolve"),
    "inventario": ("purchases:read", "purchases:write", "purchases:receive"),
}


def validate_target(database, authorize_thesis=False):
    allowed = database in ("ferreteria_conejo", "ferreteria_conejo_e2e", "ferreteria_conejo_tesis")
    if not allowed and not re.fullmatch(r"conejo_approval_test_[a-f0-9]{12}", database):
        raise RuntimeError("Explicit allowed database required.")
    if database == "ferreteria_conejo_tesis" and not authorize_thesis:
        raise RuntimeError("Thesis migration blocked: separate express operator authorization required.")


def _purchase_schema(connection):
    actual = {column["name"] for column in inspect(connection).get_columns("purchase_orders")}
    for name, definition in COLUMNS.items():
        if name not in actual:
            connection.execute(text(f"ALTER TABLE purchase_orders ADD COLUMN {name} {definition}"))
    indexes = {index["name"] for index in inspect(connection).get_indexes("purchase_orders")}
    foreign_keys = {tuple(fk["constrained_columns"]) for fk in inspect(connection).get_foreign_keys("purchase_orders")}
    for name in ("approved_by_user_id", "rejected_by_user_id"):
        if "ix_purchase_orders_" + name not in indexes:
            connection.execute(text(f"CREATE INDEX ix_purchase_orders_{name} ON purchase_orders ({name})"))
        if (name,) not in foreign_keys:
            connection.execute(text(f"ALTER TABLE purchase_orders ADD CONSTRAINT fk_po_{name} FOREIGN KEY ({name}) REFERENCES users(id)"))
    checks = {row["name"]: row["sqltext"] for row in inspect(connection).get_check_constraints("purchase_orders")}
    current = checks.get("ck_po_status", "")
    if not all(state in current for state in ("pending_approval", "approved", "rejected")):
        clauses = []
        if current:
            keyword = "CONSTRAINT" if connection.dialect.is_mariadb else "CHECK"
            clauses.append(f"DROP {keyword} ck_po_status")
        clauses.append(f"ADD CONSTRAINT ck_po_status CHECK (status IN ({STATES}))")
        connection.execute(text("ALTER TABLE purchase_orders " + ", ".join(clauses)))


def _permissions(connection):
    for slug, codes in GRANTS.items():
        for code in codes:
            connection.execute(text("INSERT INTO permissions (code,module,label) VALUES (:code,:module,:label) "
                                    "ON DUPLICATE KEY UPDATE code=VALUES(code)"),
                               {"code": code, "module": code.split(":")[0], "label": permission_label(code)})
            connection.execute(text("INSERT IGNORE INTO role_permissions (role_id,permission_id) "
                                    "SELECT r.id,p.id FROM roles r JOIN permissions p ON p.code=:code WHERE r.slug=:slug"),
                               {"code": code, "slug": slug})


def migrate(engine, database, *, authorize_thesis=False):
    validate_target(database, authorize_thesis)
    if engine.url.database != database:
        raise RuntimeError("Engine target mismatch; no connection or mutation allowed.")
    with engine.begin() as connection:
        if connection.execute(text("SELECT DATABASE()")).scalar() != database:
            raise RuntimeError("Database mismatch; no mutation allowed.")
        inspector = inspect(connection)
        required = ("users", "products", "stock_movements", "purchase_orders", "roles", "permissions", "role_permissions")
        if not all(inspector.has_table(name) for name in required):
            raise RuntimeError("Existing purchase, inventory and RBAC schema required.")
        _purchase_schema(connection)
        for model in (ReconciliationRun, ReconciliationLine):
            model.__table__.dialect_options["mysql"]["engine"] = "InnoDB"
            model.__table__.dialect_options["mysql"]["charset"] = "utf8mb4"
            model.__table__.create(connection, checkfirst=True)
            actual = {column["name"] for column in inspect(connection).get_columns(model.__tablename__)}
            if set(model.__table__.columns.keys()) - actual:
                raise RuntimeError("Existing reconciliation schema incompatible; operator review required.")
        _permissions(connection)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--apply", action="store_true", help="Execute DDL and base-role grants")
    parser.add_argument("--authorize-thesis", action="store_true", help="Only after express user authorization")
    args = parser.parse_args()
    validate_target(args.database, args.authorize_thesis)
    if not args.apply:
        print("PLAN ONLY: nullable purchase decision columns, expanded status CHECK, reconciliation tables and base-role grants.")
        print("No database connection. No purchase status changes, stock changes or seed execution.")
        return
    engine = engine_for(args.database)
    try:
        migrate(engine, args.database, authorize_thesis=args.authorize_thesis)
        print("PASS: additive migration and base-role grants; business rows preserved.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
