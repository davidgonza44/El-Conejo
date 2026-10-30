#!/usr/bin/env python3
"""Suite aislada de Products Core: SQLite temporal, sin MySQL ni ferreteria_conejo.

Cubre consultas, validación, CRUD, stock inicial, rollback atómico, permisos.
NO demuestra SELECT FOR UPDATE de MySQL (SQLite lo ignora; el bloqueo se
conserva en inventory_service._get_active_product_locked y se reporta por revisión de fuente).

Uso: python scripts/test_products_core.py
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import BigInteger
from sqlalchemy.engine import Engine
from sqlalchemy.event import listens_for
from sqlalchemy.ext.compiler import compiles

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Category, Product, StockMovement, User  # noqa: E402
from app.models.user import ROLE_ADMIN, ROLE_ASISTENTE, ROLE_INVENTARIO, ROLE_VENDEDOR  # noqa: E402

FAILURES = 0
CHECKS_RUN = 0
ADMIN_PASSWORD = "SecretCorePass999"


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


def check(name: str, condition: bool, detail: str = "") -> None:
    global FAILURES, CHECKS_RUN
    CHECKS_RUN += 1
    if not condition:
        FAILURES += 1
    suffix = f" — {detail}" if detail else ""
    print(f"[{'OK' if condition else 'FALLO'}] {name}{suffix}")


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


def login(client, username: str, password: str):
    response = client.post("/api/auth/login", json={"identifier": username, "password": password})
    if response.status_code != 200:
        raise RuntimeError(f"login {username} -> {response.status_code} {response.get_data(as_text=True)}")
    return response


def setup_app(temp_root: Path):
    IsolatedConfig.SQLALCHEMY_DATABASE_URI = (
        "sqlite:///" + (temp_root / "products_core.sqlite").resolve().as_posix() + "?check_same_thread=false"
    )
    IsolatedConfig.SECRET_KEY = secrets.token_urlsafe(32)
    app = create_app(IsolatedConfig)
    app.instance_path = str((temp_root / "instance").resolve())
    (temp_root / "instance").mkdir(parents=True, exist_ok=True)

    with app.app_context():
        db.create_all()
        from app.services import rbac_service
        rbac_service.ensure_seeded()
        admin = User(name="Admin", email="admin@core.test", username="core_admin", role=ROLE_ADMIN)
        admin.set_password(ADMIN_PASSWORD)
        inventario = User(name="Inventario", email="inv@core.test", username="core_inv", role=ROLE_INVENTARIO)
        inventario.set_password("inv123")
        vendedor = User(name="Vendedor", email="ven@core.test", username="core_ven", role=ROLE_VENDEDOR)
        vendedor.set_password("ven123")
        asistente = User(name="Asistente", email="asis@core.test", username="core_asis", role=ROLE_ASISTENTE)
        asistente.set_password("asis123")
        construccion = Category(name="Construcción")
        electricidad = Category(name="Electricidad")
        db.session.add_all([admin, inventario, vendedor, asistente, construccion, electricidad])
        db.session.commit()
        ids = {
            "construccion": construccion.id,
            "electricidad": electricidad.id,
            "admin_id": admin.id,
            "asistente_id": asistente.id,
        }

    admin_c = app.test_client()
    inv_c = app.test_client()
    ven_c = app.test_client()
    asis_c = app.test_client()
    login(admin_c, "core_admin", ADMIN_PASSWORD)
    login(inv_c, "core_inv", "inv123")
    login(ven_c, "core_ven", "ven123")
    login(asis_c, "core_asis", "asis123")
    app.core_roles = {"admin": admin_c, "inventario": inv_c, "vendedor": ven_c, "asistente": asis_c}
    return app, admin_c, inv_c, ven_c, ids


class _ApprovedProduct:
    """Respuesta 201 del alta ya aprobada, para las suites que solo necesitan el producto activo."""

    def __init__(self, payload):
        self.status_code = 201
        self.json = payload

    def get_json(self, silent=False):
        return self.json


def create_via_api(client, **fields):
    body = {
        "code": fields.get("code"),
        "name": fields.get("name"),
        "category_id": fields.get("category_id"),
        "unit": fields.get("unit", "unidad"),
        "current_stock": fields.get("current_stock", 0),
        "minimum_stock": fields.get("minimum_stock", 0),
        "purchase_price": fields.get("purchase_price", 1),
        "sale_price": fields.get("sale_price", 2),
        "sale_price_currency": fields.get("sale_price_currency", "USD"),
        "purchase_price_currency": fields.get("purchase_price_currency", "USD"),
    }
    body.update({key: fields[key] for key in ("brand", "description") if key in fields})
    roles = getattr(client.application, "core_roles", None)
    writer = roles["asistente"] if roles and client is not roles["asistente"] else client
    proposed = writer.post("/api/products", json=body)
    if proposed.status_code != 201 or writer is client:
        return proposed
    approved = roles["admin"].post(f"/api/products/{proposed.json['id']}/approve")
    if approved.status_code != 200:
        return approved
    return _ApprovedProduct({**proposed.json, **approved.json})


def modify_via_api(client, product_id, changes):
    """Propone como Asistente y decide como Admin, sin escribir el producto directamente."""
    roles = client.application.core_roles
    changes = dict(changes)
    for price in ("sale_price", "purchase_price"):
        if price in changes:
            changes.setdefault(price + "_currency", "USD")  # Explicit synthetic fixture denomination.
    proposed = roles["asistente"].put(f"/api/products/{product_id}", json=changes)
    if proposed.status_code != 200:
        return proposed
    return roles["admin"].post(f"/api/products/{product_id}/approve", json={
        "proposal_id": proposed.json["pending_change"]["id"],
    })


def main() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="products-core-"))
    try:
        app, admin, inventario, vendedor, ids = setup_app(temp_root)
        cat_a = ids["construccion"]
        cat_b = ids["electricidad"]

        empty = admin.get("/api/products")
        check("lista vacía", empty.status_code == 200 and empty.json["count"] == 0, str(empty.json))

        page = admin.get("/products")
        html = page.get_data(as_text=True)
        sidebar_at = html.find('id="ix-sidebar"')
        sidebar = html[sidebar_at:html.find("</aside>", sidebar_at)] if sidebar_at != -1 else ""
        check("página live usa ix", page.status_code == 200 and "ix-prod-search" in html and "1,247" not in html)
        check("página live no carga JS de referencia", "ix_product_drawer.js" not in html and "ix_products_live.js" in html)
        check(
            "página live usa cáscara Pro sin fixtures",
            'data-reference-mode="0"' in html
            and "El Conejo" in sidebar
            and "Sucursal" not in html
            and "Catálogo visual" not in sidebar
            and "Asistente de inventario" not in sidebar
            and "Notas de entrega" not in sidebar
            and "Importación histórica" not in sidebar
            and "Diagnóstico de historial" not in sidebar
            and "?ref=1" not in sidebar,
        )

        r0 = create_via_api(admin, code="ZERO-1", name="Sin stock inicial", category_id=cat_a, current_stock=0)
        check("crear stock 0", r0.status_code == 201 and r0.json["current_stock"] == 0)
        with app.app_context():
            moves = StockMovement.query.filter_by(product_id=r0.json["id"]).count()
            check("stock 0 no crea movimiento", moves == 0)

        r30 = create_via_api(
            admin, code="HER-001", name="Martillo de uña 16 oz", category_id=cat_a,
            current_stock=30, minimum_stock=10, sale_price=15,
        )
        check("crear stock 30", r30.status_code == 201 and r30.json["current_stock"] == 30, str(r30.status_code))
        with app.app_context():
            movement = StockMovement.query.filter_by(product_id=r30.json["id"]).one()
            check(
                "movimiento inicial trazable",
                movement.movement_type == "entrada"
                and movement.quantity == 30
                and movement.previous_stock == 0
                and movement.new_stock == 30
                and movement.reason == "Stock inicial"
                and movement.user_id == ids["asistente_id"],
            )

        low = create_via_api(
            admin, code="HER-LOW", name="Martillo bajo", category_id=cat_a,
            current_stock=8, minimum_stock=10, sale_price=12,
        )
        out = create_via_api(
            admin, code="OUT-1", name="Producto agotado", category_id=cat_b,
            current_stock=0, minimum_stock=5, sale_price=9,
        )
        available = create_via_api(
            admin, code="ELE-001", name="Cable THW", category_id=cat_b,
            current_stock=50, minimum_stock=10, sale_price=3,
        )
        check("semilla estados", all(r.status_code == 201 for r in (low, out, available)))

        asistente = app.core_roles["asistente"]
        inactive = create_via_api(admin, code="INA-1", name="Inactivo demo", category_id=cat_a, current_stock=4)
        deact = asistente.delete(f"/api/products/{inactive.json['id']}")
        check("desactivar requiere revisión", deact.status_code == 200 and deact.json["product"]["is_active"] is True)
        approved_deactivation = admin.post(f"/api/products/{inactive.json['id']}/approve", json={
            "proposal_id": deact.json["product"]["pending_change"]["id"],
        })
        check("desactivar al aprobar", approved_deactivation.status_code == 200 and approved_deactivation.json["is_active"] is False)
        with app.app_context():
            still = db.session.get(Product, inactive.json["id"])
            check("no delete físico", still is not None and still.is_active is False)

        legacy = admin.get("/api/products")
        codes = {item["code"] for item in legacy.json["items"]}
        check("legacy activo-only", "INA-1" not in codes and legacy.json["count"] >= 4)
        check("contrato count/items", "count" in legacy.json and "items" in legacy.json and "pagination" not in legacy.json)

        todos = admin.get("/api/products?include_inactive=1")
        check("Todos include_inactive", {item["code"] for item in todos.json["items"]} >= {"INA-1", "HER-001"})

        only_inactive = admin.get("/api/products?status=inactivo")
        check(
            "status=inactivo override",
            only_inactive.status_code == 200
            and only_inactive.json["count"] == 1
            and only_inactive.json["items"][0]["code"] == "INA-1",
        )

        by_name = admin.get("/api/products?search=martillo")
        check("búsqueda nombre", {item["code"] for item in by_name.json["items"]} >= {"HER-001", "HER-LOW"})
        by_code = admin.get("/api/products?search=ELE-001")
        check("búsqueda código", by_code.json["count"] == 1 and by_code.json["items"][0]["code"] == "ELE-001")
        by_cat = admin.get("/api/products?search=electricidad")
        check("búsqueda categoría", {item["code"] for item in by_cat.json["items"]} >= {"ELE-001", "OUT-1"})
        none = admin.get("/api/products?search=zzzz-no-existe")
        check("búsqueda vacía", none.json["count"] == 0)

        cat_filter = admin.get(f"/api/products?category_id={cat_b}")
        check("filtro categoría", all(item["category_id"] == cat_b for item in cat_filter.json["items"]))
        empty_cat = admin.get("/api/products?category_id=999999")
        check("categoría sin resultados", empty_cat.json["count"] == 0)

        disp = admin.get("/api/products?status=disponible")
        check("status disponible", all(item["stock_status"] == "disponible" for item in disp.json["items"]) and disp.json["count"] >= 1)
        bajo = admin.get("/api/products?status=bajo_stock")
        check(
            "status bajo_stock excluye 0",
            all(item["stock_status"] == "bajo_stock" and item["current_stock"] > 0 for item in bajo.json["items"])
            and any(item["code"] == "HER-LOW" for item in bajo.json["items"]),
        )
        agot = admin.get("/api/products?status=agotado")
        check("status agotado", all(item["current_stock"] == 0 and item["is_active"] for item in agot.json["items"]))

        price = admin.get("/api/products?price_min=10&price_max=20")
        check("rango precio", all(10 <= item["sale_price"] <= 20 for item in price.json["items"]))
        bad_price = admin.get("/api/products?price_min=20&price_max=1")
        check("precio inválido", bad_price.status_code == 400)
        stock = admin.get("/api/products?stock_min=10&stock_max=40")
        check("rango stock", all(10 <= item["current_stock"] <= 40 for item in stock.json["items"]))
        bad_stock = admin.get("/api/products?stock_min=9&stock_max=1")
        check("stock inválido", bad_stock.status_code == 400)

        now = datetime.utcnow()
        later = (now + timedelta(days=1)).date().isoformat()
        earlier = (now - timedelta(days=1)).date().isoformat()
        dated = admin.get(f"/api/products?updated_from={earlier}&updated_to={later}")
        check("rango fecha", dated.status_code == 200 and dated.json["count"] >= 1)
        bad_date = admin.get(f"/api/products?updated_from={later}&updated_to={earlier}")
        check("fecha inválida", bad_date.status_code == 400)

        for sort in ("name-asc", "name-desc", "price-asc", "price-desc", "stock-asc", "stock-desc", "created-desc", "created-asc", "updated-desc"):
            resp = admin.get(f"/api/products?sort={sort}")
            check(f"sort {sort}", resp.status_code == 200, str(resp.status_code))
        bad_sort = admin.get("/api/products?sort=drop-table")
        check("sort rechazado", bad_sort.status_code == 400)

        p1 = admin.get("/api/products?page=1&per_page=2&sort=name-asc")
        check(
            "paginación página 1",
            p1.status_code == 200
            and len(p1.json["items"]) == 2
            and p1.json["pagination"]["page"] == 1
            and p1.json["pagination"]["has_prev"] is False
            and p1.json["pagination"]["has_next"] is True,
        )
        p2 = admin.get("/api/products?page=2&per_page=2&sort=name-asc")
        check("paginación siguiente", p2.json["pagination"]["page"] == 2 and p2.json["items"] != p1.json["items"])
        last_page = p1.json["pagination"]["pages"]
        plast = admin.get(f"/api/products?page={last_page}&per_page=2")
        check("última página", plast.json["pagination"]["has_next"] is False)
        oob = admin.get("/api/products?page=99&per_page=2")
        check("página fuera de rango", oob.status_code == 200 and oob.json["items"] == [] and oob.json["count"] == p1.json["count"])

        summary = admin.get("/api/products/summary")
        s = summary.json
        check("KPI total_active SQL", summary.status_code == 200 and s["total_active"] >= 4)
        check("KPI low_stock excluye 0", s["low_stock"] >= 1)
        check("KPI out_of_stock", s["out_of_stock"] >= 1)
        check("KPI active_categories distinct", s["active_categories"] >= 1)
        check("summary paneles", "category_distribution" in s and "inventory_alerts" in s and "recent_products" in s)

        dup = create_via_api(admin, code="HER-001", name="Dup", category_id=cat_a)
        check("código duplicado", dup.status_code == 409)
        empty_name = create_via_api(admin, code="X-1", name="  ", category_id=cat_a)
        check("nombre vacío", empty_name.status_code == 400)
        bad_cat = create_via_api(admin, code="X-2", name="Ok", category_id=999999)
        check("categoría inexistente", bad_cat.status_code == 400)
        neg_stock = create_via_api(admin, code="X-3", name="Ok", category_id=cat_a, current_stock=-1)
        check("stock negativo", neg_stock.status_code == 400)
        neg_min = create_via_api(admin, code="X-4", name="Ok", category_id=cat_a, minimum_stock=-2)
        check("minimum_stock negativo", neg_min.status_code == 400)
        neg_price = create_via_api(admin, code="X-5", name="Ok", category_id=cat_a, sale_price=-3)
        check("precio negativo", neg_price.status_code == 400)

        with app.app_context():
            moves_before = StockMovement.query.count()
        with patch("app.services.inventory_service._create_movement", side_effect=RuntimeError("forced-move-fail")):
            boom = create_via_api(admin, code="ATOM-1", name="No debe persistir", category_id=cat_a, current_stock=30)
        check("atomicidad HTTP error", boom.status_code >= 400)
        with app.app_context():
            check("atomicidad sin producto", Product.query.filter_by(code="ATOM-1").first() is None)
            check("atomicidad sin movimiento extra", StockMovement.query.count() == moves_before)

        pid = r30.json["id"]
        asistente = app.core_roles["asistente"]
        upd = asistente.put(f"/api/products/{pid}", json={"name": "Martillo editado", "sale_price": 18, "sale_price_currency": "USD"})
        check("propuesta conserva nombre vigente", upd.status_code == 200 and upd.json["name"] == "Martillo de uña 16 oz")
        approved_edit = admin.post(f"/api/products/{pid}/approve", json={"proposal_id": upd.json["pending_change"]["id"]})
        check("aprobación cambia nombre", approved_edit.status_code == 200 and approved_edit.json["name"] == "Martillo editado")
        stock_edit = asistente.put(f"/api/products/{pid}", json={"current_stock": 999})
        check("update rechaza current_stock", stock_edit.status_code == 400)
        with app.app_context():
            before = StockMovement.query.filter_by(product_id=inactive.json["id"]).count()
        asistente.delete(f"/api/products/{inactive.json['id']}")
        with app.app_context():
            after = StockMovement.query.filter_by(product_id=inactive.json["id"]).count()
            check("desactivar conserva movimientos", after == before)

        detail = admin.get(f"/api/products/{pid}")
        check("detalle GET", detail.status_code == 200 and detail.json["code"] == "HER-001" and "stock_status" in detail.json)
        missing = admin.get("/api/products/999999")
        check("detalle 404", missing.status_code == 404)

        read = vendedor.get("/api/products")
        check("vendedor GET permitido", read.status_code == 200)
        forbidden = vendedor.post("/api/products", json={"code": "NO", "name": "No", "category_id": cat_a})
        check("vendedor POST 403", forbidden.status_code == 403)
        forbidden_img = vendedor.post(f"/api/products/{pid}/image", data={})
        check("vendedor imagen 403", forbidden_img.status_code == 403)
        write_ok = inventario.put(f"/api/products/{pid}", json={"description": "ok"})
        check("inventario no edita productos", write_ok.status_code == 403)
        hidden = inventario.get(f"/api/products/{pid}")
        check(
            "inventario no ve precios ni costos",
            hidden.status_code == 200
            and "purchase_price" not in hidden.json
            and "sale_price" not in hidden.json,
        )
        admin_create = admin.post("/api/products", json={"code": "ADM-1", "name": "No", "category_id": cat_a})
        check("admin no propone productos", admin_create.status_code == 403)
        proposed = asistente.post("/api/products", json={
            "code": "PROP-1", "name": "Propuesto", "category_id": cat_a, "unit": "unidad",
            "purchase_price": "1.00", "sale_price": "2.00",
            "purchase_price_currency": "USD", "sale_price_currency": "USD",
        })
        check(
            "asistente propone pendiente",
            proposed.status_code == 201
            and proposed.json["approval_status"] == "pending"
            and proposed.json["is_active"] is False,
        )
        check("asistente no aprueba", asistente.post(f"/api/products/{proposed.json['id']}/approve").status_code == 403)
        approved = admin.post(f"/api/products/{proposed.json['id']}/approve")
        check("admin aprueba producto", approved.status_code == 200 and approved.json["approval_status"] == "approved")
        rejected_src = asistente.post("/api/products", json={
            "code": "PROP-2", "name": "Rechazado", "category_id": cat_a, "unit": "unidad",
            "purchase_price": "1.00", "sale_price": "2.00",
            "purchase_price_currency": "USD", "sale_price_currency": "USD",
        })
        rejected = admin.post(f"/api/products/{rejected_src.json['id']}/reject", json={"reason": "Duplicado de catálogo"})
        check("admin rechaza producto", rejected.status_code == 200 and rejected.json["approval_status"] == "rejected")

        anon = app.test_client()
        check("sin sesión 401", anon.get("/api/products").status_code == 401)

        print()
        print("SELECT FOR UPDATE: source-reviewed/preserved in inventory_service._get_active_product_locked; not claimed under SQLite.")
        print(f"CHECKS={CHECKS_RUN} FAILURES={FAILURES}")
        return 1 if FAILURES else 0
    finally:
        # Isolated SQLite lives under temp_root; do not touch ferreteria_conejo.
        pass


if __name__ == "__main__":
    sys.exit(main())
