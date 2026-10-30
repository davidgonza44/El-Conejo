#!/usr/bin/env python3
"""Asociaciones producto-proveedor en SQLite desechable. Sin MySQL ni la base de tesis."""
from __future__ import annotations

import os
import secrets
import sqlite3
import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import BigInteger
from sqlalchemy.engine import Engine
from sqlalchemy.event import listens_for
from sqlalchemy.ext.compiler import compiles

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Category, Product, User  # noqa: E402
from app.models.purchase import ProductSupplier, PurchaseOrderLine, Supplier  # noqa: E402
from app.models.user import ROLE_ADMIN, ROLE_ASISTENTE, ROLE_INVENTARIO, ROLE_VENDEDOR  # noqa: E402
from app.services import rbac_service, replenishment_service  # noqa: E402

FAILURES = 0
CHECKS = 0


@compiles(BigInteger, "sqlite")
def _compile_big_integer_for_sqlite(_type, _compiler, **_kwargs):
    return "INTEGER"


@listens_for(Engine, "connect")
def _configure_isolated_sqlite(dbapi_connection, _connection_record) -> None:
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def check(name, condition, detail=""):
    global FAILURES, CHECKS
    CHECKS += 1
    if not condition:
        FAILURES += 1
    print(f"[{'OK' if condition else 'FALLO'}] {name}" + (f" — {detail}" if detail else ""))


class IsolatedConfig:
    SECRET_KEY = secrets.token_urlsafe(32)
    APP_ENV = "testing"
    TESTING = True
    PROPAGATE_EXCEPTIONS = False
    WTF_CSRF_ENABLED = False
    MAIL_ENABLED = False
    GOOGLE_CLIENT_ID = None
    GOOGLE_CLIENT_SECRET = None
    GOOGLE_REDIRECT_URI = None
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"check_same_thread": False, "timeout": 30}}


def login(client, username, password):
    response = client.post("/api/auth/login", json={"identifier": username, "password": password})
    if response.status_code != 200:
        raise RuntimeError(f"login {username} -> {response.status_code} {response.get_data(as_text=True)}")


def enable_csrf(app, client):
    """Give this synthetic session a real token; do not bypass validation."""
    from flask import session
    from flask_wtf.csrf import generate_csrf
    with app.test_request_context():
        token = generate_csrf()
        seed = session["csrf_token"]
    with client.session_transaction() as saved:
        saved["csrf_token"] = seed
    client.environ_base["HTTP_X_CSRFTOKEN"] = token


def require_json(response, status):
    if response.status_code != status or not getattr(response, "is_json", True):
        raise AssertionError(f"Expected JSON {status}, got {response.status_code}: {response.get_data(as_text=True)}")
    return response.get_json()


def main():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        IsolatedConfig.SQLALCHEMY_DATABASE_URI = "sqlite:///" + (root / "links.sqlite").resolve().as_posix()
        app = create_app(IsolatedConfig)
        try:
            with app.app_context():
                db.create_all()
                rbac_service.ensure_seeded()
                inventario = User(name="Inventario", email="inv@links.test", username="links_inv", role=ROLE_INVENTARIO)
                inventario.set_password("inv123")
                asistente = User(name="Asistente", email="asis@links.test", username="links_asis", role=ROLE_ASISTENTE)
                asistente.set_password("asis123")
                administrador = User(name="Administrador", email="admin@links.test", username="links_admin", role=ROLE_ADMIN)
                administrador.set_password("admin123")
                vendedor = User(name="Vendedor", email="ven@links.test", username="links_ven", role=ROLE_VENDEDOR)
                vendedor.set_password("ven123")
                category = Category(name="Fijación")
                db.session.add_all([inventario, vendedor, asistente, administrador, category])
                db.session.commit()
                category_id = category.id
            inv = app.test_client()
            ven = app.test_client()
            login(inv, "links_inv", "inv123")
            login(ven, "links_ven", "ven123")
            assistant, admin = app.test_client(), app.test_client()
            login(assistant, "links_asis", "asis123")
            login(admin, "links_admin", "admin123")
            enable_csrf(app, assistant)
            created = assistant.post("/api/products", json={
                "name": "Tornillo", "code": "TOR-1", "category_id": category_id, "unit": "unidad",
                "purchase_price": "10.00", "sale_price": "15.00", "current_stock": 4, "minimum_stock": 1,
                "purchase_price_currency": "USD", "sale_price_currency": "USD",
            })
            other = assistant.post("/api/products", json={
                "name": "Tuerca", "code": "TUE-1", "category_id": category_id, "unit": "unidad",
                "purchase_price": "3.00", "sale_price": "5.00", "current_stock": 2, "minimum_stock": 1,
                "purchase_price_currency": "USD", "sale_price_currency": "USD",
            })
            check("alta de productos", created.status_code == 201 and other.status_code == 201, created.get_data(as_text=True))
            product_id = require_json(created, 201)["id"]
            other_id = require_json(other, 201)["id"]
            require_json(admin.post(f"/api/products/{product_id}/approve"), 200)
            require_json(admin.post(f"/api/products/{other_id}/approve"), 200)
            check("inventario no crea ni asocia productos",
                  inv.post("/api/products", json={"name": "No autorizado"}).status_code == 403
                  and inv.post(f"/api/products/{product_id}/suppliers", json={}).status_code == 403)
            check("asistente no aprueba productos", assistant.post(f"/api/products/{product_id}/approve").status_code == 403)
            with app.app_context():
                one = Supplier(name="Aceros", tax_id="J-PS-1", is_active=True)
                two = Supplier(name="Maderas", tax_id="J-PS-2", is_active=True)
                idle = Supplier(name="Inactivo", tax_id="J-PS-3", is_active=False)
                db.session.add_all([one, two, idle])
                db.session.commit()
                supplier_ids = {"one": one.id, "two": two.id, "idle": idle.id}
            empty = assistant.get(f"/api/products/{product_id}/suppliers")
            check("producto sin proveedores", empty.status_code == 200 and empty.json["items"] == [])
            denied = ven.post(f"/api/products/{product_id}/suppliers", json={"supplier_id": supplier_ids["one"], "purchase_price": "4.00", "purchase_price_currency": "USD"})
            check("vendedor no asocia", denied.status_code == 403)
            first = assistant.post(f"/api/products/{product_id}/suppliers", json={
                "supplier_id": supplier_ids["one"], "purchase_price": "4.00", "supplier_product_code": "AC-1",
                "purchase_price_currency": "USD",
                "lead_time_days": 5, "minimum_order_quantity": 20, "is_preferred": True,
            })
            second = assistant.post(f"/api/products/{product_id}/suppliers", json={
                "supplier_id": supplier_ids["two"], "purchase_price": "6.50", "is_preferred": False,
                "purchase_price_currency": "USD",
            })
            third = assistant.post(f"/api/products/{other_id}/suppliers", json={
                "supplier_id": supplier_ids["one"], "purchase_price": "2.00", "is_preferred": True,
                "purchase_price_currency": "USD",
            })
            check("varios proveedores y varios productos", first.status_code == 201 and second.status_code == 201 and third.status_code == 201,
                  f"{first.status_code} {second.status_code} {third.status_code} {first.get_data(as_text=True)}")
            duplicate = assistant.post(f"/api/products/{product_id}/suppliers", json={"supplier_id": supplier_ids["one"], "purchase_price": "1.00", "purchase_price_currency": "USD"})
            check("no duplica product_id + supplier_id", duplicate.status_code == 409)
            inactive_new = assistant.post(f"/api/products/{product_id}/suppliers", json={"supplier_id": supplier_ids["idle"], "purchase_price": "1.00", "purchase_price_currency": "USD", "is_preferred": True})
            check("proveedor inactivo no se asocia ni se prefiere", inactive_new.status_code == 400, inactive_new.get_data(as_text=True))
            listed = assistant.get(f"/api/products/{product_id}/suppliers")
            preferred = [row for row in require_json(listed, 200)["items"] if row["operational_preferred"]]
            check("un solo preferido operativo", len(preferred) == 1 and preferred[0]["supplier_id"] == supplier_ids["one"])
            with app.app_context():
                db.session.get(Supplier, supplier_ids["one"]).is_active = False
                db.session.commit()
            listed = assistant.get(f"/api/products/{product_id}/suppliers")
            stored = next(row for row in require_json(listed, 200)["items"] if row["id"] == first.json["id"])
            check("preferido de proveedor inactivo se conserva y se ignora",
                  stored["is_preferred"] and not stored["operational_preferred"])
            blocked = assistant.patch(f"/api/products/{product_id}/suppliers/{first.json['id']}", json={"is_preferred": True})
            check("no se puede marcar preferido un proveedor inactivo", blocked.status_code == 400, blocked.get_data(as_text=True))
            switched = assistant.patch(f"/api/products/{product_id}/suppliers/{second.json['id']}", json={"is_preferred": True, "minimum_order_quantity": 20})
            listed = assistant.get(f"/api/products/{product_id}/suppliers")
            preferred = [row for row in require_json(listed, 200)["items"] if row["is_preferred"]]
            check("cambiar preferido deja uno", switched.status_code == 200 and len(preferred) == 1 and preferred[0]["id"] == second.json["id"])
            with app.app_context():
                product = db.session.get(Product, product_id)
                check("el costo de referencia no cambia", Decimal(product.purchase_price) == Decimal("10.00"))
            options = assistant.get(f"/api/purchases/options?supplier_id={supplier_ids['two']}")
            products = require_json(options, 200)["products"]
            tornillo = next(row for row in products if row["id"] == product_id)
            tuerca = next(row for row in products if row["id"] == other_id)
            check("prioriza asociados y sugiere el costo del proveedor",
                  options.status_code == 200 and products[0]["id"] == product_id and tornillo["associated"]
                  and tornillo["unit_cost"] == "6.50" and tornillo["cost_source"] == "supplier"
                  and not tuerca["associated"] and tuerca["unit_cost"] == "3.00" and tuerca["cost_source"] == "reference")
            idle_options = assistant.get(f"/api/purchases/options?supplier_id={supplier_ids['one']}")
            idle_tornillo = next(row for row in require_json(idle_options, 200)["products"] if row["id"] == product_id)
            check("proveedor inactivo no sugiere asociación", not idle_tornillo["associated"] and idle_tornillo["cost_source"] == "reference")
            quote = assistant.post("/api/supplier-quotes", json={
                "supplier_id": supplier_ids["two"], "issued_on": date.today().isoformat(),
                "currency": "VES", "valid_until": "2099-01-01",
                "items": [{"product_id": product_id, "quantity": 1, "unit_price": "7.25"}],
            })
            order = assistant.post("/api/purchases", json={
                "supplier_quote_id": require_json(quote, 201)["id"],
                "supplier_id": supplier_ids["two"],
                "items": [{"product_id": product_id, "quantity": 1, "unit_cost": "7.25"}],
            })
            check("la orden acepta cantidad bajo el mínimo y no crea asociaciones", order.status_code == 201, order.get_data(as_text=True))
            with app.app_context():
                before = ProductSupplier.query.count()
                line = PurchaseOrderLine.query.filter_by(product_id=product_id).one()
                link = db.session.get(ProductSupplier, second.json["id"])
                link.purchase_price = Decimal("9.99")
                reference = Decimal(db.session.get(Product, product_id).purchase_price)
                db.session.commit()
                line = db.session.get(PurchaseOrderLine, line.id)
                after = ProductSupplier.query.count()
            check("los tres costos no se sincronizan", before == after == 3 and Decimal(line.unit_cost) == Decimal("7.25") and reference == Decimal("10.00"))
            removed = assistant.patch(f"/api/products/{product_id}/suppliers/{second.json['id']}", json={"is_active": False})
            with app.app_context():
                still_product = db.session.get(Product, product_id) is not None
                still_supplier = db.session.get(Supplier, supplier_ids["two"]) is not None
                still_link = db.session.get(ProductSupplier, second.json["id"])
            check("desactivar la asociación no borra producto ni proveedor",
                  removed.status_code == 200 and still_product and still_supplier and still_link is not None and not still_link.is_active)
            with app.app_context():
                payload = replenishment_service.order_payload(
                    [{"product_id": other_id, "quantity": 2}], supplier_ids["two"])
                recommendation = replenishment_service.compute(1, 1, 1.0, 0, Decimal("3.00"), date(2026, 9, 28))
                from app.services import product_supplier_service
                hints = product_supplier_service.operational_hints([product_id, other_id])
            check("reabastecimiento conserva lead time 7 y costo de referencia",
                  replenishment_service.DEFAULT_LEAD_TIME_DAYS == 7
                  and recommendation.lead_time_days == 7
                  and recommendation.lead_time_source == replenishment_service.LEAD_TIME_ASSUMED
                  and payload["items"][0]["unit_cost"] == "3.00")
            check("precarga solo el preferido operativo", hints == {})
        finally:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
    print(f"\n{CHECKS - FAILURES}/{CHECKS} checks passed")
    if FAILURES:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
