"""Instancia local desechable para probar en el navegador el alcance de ventas del vendedor.

SQLite en un directorio temporal del sistema. No abre .env ni ferreteria_conejo_tesis.

    .\\venv312\\Scripts\\python.exe -B scripts/serve_sales_scope_ui.py
"""
import os
import sqlite3
import sys
import tempfile
import uuid
from datetime import timedelta
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
from flask_login import login_user

from app import create_app
from app.extensions import db
from app.models import Category, Customer, Notification, Sale, User
from app.models.user import ROLE_ADMIN, ROLE_VENDEDOR
from app.services import product_service, sale_service
from app.services.sales_common import commercial_today, utc_boundary

PASSWORD_A = "VendedorA123"
PASSWORD_B = "VendedorB123"
PASSWORD_ADMIN = "AdminPrueba123"
HOST = "127.0.0.1"
PORT = 5057


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
    SECRET_KEY = "scope-ui-local-only"
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


def _noon(day):
    return utc_boundary(day) + timedelta(hours=16)


def _sell(app, user, customer_id, method, product_id, quantity):
    with app.test_request_context():
        login_user(user)
        sale, _created = sale_service.create_sale({
            "customer_id": customer_id,
            "payment_method": method,
            "payment_condition": "cash",
            "idempotency_key": uuid.uuid4().hex,
            "items": [{"product_id": product_id, "quantity": quantity}],
        })
        return sale.id


def main():
    folder = Path(tempfile.mkdtemp(prefix="sales-scope-ui-"))
    db_path = folder / "ui.sqlite"
    UiConfig.SQLALCHEMY_DATABASE_URI = "sqlite:///" + db_path.resolve().as_posix() + "?check_same_thread=false"
    if "ferreteria_conejo" in UiConfig.SQLALCHEMY_DATABASE_URI:
        raise SystemExit("La URI no es SQLite aislada; no se arranca.")

    app = create_app(UiConfig)
    app.instance_path = str((folder / "instance").resolve())
    (folder / "instance").mkdir(parents=True, exist_ok=True)

    today = commercial_today()
    with app.app_context():
        if db.engine.url.drivername != "sqlite":
            raise SystemExit("El motor no es SQLite; no se arranca.")
        db.create_all()
        admin = User(name="Administrador de prueba", email="admin.prueba@scope.local",
                     username="admin_prueba", role=ROLE_ADMIN)
        admin.set_password(PASSWORD_ADMIN)
        seller_a = User(name="Vendedor A", email="vendedor.a@scope.local",
                        username="vendedor_a", role=ROLE_VENDEDOR)
        seller_a.set_password(PASSWORD_A)
        seller_b = User(name="Vendedor B", email="vendedor.b@scope.local",
                        username="vendedor_b", role=ROLE_VENDEDOR)
        seller_b.set_password(PASSWORD_B)
        category = Category(name="Construcción")
        andes = Customer(name="Ferretería Los Andes", tax_id="J-30111222-3")
        delta = Customer(name="Constructora Delta", tax_id="J-30444555-6")
        pino = Customer(name="Taller El Pino", tax_id="J-30777888-9")
        db.session.add_all([admin, seller_a, seller_b, category, andes, delta, pino])
        db.session.commit()

        with app.test_request_context():
            login_user(admin)
            cemento = product_service.create_product({
                "code": "CEM-UI", "name": "Cemento Portland 50kg", "category_id": category.id,
                "current_stock": 500, "minimum_stock": 5, "purchase_price": "6.00", "sale_price": "10.00",
            }).id
            cable = product_service.create_product({
                "code": "CAB-UI", "name": "Cable THW 12", "category_id": category.id,
                "current_stock": 500, "minimum_stock": 5, "purchase_price": "3.00", "sale_price": "5.00",
            }).id

        plan = [
            ("A", seller_a, andes.id, "cash", cemento, 2, today, "Ferretería Los Andes"),
            ("A", seller_a, delta.id, "transfer", cable, 4, today - timedelta(days=1), "Constructora Delta"),
            ("A", seller_a, andes.id, "debit_card", cemento, 1, today - timedelta(days=2), "Ferretería Los Andes"),
            ("A", seller_a, delta.id, "credit_card", cable, 3, today - timedelta(days=5), "Constructora Delta"),
            ("A", seller_a, andes.id, "check", cemento, 5, today - timedelta(days=8), "Ferretería Los Andes"),
            ("B", seller_b, andes.id, "cash", cemento, 1, today, "Ferretería Los Andes"),
            ("B", seller_b, pino.id, "cash", cable, 2, today - timedelta(days=1), "Taller El Pino"),
            ("B", seller_b, andes.id, "transfer", cemento, 3, today - timedelta(days=3), "Ferretería Los Andes"),
            ("B", seller_b, pino.id, "credit_card", cable, 1, today - timedelta(days=6), "Taller El Pino"),
            ("B", seller_b, andes.id, "debit_card", cemento, 2, today - timedelta(days=10), "Ferretería Los Andes"),
        ]
        rows = []
        for owner, user, customer_id, method, product_id, quantity, day, customer_name in plan:
            sale_id = _sell(app, user, customer_id, method, product_id, quantity)
            sale = db.session.get(Sale, sale_id)
            sale.sold_at = _noon(day)
            db.session.commit()
            rows.append((owner, sale.sale_number, day.isoformat(), method, customer_name, sale.id))

        probe_id = next(sale_id for owner, _number, _day, _method, _customer, sale_id in rows if owner == "A")
        probe_number = next(number for owner, number, _day, _method, _customer, sale_id in rows if sale_id == probe_id)
        holders = {
            note.user_id: User.query.get(note.user_id).username
            for note in Notification.query.filter_by(entity_type="sale", entity_id=probe_id)
        }
        if "vendedor_a" not in holders.values() or "admin_prueba" not in holders.values():
            raise SystemExit(f"La venta {probe_number} no notificó a A y al admin: {sorted(holders.values())}")
        if "vendedor_b" in holders.values():
            raise SystemExit(f"La venta {probe_number} llegó a vendedor_b.")

    print("SCOPE_UI_READY")
    print(f"database={db_path}")
    print(f"login=http://{HOST}:{PORT}/login")
    print(f"ventas=http://{HOST}:{PORT}/sales")
    print(f"listado=http://{HOST}:{PORT}/sales/list")
    print(f"vendedor_a={PASSWORD_A}")
    print(f"vendedor_b={PASSWORD_B}")
    print(f"admin_prueba={PASSWORD_ADMIN}")
    print(f"notificacion_venta_A={probe_number}")
    for owner, number, day, method, customer_name, _sale_id in rows:
        print(f"sale {owner} {number} {day} {method} {customer_name}")
    sys.stdout.flush()
    app.run(host=HOST, port=PORT, debug=True, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
