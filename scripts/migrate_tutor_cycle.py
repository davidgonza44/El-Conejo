"""Migración aditiva del ciclo del tutor. Solo DEMO 5001, dentro del contenedor.

    python -B scripts/migrate_tutor_cycle.py
    python -B scripts/migrate_tutor_cycle.py --apply

Sin --apply no conecta. No toca ferreteria_conejo ni el puerto 5000.
"""
import argparse
import os
import sys
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text

GRANTS = {
    "asistente": {
        "products:read", "products:write", "products:view_cost", "products:view_price",
        "categories:write", "inventory:read", "purchases:read", "purchases:write",
        "purchases:close_request", "replenishment:read", "replenishment:process", "suppliers:read", "suppliers:propose",
        "supplier_quotes:read", "supplier_quotes:write", "predictions:read", "reports:read",
    },
    "admin": {
        "products:approve", "products:view_price", "suppliers:read", "suppliers:approve", "supplier_quotes:read",
        "purchases:close_approve", "replenishment:read",
    },
    "inventario": {"replenishment:create", "replenishment:read"},
    "vendedor": {"products:view_price"},
}
REVOKES = {
    "asistente": {"replenishment:create"},
    "inventario": {
        "purchases:write", "products:write", "products:view_cost", "categories:write",
        "customers:read", "sales:read", "delivery_notes:cancel",
    },
    "admin": {"products:write", "purchases:write", "purchases:receive", "replenishment:create"},
    "soporte_ti": {"products:view_cost", "products:view_price", "purchases:write", "replenishment:create"},
    "vendedor": {"products:view_cost", "purchases:write", "replenishment:create"},
}
NEW_CODES = sorted(set().union(*GRANTS.values()))


def _columns(inspector, table):
    if not inspector.has_table(table):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def _inspector(connection):
    inspector = inspect(connection)
    inspector.clear_cache()
    return inspector


def _add(connection, table, name, ddl):
    inspector = _inspector(connection)
    if name not in _columns(inspector, table):
        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def _unique(connection, table, index, column):
    inspector = _inspector(connection)
    if any(item["name"] == index for item in inspector.get_indexes(table)):
        return
    connection.execute(text(f"CREATE UNIQUE INDEX {index} ON {table} ({column})"))


def apply_changes(connection):
    from app.utils.permissions import permission_label

    inspector = inspect(connection)
    if not inspector.has_table("purchase_orders") or not inspector.has_table("roles"):
        raise RuntimeError("Se requiere el esquema de compras y de roles.")
    _add(connection, "products", "approval_status", "VARCHAR(20) NOT NULL DEFAULT 'approved'")
    _add(connection, "products", "proposed_by_user_id", "INTEGER NULL")
    _add(connection, "products", "approved_by_user_id", "INTEGER NULL")
    _add(connection, "products", "approved_at", "DATETIME NULL")
    _add(connection, "products", "rejection_reason", "VARCHAR(1000) NULL")
    _add(connection, "suppliers", "approval_status", "VARCHAR(20) NOT NULL DEFAULT 'approved'")
    _add(connection, "suppliers", "proposed_by_user_id", "INTEGER NULL")
    _add(connection, "suppliers", "approved_by_user_id", "INTEGER NULL")
    _add(connection, "suppliers", "approved_at", "DATETIME NULL")
    _add(connection, "suppliers", "rejection_reason", "VARCHAR(1000) NULL")
    _add(connection, "purchase_orders", "supplier_quote_id", "INTEGER NULL")
    _add(connection, "purchase_orders", "closed_at", "DATETIME NULL")
    _add(connection, "purchase_orders", "closed_by_user_id", "INTEGER NULL")
    _add(connection, "purchase_orders", "closure_reason", "VARCHAR(1000) NULL")
    _add(connection, "purchase_orders", "replenishment_request_id", "INTEGER NULL")
    _unique(connection, "purchase_orders", "uq_po_replenishment_request", "replenishment_request_id")
    _add(connection, "purchase_order_lines", "quantity_not_supplied", "INTEGER NOT NULL DEFAULT 0")
    connection.execute(text(
        "CREATE TABLE IF NOT EXISTS replenishment_requests ("
        "id INTEGER PRIMARY KEY AUTO_INCREMENT, request_number VARCHAR(20) NULL UNIQUE, "
        "status VARCHAR(20) NOT NULL, notes VARCHAR(1000) NULL, created_by_user_id INTEGER NOT NULL, "
        "purchase_order_id INTEGER NULL, created_at DATETIME NOT NULL)"))
    connection.execute(text(
        "CREATE TABLE IF NOT EXISTS replenishment_request_lines ("
        "id INTEGER PRIMARY KEY AUTO_INCREMENT, request_id INTEGER NOT NULL, product_id INTEGER NOT NULL, "
        "product_code VARCHAR(50) NOT NULL, product_name VARCHAR(150) NOT NULL, unit VARCHAR(30) NOT NULL, "
        "quantity INTEGER NOT NULL)"))
    _add(connection, "replenishment_requests", "origin", "VARCHAR(20) NULL")
    _add(connection, "replenishment_requests", "processed_by_user_id", "INTEGER NULL")
    _add(connection, "replenishment_requests", "processed_at", "DATETIME NULL")
    _add(connection, "replenishment_request_lines", "stock_on_hand", "INTEGER NULL")
    connection.execute(text(
        "CREATE TABLE IF NOT EXISTS supplier_quotes ("
        "id INTEGER PRIMARY KEY AUTO_INCREMENT, quote_number VARCHAR(20) NULL UNIQUE, supplier_id INTEGER NOT NULL, "
        "issued_on DATE NOT NULL, currency VARCHAR(3) NOT NULL, valid_until DATE NOT NULL, "
        "status VARCHAR(20) NOT NULL, notes VARCHAR(1000) NULL, created_by_user_id INTEGER NOT NULL, "
        "replaces_quote_id INTEGER NULL, created_at DATETIME NOT NULL)"))
    connection.execute(text(
        "CREATE TABLE IF NOT EXISTS supplier_quote_lines ("
        "id INTEGER PRIMARY KEY AUTO_INCREMENT, quote_id INTEGER NOT NULL, product_id INTEGER NOT NULL, "
        "product_code VARCHAR(50) NOT NULL, product_name VARCHAR(150) NOT NULL, quantity INTEGER NOT NULL, "
        "unit_price DECIMAL(12,2) NOT NULL)"))
    connection.execute(text(
        "CREATE TABLE IF NOT EXISTS purchase_closure_requests ("
        "id INTEGER PRIMARY KEY AUTO_INCREMENT, purchase_order_id INTEGER NOT NULL, kind VARCHAR(20) NOT NULL, "
        "reason VARCHAR(1000) NOT NULL, status VARCHAR(20) NOT NULL, requested_by_user_id INTEGER NOT NULL, "
        "requested_at DATETIME NOT NULL, decided_by_user_id INTEGER NULL, decided_at DATETIME NULL, "
        "decision_note VARCHAR(1000) NULL)"))
    dialect = connection.dialect
    exists = connection.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS "
        "WHERE TABLE_NAME = 'purchase_orders' AND CONSTRAINT_NAME = 'ck_po_status'")).scalar()
    if exists:
        drop = ("ALTER TABLE purchase_orders DROP CONSTRAINT ck_po_status"
                if getattr(dialect, "is_mariadb", False)
                else "ALTER TABLE purchase_orders DROP CHECK ck_po_status")
        connection.execute(text(drop))
    connection.execute(text(
        "ALTER TABLE purchase_orders ADD CONSTRAINT ck_po_status CHECK (status IN ("
        "'pending','partial','completed','cancelled','pending_approval','approved','rejected',"
        "'closed_complete','closed_partial','closed_unavailable','closed_price_change'))"))
    for code in NEW_CODES:
        connection.execute(text(
            "INSERT INTO permissions (code, module, label) SELECT :code, :module, :label "
            "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :code)"),
            {"code": code, "module": code.split(":")[0], "label": permission_label(code)})
    connection.execute(text(
        "INSERT INTO roles (slug, name, description, is_system, is_active, created_at, updated_at) "
        "SELECT 'asistente', 'Asistente administrativo', "
        "'Propone productos y proveedores, cotiza y prepara órdenes. No aprueba sus operaciones.', "
        "1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP WHERE NOT EXISTS (SELECT 1 FROM roles WHERE slug = 'asistente')"))
    for slug, codes in REVOKES.items():
        for code in sorted(codes):
            connection.execute(text(
                "DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE slug = :slug AND is_system = 1) "
                "AND permission_id IN (SELECT id FROM permissions WHERE code = :code)"),
                {"slug": slug, "code": code})
    for slug, codes in GRANTS.items():
        for code in sorted(codes):
            connection.execute(text(
                "INSERT INTO role_permissions (role_id, permission_id) "
                "SELECT r.id, p.id FROM roles r JOIN permissions p ON p.code = :code "
                "WHERE r.slug = :slug AND r.is_system = 1 AND NOT EXISTS ("
                "SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)"),
                {"slug": slug, "code": code})
    connection.execute(text("UPDATE products SET approval_status = 'approved' WHERE approval_status IS NULL OR approval_status = ''"))
    connection.execute(text("UPDATE suppliers SET approval_status = 'approved' WHERE approval_status IS NULL OR approval_status = ''"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print("PLAN ONLY: ciclo del tutor, sin conexión.")
        return
    from docker_support.guard import validate_demo

    validate_demo()
    from docker_support.bootstrap import engine
    from docker_support.demo import require_ready

    target = engine()
    try:
        with target.begin() as connection:
            require_ready(connection)
            apply_changes(connection)
        print("PASS: ciclo del tutor aplicado en DEMO.")
    finally:
        target.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Migración rechazada o incompleta. No se registraron credenciales.")
        raise SystemExit(1)
