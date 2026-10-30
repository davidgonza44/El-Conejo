"""Instancia local desechable para revisar Producto–Proveedor en el navegador.

SQLite temporal. No abre .env ni escribe en ferreteria_conejo_tesis.

    .\\venv312\\Scripts\\python.exe -B scripts/serve_product_supplier_ui.py
"""
import os
import sqlite3
import sys
import tempfile
from datetime import timedelta
from decimal import Decimal
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
from app.models import Category, Customer, Product, User
from app.models.purchase import ProductSupplier, Supplier
from app.models.sale import Sale
from app.models.sale_line import SaleLine
from app.models.stock_movement import StockMovement
from app.models.user import ROLE_ADMIN
from app.services import predictive_analysis_service, product_supplier_service, rbac_service
from app.services.demand_analysis_service import institutional_today
from app.services.demand_data_service import commercial_datetime_as_utc_naive

PASSWORD = "PsUiPrueba123"
HOST = "127.0.0.1"
PORT = 5062


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
    SECRET_KEY = "product-supplier-ui-local-only"
    DEBUG = True
    APP_ENV = "testing"
    TESTING = False
    WTF_CSRF_ENABLED = True
    MAIL_ENABLED = False
    GOOGLE_CLIENT_ID = None
    GOOGLE_CLIENT_SECRET = None
    GOOGLE_REDIRECT_URI = None
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"check_same_thread": False, "timeout": 30}}


def _sale(admin_id, customer_id, product, day, sequence):
    when = commercial_datetime_as_utc_naive(day, 10, 0)
    movement = StockMovement(product_id=product.id, movement_type="salida", quantity=2,
                             previous_stock=10, new_stock=8, reason="Venta de demostración",
                             user_id=admin_id, created_at=when)
    db.session.add(movement)
    db.session.flush()
    sale = Sale(sale_number=f"VTA-UI-{sequence:04d}", customer_id=customer_id, seller_id=admin_id,
                sold_at=when, payment_method="cash", payment_condition="cash",
                subtotal=Decimal("4.00"), discount_total=0, total=Decimal("4.00"),
                status="completed", idempotency_key=f"ps-ui-{product.code}-{day.isoformat()}")
    db.session.add(sale)
    db.session.flush()
    db.session.add(SaleLine(sale_id=sale.id, product_id=product.id, stock_movement_id=movement.id,
                            product_code=product.code, product_name=product.name, quantity=2,
                            unit_price=product.sale_price, discount_percent=0, discount_amount=0,
                            line_subtotal=Decimal("4.00"), line_total=Decimal("4.00"), created_at=when))


def _link(product_id, supplier_id, code, price, lead, minimum, preferred):
    db.session.add(ProductSupplier(
        product_id=product_id, supplier_id=supplier_id, supplier_product_code=code,
        purchase_price=Decimal(price), lead_time_days=lead, minimum_order_quantity=minimum,
        is_preferred=preferred, is_active=True,
    ))


def main():
    folder = Path(tempfile.mkdtemp(prefix="product-supplier-ui-"))
    db_path = folder / "ui.sqlite"
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
        admin = User(name="Administrador de prueba", email="ps.admin@example.invalid",
                     username="ps_admin", role=ROLE_ADMIN)
        admin.set_password(PASSWORD)
        category = Category(name="Ferretería de prueba")
        customer = Customer(name="Cliente de prueba", tax_id="V-PSUI-1", is_active=True)
        db.session.add_all([admin, category, customer])
        db.session.flush()
        products = {
            "tornillo": Product(code="TOR-UI", name="Tornillo zinc 1 pulgada", category_id=category.id,
                                unit="unidad", current_stock=0, minimum_stock=5, purchase_price=Decimal("10.00"),
                                sale_price=Decimal("14.00"), is_active=True),
            "tuerca": Product(code="TUE-UI", name="Tuerca hexagonal", category_id=category.id,
                              unit="unidad", current_stock=0, minimum_stock=4, purchase_price=Decimal("3.00"),
                              sale_price=Decimal("4.50"), is_active=True),
            "disco": Product(code="DIS-UI", name="Disco de corte", category_id=category.id,
                             unit="unidad", current_stock=0, minimum_stock=3, purchase_price=Decimal("8.00"),
                             sale_price=Decimal("12.00"), is_active=True),
            "clavo": Product(code="CLA-UI", name="Clavo suelto", category_id=category.id,
                             unit="unidad", current_stock=20, minimum_stock=2, purchase_price=Decimal("1.50"),
                             sale_price=Decimal("2.40"), is_active=True),
        }
        suppliers = {
            "aceros": Supplier(name="Aceros del Centro", tax_id="J-PSUI-001", contact_name="Ana Rivas",
                               phone="0241-0000001", email="aceros@example.invalid", category="Aceros",
                               is_active=True, is_preferred=False),
            "maderas": Supplier(name="Maderas Andinas", tax_id="J-PSUI-002", contact_name="Luis Peña",
                                phone="0241-0000002", email="maderas@example.invalid", category="Maderas",
                                is_active=True, is_preferred=False),
            "pinturas": Supplier(name="Pinturas El Lago", tax_id="J-PSUI-003", contact_name="Mara Díaz",
                                 phone="0241-0000003", email="pinturas@example.invalid", category="Pinturas",
                                 is_active=True, is_preferred=False),
            "vieja": Supplier(name="Ferretería Vieja", tax_id="J-PSUI-004", contact_name="Archivo",
                              phone="0241-0000004", email="vieja@example.invalid", category="General",
                              is_active=False, is_preferred=False),
        }
        db.session.add_all([*products.values(), *suppliers.values()])
        db.session.flush()
        _link(products["tornillo"].id, suppliers["aceros"].id, "AC-TOR-1", "4.00", 5, 20, True)
        _link(products["tornillo"].id, suppliers["maderas"].id, "MAD-TOR-9", "6.50", 9, 12, False)
        _link(products["tuerca"].id, suppliers["maderas"].id, "MAD-TUE-2", "2.25", 3, 30, True)
        _link(products["disco"].id, suppliers["vieja"].id, "VIE-DIS-1", "5.40", 12, 6, True)
        _link(products["disco"].id, suppliers["pinturas"].id, "PIN-DIS-4", "7.10", 4, 8, False)
        today = institutional_today()
        sequence = 1
        for product in (products["tornillo"], products["tuerca"], products["disco"]):
            for offset in (1, 3, 5, 8):
                _sale(admin.id, customer.id, product, today - timedelta(days=offset), sequence)
                sequence += 1
        db.session.commit()
        analysis = predictive_analysis_service.analyze(30, "auto")
        hints = product_supplier_service.operational_hints([item.product.id for item in analysis.items])
        needed = []
        for item in analysis.items:
            if item.recommendation and item.recommendation.needed:
                hint = hints.get(item.product.id)
                needed.append((item.product.code, item.recommendation.suggested_quantity,
                               item.recommendation.lead_time_days, hint["supplier_name"] if hint else ""))
        expected = {"TOR-UI", "TUE-UI", "DIS-UI"}
        found = {code for code, *_rest in needed}
        if found != expected or any(days != 7 for _code, _qty, days, _name in needed):
            raise SystemExit(f"Reabastecimiento de demostración inesperado: {needed}")
        if hints.get(products["disco"].id) or hints.get(products["clavo"].id):
            raise SystemExit("Un producto sin preferido operativo apareció como precargable.")
        if hints[products["tornillo"].id]["supplier_name"] != "Aceros del Centro":
            raise SystemExit("El preferido del tornillo no es Aceros del Centro.")
        if hints[products["tuerca"].id]["supplier_name"] != "Maderas Andinas":
            raise SystemExit("El preferido de la tuerca no es Maderas Andinas.")
    client = app.test_client()
    login = client.post("/api/auth/login", json={"identifier": "ps_admin", "password": PASSWORD})
    if login.status_code != 200:
        raise SystemExit("No se pudo iniciar sesión en la instancia de prueba.")
    products_page = client.get("/products").get_data(as_text=True)
    if "Proveedores del producto" not in products_page or 'id="np-supplier"' in products_page:
        raise SystemExit("La ficha de producto no muestra la sección real o conserva el select falso.")
    replenishment = client.get("/predictive/replenishment").get_data(as_text=True)
    if "Tornillo zinc" not in replenishment or 'data-preferred-supplier-id=""' not in replenishment:
        raise SystemExit("Reabastecimiento no publicó la pista de proveedor preferido.")
    print("PS_UI_READY")
    print(f"database={db_path}")
    print(f"login=http://{HOST}:{PORT}/login")
    print(f"products=http://{HOST}:{PORT}/products")
    print(f"suppliers=http://{HOST}:{PORT}/suppliers")
    print(f"purchases=http://{HOST}:{PORT}/purchases")
    print(f"replenishment=http://{HOST}:{PORT}/predictive/replenishment")
    print("username=ps_admin")
    print(f"password={PASSWORD}")
    sys.stdout.flush()
    app.run(host=HOST, port=PORT, debug=True, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
