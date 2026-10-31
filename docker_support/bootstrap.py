"""Idempotent installation on a new, explicitly isolated MariaDB only."""
import os
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, inspect, text

from docker_support.guard import wait_database, validate

HEAD = "0010_inventory_integrity"


def engine():
    validate()
    from app.config import Config
    target = create_engine(Config.SQLALCHEMY_DATABASE_URI, pool_pre_ping=True, hide_parameters=True)
    if os.environ["DOCKER_ENV_KIND"] == "demo":
        from docker_support.guard import validate_demo_url
        validate_demo_url(target.url)
    return target


def identity(connection):
    row = connection.execute(text("SELECT environment_id, kind, ready FROM container_installation WHERE id=1")).one()
    if row.environment_id != os.environ["ENVIRONMENT_ID"] or row.kind != os.environ["DOCKER_ENV_KIND"]:
        raise RuntimeError("Volume belongs to another environment; no writes allowed.")
    return row


def prepare_registry(connection):
    tables = set(inspect(connection).get_table_names())
    if tables and "container_installation" not in tables:
        raise RuntimeError("Unmanaged database: automatic adoption is forbidden.")
    if not tables:
        connection.execute(text("CREATE TABLE container_installation (id INT PRIMARY KEY, "
            "environment_id VARCHAR(32) NOT NULL, kind VARCHAR(16) NOT NULL, ready BOOLEAN NOT NULL DEFAULT 0, "
            "admin_id INT NULL, demo_ready BOOLEAN NOT NULL DEFAULT 0) ENGINE=InnoDB"))
        connection.execute(text("INSERT INTO container_installation(id,environment_id,kind) VALUES(1,:identity,:kind)"),
            {"identity": os.environ["ENVIRONMENT_ID"], "kind": os.environ["DOCKER_ENV_KIND"]})
        connection.commit()
    identity(connection)
    connection.commit()


def verify_installation():
    with engine().connect() as connection:
        if not identity(connection).ready:
            raise RuntimeError("Initialization has not completed.")
        if connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() != HEAD:
            raise RuntimeError("Schema version does not match this application.")
        if os.environ["DOCKER_ENV_KIND"] == "demo":
            from docker_support.demo import require_ready
            require_ready(connection)


def verify_schema(app):
    from app.extensions import db
    from scripts.migrate_reconciliation_cuts import verify_schema as verify_cuts
    from scripts.migrate_historical_imports import _verify_all
    with app.app_context():
        inspector = inspect(db.engine)
        for table in db.metadata.sorted_tables:
            actual = {column["name"] for column in inspector.get_columns(table.name)}
            if actual != set(table.columns.keys()):
                raise RuntimeError(f"Schema mismatch: {table.name}")
        with db.engine.connect() as connection:
            verify_cuts(connection, os.environ["DB_NAME"])
        if not _verify_all():
            raise RuntimeError("Historical schema/triggers verification failed.")


def ensure_admin():
    from app.extensions import db
    from app.models import User, AuthIdentity
    from app.services import auth_service
    existing_id = db.session.execute(text("SELECT admin_id FROM container_installation WHERE id=1")).scalar_one()
    if existing_id is not None:
        # After bootstrap, user management remains authoritative. Never restore
        # privileges, reactivate, reset a password or recreate a deleted account.
        return db.session.get(User, existing_id)
    username = os.environ["ADMIN_USERNAME"].strip().lower()
    email = os.environ["ADMIN_EMAIL"].strip().lower()
    password = os.environ["ADMIN_PASSWORD"]
    if len(password) < 16:
        raise RuntimeError("Initial administrator requires a password of at least 16 characters.")
    user = User.query.filter(db.or_(User.username == username, User.email == email)).first()
    if user:
        if not (user.username == username and user.email == email and user.role == "admin"
                and user.is_active and user.check_password(password)
                and AuthIdentity.query.filter_by(user_id=user.id, provider="local").first()):
            raise RuntimeError("Administrator identity collision; existing accounts are not modified.")
    else:
        user = auth_service.register(dict(name="Administrador del entorno aislado", username=username,
            email=email, password=password, role="admin"), allowed_roles=("admin",))
    db.session.execute(text("UPDATE container_installation SET admin_id=:id WHERE id=1"), {"id": user.id})
    db.session.commit()
    return user


def main():
    wait_database()
    target = engine()
    with target.connect() as lock:
        if lock.execute(text("SELECT GET_LOCK('conejo.container.init', 10)")).scalar() != 1:
            raise RuntimeError("Another initializer is running.")
        try:
            prepare_registry(lock)
            if os.environ["DOCKER_ENV_KIND"] == "demo":
                from docker_support.demo import preflight
                preflight(lock)
                lock.commit()
            command.upgrade(AlembicConfig(str(Path("alembic.ini"))), "head")
            from app import create_app
            from app.extensions import db
            from app.services import rbac_service
            app = create_app()
            verify_schema(app)
            with app.app_context():
                rbac_service.ensure_seeded()
                admin = ensure_admin()
                if os.environ["DOCKER_ENV_KIND"] == "demo":
                    from docker_support.demo import seed
                    seed(app, admin)
                    rbac_service.revoke_withdrawn_customer_sales()
                db.session.execute(text("UPDATE container_installation SET ready=1 WHERE id=1"))
                db.session.commit()
            print("Initialization verified: schema, triggers, RBAC and initial administrator. No secrets logged.")
        finally:
            lock.execute(text("SELECT RELEASE_LOCK('conejo.container.init')"))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # SQLAlchemy errors can embed parameters, connection URLs or private values.
        print(f"Initialization failed ({type(error).__name__}); application remains stopped.")
        if isinstance(error, RuntimeError):
            print(str(error))
        raise SystemExit(1)
