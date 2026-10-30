"""Create one local demo admin, using the existing registration/password policy.

No dotenv, application factory, seeding, login session, email, or schema changes.
Only TESIS_ADMIN_PASSWORD supplies the password; command-line arguments are rejected.
The operator must explicitly configure localhost / ferreteria_conejo_tesis.
"""
from contextlib import contextmanager
import logging
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import seed_tesis_safety as safety

USERNAME = "admin_tesis"
EMAIL = "admin_tesis@example.invalid"  # Local-only placeholder; no mail is sent.


class AdminError(ValueError):
    """Only deliberately authored, non-sensitive operator messages."""


def require(condition, message):
    if not condition:
        raise AdminError(message)


def validate_url(url):
    from sqlalchemy.engine import make_url

    url = make_url(url)
    # configured_url already requires DB_HOST=localhost and pins its loopback address.
    require(url.drivername == "mysql+pymysql" and url.host in ("127.0.0.1", "::1")
            and url.port == 3306 and url.database == safety.DATABASE
            and dict(url.query) == {"charset": "utf8mb4"}, "Destino efectivo de conexión no autorizado.")


def load_auth():
    # Importing app would otherwise import Config and attempt to read .env.
    with patch("dotenv.load_dotenv", return_value=False):
        from app.extensions import db
        from app import models
        from app.services import auth_service, rbac_service
        from app.utils.permissions import ALL_PERMISSIONS
    return SimpleNamespace(**locals())


def build_app(url, domain):
    from flask import Flask

    app = Flask("create_tesis_admin")
    app.config.update(SQLALCHEMY_DATABASE_URI=url, SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      SQLALCHEMY_ECHO=False, SQLALCHEMY_ENGINE_OPTIONS={"hide_parameters": True})
    domain.db.init_app(app)
    return app


def preflight(connection):
    from sqlalchemy import MetaData, inspect, text

    require(connection.dialect.name == "mysql", "Solo se permite MySQL/MariaDB local.")
    # First application query, before any user/role/settings read or mutation.
    require(connection.execute(text("SELECT DATABASE()")).scalar() == safety.DATABASE,
            "La base seleccionada no es ferreteria_conejo_tesis; operación cancelada.")
    require(connection.execute(text("SELECT 1 FROM information_schema.KEY_COLUMN_USAGE WHERE "
            "(TABLE_SCHEMA=DATABASE() AND REFERENCED_TABLE_SCHEMA<>DATABASE()) OR "
            "(REFERENCED_TABLE_SCHEMA=DATABASE() AND TABLE_SCHEMA<>DATABASE()) LIMIT 1")).first() is None,
            "Hay relaciones entre bases; operación cancelada.")
    engines = dict(connection.execute(text("SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
                                           "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE'")).all())
    required = {"users", "auth_identities", "roles", "permissions", "role_permissions"}
    require(required <= set(engines), "Faltan tablas de autenticación/RBAC; no se creará ni migrará el esquema.")
    require(all(engine == "InnoDB" for engine in engines.values()), "Se requiere almacenamiento transaccional InnoDB.")
    require(connection.execute(text("SELECT 1 FROM information_schema.TRIGGERS "
                                    "WHERE TRIGGER_SCHEMA=DATABASE() LIMIT 1")).first() is None,
            "Hay triggers no revisados; operación cancelada.")
    metadata = MetaData()
    metadata.reflect(bind=connection, only=inspect(connection).get_table_names())
    return metadata.tables


@contextmanager
def bound_connection(domain, connection):
    """Bind this CLI app only; the shared session object/configuration is untouched.

    An existing external savepoint makes Flask-SQLAlchemy's conditional_savepoint
    joining release only its own savepoint when register() calls session.commit().
    The enclosing transaction remains ours until invariants have been checked.
    """
    domain.db.session.remove()
    engines = domain.db.engines
    original = engines[None]
    engines[None] = connection
    try:
        yield
    finally:
        domain.db.session.remove()
        engines[None] = original


def fingerprint(connection, tables, excluded=None):
    from sqlalchemy import select

    excluded = excluded or {}
    result = {}
    for name, table in tables.items():
        query = select(table)
        if name in excluded:
            query = query.where(table.c.id != excluded[name])
        # Only row digests remain in memory; no values/hashes are printed or saved.
        hashes = sorted(safety.digest(dict(row)) for row in connection.execute(query).mappings())
        result[name] = (len(hashes), safety.digest(hashes))
    return result


def existing_admin_role(domain):
    require(domain.rbac_service.is_available(), "Faltan tablas RBAC; no se sembrarán roles.")
    role = domain.models.Role.query.filter_by(slug="admin").first()
    require(role is not None and role.is_active, "Debe existir el rol admin activo; no se sembrarán roles.")
    require(domain.ALL_PERMISSIONS <= set(role.permission_codes),
            "El rol admin existente no tiene todos los permisos; no se modificarán sus permisos.")


def verify_user(domain, password):
    user = domain.models.User.query.filter_by(username=USERNAME).one()
    require(user.name == "Administrador" and user.email == EMAIL and user.role == "admin"
            and user.is_active and user.check_password(password), "No se pudo verificar la cuenta creada.")
    existing_admin_role(domain)
    identities = domain.models.AuthIdentity.query.filter_by(user_id=user.id).all()
    require(len(identities) == 1 and identities[0].provider == "local"
            and identities[0].provider_user_id == str(user.id) and identities[0].email == EMAIL,
            "La identidad local no coincide con la cuenta.")
    return user.id, identities[0].id


def only_auth_inserts(_connection, _cursor, statement, _parameters, context, _many):
    from sqlalchemy.sql.dml import Insert

    command = statement.lstrip().split(None, 1)[0].upper()
    if command in ("SELECT", "SHOW", "SAVEPOINT", "RELEASE", "ROLLBACK"):
        return
    compiled = context.compiled.statement if context.compiled is not None else None
    require(isinstance(compiled, Insert) and compiled.table.name in ("users", "auth_identities")
            and compiled.table.schema in (None, safety.DATABASE),
            "El registro intentó una escritura fuera de la cuenta autorizada; se revierte todo.")


def create_account(domain, connection, tables, password):
    from sqlalchemy import event, select

    before = fingerprint(connection, tables)
    existing_admin_role(domain)
    require(domain.models.User.query.filter_by(username=USERNAME).first() is None,
            "admin_tesis ya existe; no se actualizará ni reemplazará.")
    require(domain.models.User.query.filter_by(email=EMAIL).first() is None,
            "El correo local reservado ya está en uso; no se actualizará ninguna cuenta.")
    # Complete optional-table reflection before installing the strict mutation guard.
    domain.auth_service.validate_password(password)
    event.listen(connection, "before_cursor_execute", only_auth_inserts)
    try:
        domain.auth_service.register(dict(name="Administrador", username=USERNAME, email=EMAIL,
                                          role="admin", password=password), allowed_roles=None)
        user_id, identity_id = verify_user(domain, password)
        excluded = {"users": user_id, "auth_identities": identity_id}
        require(fingerprint(connection, tables, excluded) == before,
                "Se detectaron cambios distintos de la cuenta autorizada; se revierte todo.")
        for name in excluded:
            count = len(connection.execute(select(tables[name].c.id)).all())
            require(count == before[name][0] + 1, "La cantidad de filas creadas no es la esperada.")
        return excluded
    finally:
        event.remove(connection, "before_cursor_execute", only_auth_inserts)


def run(password):
    url = safety.configured_url()
    validate_url(url)
    domain = load_auth()
    app = build_app(url, domain)
    committed = False
    with app.app_context():
        engine = domain.db.engine
        try:
            with engine.connect() as connection:
                tables = preflight(connection)
                connection.rollback()
                with connection.begin(), connection.begin_nested(), bound_connection(domain, connection):
                    create_account(domain, connection, tables, password)
                committed = True
                # Read-only credential predicate, not login(): no sessions/login events.
                with connection.begin(), connection.begin_nested(), bound_connection(domain, connection):
                    verify_user(domain, password)
        except Exception:
            if committed:
                raise AdminError("La creación se confirmó, pero falló la verificación posterior. No reintente a ciegas.") from None
            raise
        finally:
            domain.db.session.remove()
            engine.dispose()


def main():
    password = os.environ.pop("TESIS_ADMIN_PASSWORD", None)
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        require(len(sys.argv) == 1, "Este comando no acepta argumentos; la contraseña solo se recibe por entorno temporal.")
        require(bool(password), "Falta TESIS_ADMIN_PASSWORD; no se abrió ninguna conexión.")
        run(password)
        print("Creado admin_tesis (Administrador), activo y con el rol admin completo.")
        print("Contraseña e identidad local verificadas en solo lectura; no se inició una sesión web.")
        print("Cambios: 1 usuario y 1 identidad local; la comprobación transaccional no detectó otros cambios.")
        return 0
    except (AdminError, safety.SeedError) as error:
        print(f"ABORTADO: {error}", file=sys.stderr)
        return 1
    except (Exception, KeyboardInterrupt):
        print("ABORTADO: no se pudo completar la operación. Verifique el estado antes de reintentar; no se muestran credenciales.", file=sys.stderr)
        return 1
    finally:
        password = None
        logging.disable(previous_logging)


if __name__ == "__main__":
    raise SystemExit(main())
