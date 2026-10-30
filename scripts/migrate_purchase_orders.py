"""Additive, idempotent purchase schema and four dynamic permissions.

Uses explicit DB_* process environment (never reads .env). No app startup or data seed.
Production requires an explicit --database ferreteria_conejo operator invocation.
    python -B scripts/migrate_purchase_orders.py --database ferreteria_conejo_e2e
"""
import argparse
import os
import sys
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL
from app.models.purchase import Supplier, PurchaseOrder, PurchaseOrderLine, PurchaseReception, PurchaseReceptionLine

TABLES = [model.__table__ for model in (Supplier, PurchaseOrder, PurchaseOrderLine, PurchaseReception, PurchaseReceptionLine)]


def engine_for(database):
    return create_engine(URL.create("mysql+pymysql", username=os.environ.get("DB_USER", "root"),
                                   password=os.environ.get("DB_PASSWORD", ""), host=os.environ.get("DB_HOST", "127.0.0.1"),
                                   port=int(os.environ.get("DB_PORT", "3306")), database=database, query={"charset": "utf8mb4"}),
                         isolation_level="READ COMMITTED")


def migrate(engine, database):
    if database not in ("ferreteria_conejo", "ferreteria_conejo_e2e"):
        raise RuntimeError("Database must be explicitly selected.")
    created = []
    with engine.begin() as connection:
        if connection.execute(text("SELECT DATABASE()")).scalar() != database:
            raise RuntimeError("Database mismatch; no mutation allowed.")
        inspector = inspect(connection)
        if not all(inspector.has_table(name) for name in ("users", "products", "stock_movements", "roles", "permissions", "role_permissions")):
            raise RuntimeError("Requires existing inventory and dynamic RBAC migrations.")
        for table in TABLES:
            if not inspector.has_table(table.name):
                table.dialect_options["mysql"]["engine"] = "InnoDB"
                table.dialect_options["mysql"]["charset"] = "utf8mb4"
                table.create(connection, checkfirst=True)
                created.append(table.name)
            else:
                actual = {column["name"] for column in inspect(connection).get_columns(table.name)}
                if set(table.columns.keys()) - actual:
                    raise RuntimeError(f"Existing table {table.name} is incompatible; operator review required.")
        for action, label in (("read", "Ver compras"), ("write", "Crear y editar compras"), ("receive", "Registrar recepciones"), ("cancel", "Cancelar compras")):
            code = "purchases:" + action
            connection.execute(text("INSERT INTO permissions (code,module,label) VALUES (:code,'purchases',:label) ON DUPLICATE KEY UPDATE code=VALUES(code)"), {"code": code, "label": label})
            from app.utils.permissions import ROLE_PERMISSIONS
            for slug, permissions in ROLE_PERMISSIONS.items():
                if code in permissions:
                    connection.execute(text("INSERT IGNORE INTO role_permissions (role_id,permission_id) SELECT r.id,p.id FROM roles r JOIN permissions p ON p.code=:code WHERE r.slug=:slug"), {"code": code, "slug": slug})
    return created


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, choices=("ferreteria_conejo", "ferreteria_conejo_e2e"))
    args = parser.parse_args()
    engine = engine_for(args.database)
    try:
        created = migrate(engine, args.database)
        print("PASS: purchase schema ready; created: " + (", ".join(created) or "none (idempotent)"))
        print("Permissions catalog and system-role assignments ensured; no business rows inserted.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
