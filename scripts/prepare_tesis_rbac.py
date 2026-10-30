"""Prepare only missing standard RBAC rows in the explicitly selected local thesis DB.

Reuse the real RBAC service under an external transaction, with fail-closed target,
insert-only and all-table integrity checks. No users, schema, dotenv or app factory.
"""
import logging
import os
import sys

import create_tesis_admin as admin


def configured_url():
    admin.require(os.getenv("DB_ALLOW_EMPTY_PASSWORD") == "1",
                  "Se requiere DB_ALLOW_EMPTY_PASSWORD=1 explícito para esta preparación local.")
    url = admin.safety.configured_url()
    admin.validate_url(url)
    admin.require(url.username == "root" and url.password == "",
                  "La preparación requiere la conexión local root sin contraseña autorizada.")
    return url


def verify_database(connection):
    from sqlalchemy import text

    admin.require(connection.dialect.name == "mysql"
                  and connection.execute(text("SELECT DATABASE()")).scalar() == admin.safety.DATABASE,
                  "La base efectiva no es ferreteria_conejo_tesis; operación cancelada.")


def catalog(domain):
    """Validate existing rows without normalizing operator-editable role text."""
    service = domain.rbac_service
    admin.require(service.is_available(), "Faltan tablas RBAC; prepare el esquema por separado.")
    permissions = {row.code: row for row in domain.models.Permission.query.all()}
    roles = {row.slug: row for row in domain.models.Role.query.all()}
    for code in service.ALL_PERMISSIONS & permissions.keys():
        row = permissions[code]
        admin.require(row.module == code.split(":")[0] and row.label == service.permission_label(code),
                      "Un permiso estándar existente tiene metadatos incompatibles; no se modificará.")
    for slug in service.ROLES:
        if slug not in roles:
            continue
        row = roles[slug]
        # update_role explicitly permits changing name/description; preserve both.
        admin.require(row.is_active and row.is_system
                      and row.permission_codes == service.ROLE_PERMISSIONS[slug],
                      "Un rol estándar existente es incompatible; no se modificará.")
    return permissions, roles


def fingerprint(connection, tables, created=None):
    from sqlalchemy import select

    created = created or {}
    result = {}
    for name, table in tables.items():
        query = select(table)
        ids = created.get("roles" if name == "role_permissions" else name, ())
        if ids:
            column = table.c.role_id if name == "role_permissions" else table.c.id
            query = query.where(column.not_in(ids))
        hashes = sorted(admin.safety.digest(dict(row)) for row in connection.execute(query).mappings())
        result[name] = (len(hashes), admin.safety.digest(hashes))
    return result


def only_rbac_inserts(_connection, _cursor, statement, _parameters, context, _many):
    from sqlalchemy.sql.dml import Insert

    command = statement.lstrip().split(None, 1)[0].upper()
    if command in ("SELECT", "SHOW", "SAVEPOINT", "RELEASE", "ROLLBACK"):
        return
    compiled = context.compiled.statement if context.compiled is not None else None
    admin.require(isinstance(compiled, Insert)
                  and compiled.table.name in ("roles", "permissions", "role_permissions")
                  and compiled.table.schema in (None, admin.safety.DATABASE),
                  "Se intentó una escritura fuera del RBAC faltante; se revierte todo.")


def prepare_catalog(domain, connection, tables):
    from sqlalchemy import event, select

    before = fingerprint(connection, tables)
    permissions, roles = catalog(domain)
    service = domain.rbac_service
    missing_permissions = service.ALL_PERMISSIONS - permissions.keys()
    missing_roles = set(service.ROLES) - roles.keys()
    event.listen(connection, "before_cursor_execute", only_rbac_inserts)
    try:
        # Recheck immediately before handing control to the only writing service.
        verify_database(connection)
        service.ensure_seeded()
        after_permissions, after_roles = catalog(domain)
        admin.require(set(after_permissions) == set(permissions) | missing_permissions
                      and set(after_roles) == set(roles) | missing_roles,
                      "El catálogo resultante no coincide con las inserciones autorizadas.")
        for slug in missing_roles:
            role = after_roles[slug]
            admin.require(role.name == service.SYSTEM_ROLE_NAMES[slug]
                          and role.description == service.SYSTEM_ROLE_DESCRIPTIONS[slug],
                          "Un rol nuevo no coincide con la definición del sistema.")
        created = {"roles": {after_roles[slug].id for slug in missing_roles},
                   "permissions": {after_permissions[code].id for code in missing_permissions}}
        links = tables["role_permissions"]
        expected_links = {(after_roles[slug].id, after_permissions[code].id)
                          for slug in missing_roles for code in service.ROLE_PERMISSIONS[slug]}
        actual_links = set(connection.execute(select(links.c.role_id, links.c.permission_id)
                           .where(links.c.role_id.in_(created["roles"]))).all())
        admin.require(actual_links == expected_links, "Los vínculos RBAC nuevos no son los autorizados.")
        admin.require(fingerprint(connection, tables, created) == before,
                      "Se detectaron cambios ajenos al RBAC faltante; se revierte todo.")
        return {"roles": len(created["roles"]), "permissions": len(created["permissions"]),
                "role_permissions": len(expected_links)}
    finally:
        event.remove(connection, "before_cursor_execute", only_rbac_inserts)


def run():
    url = configured_url()
    domain = admin.load_auth()
    app = admin.build_app(url, domain)
    committed = False
    with app.app_context():
        engine = domain.db.engine
        try:
            with engine.connect() as connection:
                tables = admin.preflight(connection)  # SELECT DATABASE is the first application query.
                connection.rollback()
                with connection.begin(), connection.begin_nested(), admin.bound_connection(domain, connection):
                    delta = prepare_catalog(domain, connection, tables)
                    expected = fingerprint(connection, tables)
                committed = True
                with connection.begin(), connection.begin_nested(), admin.bound_connection(domain, connection):
                    verify_database(connection)
                    catalog(domain)
                    admin.require(fingerprint(connection, tables) == expected,
                                  "La verificación posterior detectó un estado diferente.")
                return delta
        except Exception:
            if committed:
                raise admin.AdminError("La preparación se confirmó, pero falló la verificación posterior. "
                                       "Revise el estado antes de reintentar.") from None
            raise
        finally:
            domain.db.session.remove()
            engine.dispose()


def main():
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        admin.require(len(sys.argv) == 1, "Este comando no acepta argumentos; use el entorno explícito.")
        delta = run()
        print("RBAC estándar preparado y verificado en ferreteria_conejo_tesis.")
        print(f"Filas creadas: {delta['roles']} roles, {delta['permissions']} permisos, "
              f"{delta['role_permissions']} vínculos.")
        print("Usuarios y demás tablas sin cambios; no se creó ninguna cuenta ni sesión web.")
        return 0
    except (admin.AdminError, admin.safety.SeedError) as error:
        print(f"ABORTADO: {error}", file=sys.stderr)
        return 1
    except (Exception, KeyboardInterrupt):
        print("ABORTADO: no se pudo completar la preparación. Revise el estado antes de reintentar; "
              "no se muestran detalles de conexión.", file=sys.stderr)
        return 1
    finally:
        logging.disable(previous_logging)


if __name__ == "__main__":
    raise SystemExit(main())
