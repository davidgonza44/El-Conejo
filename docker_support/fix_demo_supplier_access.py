"""Repara solo suppliers:read en los roles aprobados de DEMO 5001.

python -m docker_support.fix_demo_supplier_access          # plan, sin conexión
python -m docker_support.fix_demo_supplier_access --apply  # reparación idempotente
No migra tablas, resiembra datos ni modifica volúmenes.
"""
import argparse

from sqlalchemy import text


def grant_read(connection):
    """Añade únicamente las dos asociaciones faltantes; conserva el RBAC restante."""
    roles = connection.execute(text(
        "SELECT id, slug FROM roles WHERE slug IN ('admin', 'asistente') "
        "AND is_system = 1 AND is_active = 1"
    )).all()
    if {row.slug for row in roles} != {"admin", "asistente"}:
        raise RuntimeError("Se requieren ambos roles del sistema activos; reparación cancelada.")
    permission_id = connection.execute(text(
        "SELECT id FROM permissions WHERE code = 'suppliers:read'"
    )).scalar_one_or_none()
    if permission_id is None:
        from app.utils.permissions import permission_label

        connection.execute(text(
            "INSERT INTO permissions (code, module, label) VALUES ('suppliers:read', 'suppliers', :label)"
        ), {"label": permission_label("suppliers:read")})
        permission_id = connection.execute(text(
            "SELECT id FROM permissions WHERE code = 'suppliers:read'"
        )).scalar_one()
    added = 0
    for role_id, _slug in roles:
        result = connection.execute(text(
            "INSERT INTO role_permissions (role_id, permission_id) "
            "SELECT :role_id, :permission_id WHERE NOT EXISTS ("
            "SELECT 1 FROM role_permissions WHERE role_id = :role_id AND permission_id = :permission_id)"
        ), {"role_id": role_id, "permission_id": permission_id})
        added += result.rowcount
    return added


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print("PLAN: añadir solo suppliers:read a Admin y Asistente de DEMO 5001; sin conexión.")
        return
    from docker_support.guard import validate_demo
    from docker_support.bootstrap import engine
    from docker_support.demo import require_ready

    validate_demo()
    target = engine()
    try:
        with target.begin() as connection:
            require_ready(connection)
            added = grant_read(connection)
        print(f"PASS DEMO 5001: {added} asociaciones suppliers:read añadidas; resto del RBAC intacto.")
    finally:
        target.dispose()


if __name__ == "__main__":
    main()
