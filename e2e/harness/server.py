"""Servidor E2E desechable: SQLite temporal + dataset sintético (e2e/harness/seed.py).

Nunca lee .env ni toca MySQL. Cada arranque crea una base nueva en un directorio
temporal y la borra al salir. Usuarios de prueba: e2e/fixtures/users.json.

Uso: .venv/Scripts/python -B e2e/harness/server.py  (puerto E2E_PORT, por defecto 5099)
"""
import atexit
import json
import logging
import os
import secrets
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
for name in ("DEMO_MODE", "DOCKER_ENV_KIND", "CONEJO_CONTAINER"):
    os.environ.pop(name, None)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import BigInteger  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.event import listens_for  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402

from app import create_app  # noqa: E402
from app.config import Config  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User  # noqa: E402
from app.services import rbac_service  # noqa: E402
from seed import seed  # noqa: E402

TEMP = Path(tempfile.mkdtemp(prefix="conejo_e2e_"))
atexit.register(shutil.rmtree, TEMP, ignore_errors=True)
USERS = json.loads((ROOT / "e2e" / "fixtures" / "users.json").read_text(encoding="utf-8"))


@compiles(BigInteger, "sqlite")
def _big_integer(_type, _compiler, **_kwargs):
    return "INTEGER"


@listens_for(Engine, "connect")
def _sqlite_pragmas(connection, _record):
    if isinstance(connection, sqlite3.Connection):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")


class E2EConfig(Config):
    SECRET_KEY = secrets.token_urlsafe(32)
    APP_ENV = "testing"
    APP_BASE_URL = "http://127.0.0.1"
    MAIL_ENABLED = False
    NOTIFICATIONS_DRY_RUN = True
    WHATSAPP_ENABLED = False
    SMS_ENABLED = False
    GOOGLE_CLIENT_ID = GOOGLE_CLIENT_SECRET = GOOGLE_REDIRECT_URI = None
    GOOGLE_DRIVE_CLIENT_ID = GOOGLE_DRIVE_CLIENT_SECRET = GOOGLE_DRIVE_REDIRECT_URI = None
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + (TEMP / "e2e.sqlite").as_posix()
    SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"check_same_thread": False, "timeout": 30}}
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MYSQLDUMP_BIN = MYSQL_BIN = ""
    BACKUP_SCHEDULER = "portable"
    RECONCILIATION_AUTO_INGEST_ENABLED = False


def _user(role):
    spec = USERS[role]
    user = User(name=spec["name"], username=spec["username"], email=spec["email"], role=role)
    user.set_password(spec["password"])
    db.session.add(user)
    return user


def make_app():
    app = create_app(E2EConfig)
    app.instance_path = str(TEMP / "instance")
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    with app.app_context():
        db.create_all()
        rbac_service.ensure_seeded()
        users = {role: _user(role) for role in USERS if not role.startswith("_")}
        db.session.commit()
        with app.test_request_context():
            print("Seed E2E:", seed(users), flush=True)
    return app


if __name__ == "__main__":
    port = int(os.environ.get("E2E_PORT", "5099"))
    application = make_app()
    # El reporte de Playwright muestra solo advertencias y errores del servidor, no cada petición.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    print(f"E2E listo en http://127.0.0.1:{port} (base temporal {TEMP.name})", flush=True)
    application.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)
