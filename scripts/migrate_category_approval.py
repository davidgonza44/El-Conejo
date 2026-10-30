"""Migración aditiva e idempotente exclusiva de DEMO 5001.

Sin --apply no importa la configuración ni conecta. Conserva categorías y productos.
Las pruebas llaman apply_changes únicamente sobre SQLite desechable.
"""
import argparse
import os
import sys
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text
from app.utils.category_names import category_name_key


def _names(connection):
    rows = connection.execute(text("SELECT id, name FROM categories ORDER BY id")).all()
    keys = [(row.id, category_name_key(row.name)) for row in rows]
    if any(not key for _, key in keys) or len({key for _, key in keys}) != len(keys):
        raise RuntimeError("Existen nombres de categoría vacíos o equivalentes; se requiere revisión, sin cambios.")
    return keys


def _permissions(connection):
    from app.utils.permissions import permission_label

    for code in ("categories:propose", "categories:approve"):
        if not connection.execute(text("SELECT id FROM permissions WHERE code=:c"), {"c": code}).first():
            connection.execute(text("INSERT INTO permissions (code,module,label) VALUES (:c,'categories',:l)"),
                               {"c": code, "l": permission_label(code)})
    for code, slug in (("categories:propose", "asistente"), ("categories:approve", "admin"),
                       ("categories:write", "asistente"), ("categories:write", "admin")):
        connection.execute(text(
            "INSERT INTO role_permissions (role_id,permission_id) "
            "SELECT r.id,p.id FROM roles r CROSS JOIN permissions p WHERE r.slug=:r AND p.code=:c "
            "AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id)"),
            {"r": slug, "c": code})
    # Revocar permisos obsoletos incluso si se otorgaron manualmente a Inventario.
    connection.execute(text(
        "DELETE FROM role_permissions WHERE permission_id IN "
        "(SELECT id FROM permissions WHERE code='categories:write') AND role_id NOT IN "
        "(SELECT id FROM roles WHERE slug IN ('admin','asistente'))"))
    for code, slug in (("categories:propose", "asistente"), ("categories:approve", "admin")):
        connection.execute(text(
            "DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code=:c) "
            "AND role_id NOT IN (SELECT id FROM roles WHERE slug=:r)"), {"c": code, "r": slug})


def apply_changes(connection):
    required = {"categories", "users", "roles", "permissions", "role_permissions"}
    if not required.issubset(inspect(connection).get_table_names()):
        raise RuntimeError("Se requiere el esquema de categorías, usuarios y RBAC.")
    keys = _names(connection)  # Validar antes de cualquier DDL.
    columns = {column["name"] for column in inspect(connection).get_columns("categories")}
    additions = {
        "normalized_name": "VARCHAR(200) NULL",
        "approval_status": "VARCHAR(20) NOT NULL DEFAULT 'approved'",
        "proposed_by_user_id": "INTEGER NULL", "approved_by_user_id": "INTEGER NULL",
        "approved_at": "DATETIME NULL", "rejection_reason": "VARCHAR(1000) NULL",
    }
    for name, ddl in additions.items():
        if name not in columns:
            connection.execute(text(f"ALTER TABLE categories ADD COLUMN {name} {ddl}"))
    for category_id, key in keys:
        connection.execute(text("UPDATE categories SET normalized_name=:k WHERE id=:i AND "
                                "(normalized_name IS NULL OR normalized_name<>:k)"), {"k": key, "i": category_id})
    connection.execute(text("UPDATE categories SET approval_status='approved' "
                            "WHERE approval_status IS NULL OR approval_status=''"))
    indexes = inspect(connection).get_indexes("categories")
    uniques = inspect(connection).get_unique_constraints("categories")
    if not any(item["column_names"] == ["normalized_name"] for item in indexes + uniques):
        connection.execute(text("CREATE UNIQUE INDEX uq_categories_normalized_name ON categories (normalized_name)"))
    if connection.dialect.name == "mysql":
        connection.execute(text("ALTER TABLE categories MODIFY normalized_name VARCHAR(200) NOT NULL"))
        constraints = {item["name"] for item in inspect(connection).get_check_constraints("categories")}
        for name, expression in {
            "ck_category_approval": "approval_status IN ('pending','approved','rejected')",
            "ck_category_usable": "approval_status='approved' OR is_active=0",
        }.items():
            if name not in constraints:
                connection.execute(text(f"ALTER TABLE categories ADD CONSTRAINT {name} CHECK ({expression})"))
        foreign_columns = {tuple(item["constrained_columns"]) for item in inspect(connection).get_foreign_keys("categories")}
        for column in ("proposed_by_user_id", "approved_by_user_id"):
            if (column,) not in foreign_columns:
                connection.execute(text(f"ALTER TABLE categories ADD CONSTRAINT fk_categories_{column} "
                                        f"FOREIGN KEY ({column}) REFERENCES users(id)"))
    _permissions(connection)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print("Sin cambios. Use --apply solo dentro del contenedor DEMO 5001.")
        return
    from docker_support.guard import validate_demo, validate_demo_url
    validate_demo()
    from docker_support.bootstrap import engine
    from docker_support.demo import require_ready
    target = engine()
    validate_demo_url(target.url)
    with target.begin() as connection:
        require_ready(connection)
        if connection.execute(text("SELECT GET_LOCK('conejo.demo.category_approval',10)")).scalar_one() != 1:
            raise RuntimeError("Otra migración de categorías está en curso.")
        try:
            apply_changes(connection)
        finally:
            connection.execute(text("SELECT RELEASE_LOCK('conejo.demo.category_approval')"))
    target.dispose()
    print("PASS: aprobación y RBAC de categorías migrados en DEMO 5001; registros conservados.")


if __name__ == "__main__":
    main()
