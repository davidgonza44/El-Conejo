"""Instancia local desechable para probar Conciliación en el navegador.

SQLite en un directorio temporal. No abre .env ni ferreteria_conejo_tesis
y no inserta conciliaciones.

    .\\venv312\\Scripts\\python.exe -B scripts/serve_reconciliation_ui.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["PYTHONUNBUFFERED"] = "1"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import BigInteger
from sqlalchemy.engine import Engine
from sqlalchemy.event import listens_for
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.extensions import db
from app.models import Category, Product, User
from app.models.user import ROLE_ADMIN, ROLE_INVENTARIO
from app.services import rbac_service

PASSWORD_ADMIN = "AdminPrueba123"
PASSWORD_INVENTARIO = "InventarioPrueba123"
HOST = "127.0.0.1"
PORT = 5061


@compiles(BigInteger, "sqlite")
def _compile_big_integer_for_sqlite(_type, _compiler, **_kwargs):
    return "INTEGER"


@listens_for(Engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class UiConfig:
    SECRET_KEY = "reconciliation-ui-local-only"
    DEBUG = True
    APP_ENV = "testing"
    TESTING = False
    WTF_CSRF_ENABLED = False
    MAIL_ENABLED = False
    GOOGLE_CLIENT_ID = None
    GOOGLE_CLIENT_SECRET = None
    GOOGLE_REDIRECT_URI = None
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"check_same_thread": False, "timeout": 30}}


def main():
    folder = Path(tempfile.mkdtemp(prefix="reconciliation-ui-"))
    db_path = folder / "ui.sqlite"
    csv_path = folder / "conteo_prueba.csv"
    UiConfig.SQLALCHEMY_DATABASE_URI = "sqlite:///" + db_path.resolve().as_posix() + "?check_same_thread=false"
    if "ferreteria_conejo" in UiConfig.SQLALCHEMY_DATABASE_URI:
        raise SystemExit("La URI no es SQLite aislada; no se arranca.")

    app = create_app(UiConfig)
    app.instance_path = str((folder / "instance").resolve())
    (folder / "instance").mkdir(parents=True, exist_ok=True)

    with app.app_context():
        if db.engine.url.drivername != "sqlite":
            raise SystemExit("El motor no es SQLite; no se arranca.")
        db.create_all()
        rbac_service.ensure_seeded()
        admin = User(name="Administrador de prueba", email="admin.prueba@recon.local",
                     username="admin_prueba", role=ROLE_ADMIN)
        admin.set_password(PASSWORD_ADMIN)
        inventario = User(name="Inventario de prueba", email="inventario.prueba@recon.local",
                          username="inventario_prueba", role=ROLE_INVENTARIO)
        inventario.set_password(PASSWORD_INVENTARIO)
        category = Category(name="Conteo de prueba")
        products = [
            Product(code="REC-IGUAL", name="Cemento coincidente", category_id=None,
                    current_stock=12, minimum_stock=0, purchase_price=6, sale_price=10, is_active=True),
            Product(code="REC-MAS", name="Cable sobrante", category_id=None,
                    current_stock=8, minimum_stock=0, purchase_price=3, sale_price=5, is_active=True),
            Product(code="REC-MENOS", name="Disco faltante", category_id=None,
                    current_stock=20, minimum_stock=0, purchase_price=2, sale_price=4, is_active=True),
        ]
        db.session.add(category)
        db.session.flush()
        for product in products:
            product.category_id = category.id
        db.session.add_all([admin, inventario, *products])
        db.session.commit()

    csv_path.write_text(
        "product_code;quantity;product_name\n"
        "REC-IGUAL;12;Cemento coincidente\n"
        "REC-MAS;11;Cable sobrante\n"
        "REC-MENOS;15;Disco faltante\n"
        "NO-EXISTE;4;Código que no está en el catálogo\n",
        encoding="utf-8",
    )

    print("RECON_UI_READY")
    print(f"database={db_path}")
    print(f"csv={csv_path}")
    print(f"login=http://{HOST}:{PORT}/login")
    print(f"reconciliation=http://{HOST}:{PORT}/reconciliation")
    print(f"admin_prueba={PASSWORD_ADMIN}")
    print(f"inventario_prueba={PASSWORD_INVENTARIO}")
    sys.stdout.flush()
    app.run(host=HOST, port=PORT, debug=True, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
