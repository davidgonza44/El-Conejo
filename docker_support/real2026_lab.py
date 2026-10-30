"""Bootstrap only a new, explicitly identified 2026 laboratory, without Alembic."""
import argparse
import os
import re
from urllib.parse import urlparse
from urllib.request import urlopen

from sqlalchemy import create_engine, inspect, text

from docker_support.guard import validate, wait_database

MARKER = "real2026_lab_installation"
HISTORY = {"historical_2026_imports", "historical_2026_products", "historical_2026_daily"}


def validate_lab():
    database = validate()
    origin = urlparse(os.environ.get("APP_BASE_URL", ""))
    if (os.environ.get("REAL2026_ISOLATED") != "1"
            or os.environ.get("DOCKER_ENV_KIND") != "disposable"
            or not re.fullmatch(r"conejo_disposable_real2026_[a-f0-9]{8}", database)
            or origin.scheme != "http" or origin.hostname != "localhost"
            or origin.port in (None, 5000, 5001)):
        raise RuntimeError("Only a distinct, disposable real-2026 laboratory is permitted.")
    return database


def engine():
    validate_lab()
    from app.config import Config
    target = create_engine(Config.SQLALCHEMY_DATABASE_URI, hide_parameters=True, pool_pre_ping=True)
    if target.url.database != os.environ["DB_NAME"] or target.url.host != "db":
        raise RuntimeError("Laboratory engine identity mismatch.")
    return target


def verify(connection, require_ready=True):
    from scripts.migrate_real_2026_history import validate_isolated_mariadb
    validate_isolated_mariadb(connection)
    ready = connection.execute(text(f"SELECT ready FROM {MARKER} WHERE id=1")).scalar_one()
    if require_ready and not ready:
        raise RuntimeError("The laboratory initialization is incomplete.")


def prepare(connection):
    existing = set(inspect(connection).get_table_names())
    if existing:
        if MARKER not in existing:
            raise RuntimeError("An existing unmanaged database cannot become a laboratory.")
        verify(connection, require_ready=False)
        return
    connection.execute(text(f"CREATE TABLE {MARKER} (id INT PRIMARY KEY, "
        "environment_id VARCHAR(32) NOT NULL, kind VARCHAR(16) NOT NULL, "
        "ready BOOLEAN NOT NULL DEFAULT 0, admin_id INT NULL) ENGINE=InnoDB"))
    connection.execute(text(f"INSERT INTO {MARKER}(id,environment_id,kind) "
        "VALUES(1,:identity,'disposable')"), {"identity": os.environ["ENVIRONMENT_ID"]})
    connection.commit()


def initialize():
    validate_lab()
    wait_database()
    target = engine()
    with target.connect() as connection:
        prepare(connection)
        connection.commit()
    from app import create_app
    from app.extensions import db
    from app.services import auth_service, rbac_service
    from scripts.migrate_real_2026_history import apply_changes
    app = create_app()
    with app.app_context():
        with db.engine.begin() as connection:
            verify(connection, require_ready=False)
            # Fresh working-copy schema, not a claim of migration-chain compatibility.
            db.metadata.create_all(connection, tables=[table for table in db.metadata.sorted_tables
                                                      if table.name not in HISTORY])
            apply_changes(connection)
        rbac_service.ensure_seeded()
        admin_id = db.session.execute(text(f"SELECT admin_id FROM {MARKER} WHERE id=1")).scalar_one()
        if admin_id is None:
            admin = auth_service.register(dict(name="Administrador del laboratorio 2026",
                username=os.environ["ADMIN_USERNAME"], email=os.environ["ADMIN_EMAIL"],
                password=os.environ["ADMIN_PASSWORD"], role="admin"), allowed_roles=("admin",))
            db.session.execute(text(f"UPDATE {MARKER} SET admin_id=:id WHERE id=1"), {"id": admin.id})
        db.session.execute(text(f"UPDATE {MARKER} SET ready=1 WHERE id=1"))
        db.session.commit()
    print("Laboratory initialized: separate MariaDB, working-copy schema and three historical tables; no Alembic.")


def start():
    validate_lab()
    wait_database()
    with engine().connect() as connection:
        verify(connection)
    os.execvp("gunicorn", ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1",
        "--threads", "4", "--timeout", "180", "--access-logfile", "-", "app:create_app()"])


def health():
    with engine().connect() as connection:
        verify(connection)
    with urlopen("http://127.0.0.1:5000/", timeout=3) as response:
        if response.status != 200:
            raise RuntimeError("The laboratory HTTP service is unavailable.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "start", "health"))
    args = parser.parse_args()
    {"init": initialize, "start": start, "health": health}[args.action]()


if __name__ == "__main__":
    main()
