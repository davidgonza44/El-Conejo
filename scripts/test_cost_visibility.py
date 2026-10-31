#!/usr/bin/env python3
"""Visibilidad de costos. SQLite temporal; no toca ferreteria_conejo_tesis.

Uso: python scripts/test_cost_visibility.py
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from test_products_core import check, create_via_api, login, setup_app  # noqa: E402

from app.extensions import db  # noqa: E402
from app.models import User  # noqa: E402
from app.models.purchase import Supplier  # noqa: E402
from app.models.role import Permission, Role  # noqa: E402
from app.models.user import ROLE_SOPORTE_TI  # noqa: E402
from app.services import rbac_service  # noqa: E402
from app.services.inventory_snapshot_service import commercial_today  # noqa: E402
from app.utils.permissions import PRODUCTS_VIEW_COST, PRODUCTS_VIEW_PRICE, ROLE_PERMISSIONS  # noqa: E402
from migrate_settings_full import grant_view_cost  # noqa: E402

COST = "17.37"
SUPPLIER_COST = "41.27"
UNIT_COST = "63.19"
SALE = "8.50"
FORBIDDEN = (
    "purchase_price", "unit_cost", "inventory_value", "inventory_share", "supplier_cost",
    "Costo de referencia", "Costo del proveedor", "Precio de compra",
    "Valor en inventario", "Incluir valor en inventario", "Mayor valor", "Menor valor",
    "Posición por valor", COST, SUPPLIER_COST, UNIT_COST, "80.00", "80.0",
)


def _hits(text: str) -> list[str]:
    return [token for token in FORBIDDEN if token in text]


def _grant_twice() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE permissions (id INTEGER PRIMARY KEY, code TEXT UNIQUE, module TEXT, label TEXT);
        CREATE TABLE roles (
            id INTEGER PRIMARY KEY, slug TEXT UNIQUE, name TEXT UNIQUE, description TEXT,
            is_system INTEGER, is_active INTEGER, created_at TEXT, updated_at TEXT);
        CREATE TABLE role_permissions (
            role_id INTEGER, permission_id INTEGER, PRIMARY KEY (role_id, permission_id));
        """
    )
    roles = (
        (1, "admin", "Administrador"), (2, "inventario", "Inventario"),
        (3, "vendedor", "Vendedor"), (4, "soporte_ti", "Soporte TI"), (5, "auditor", "Auditor"),
    )
    connection.executemany("INSERT INTO roles (id, slug, name, is_system, is_active) VALUES (?, ?, ?, 1, 1)", roles)
    connection.execute(
        "INSERT INTO permissions (id, code, module, label) VALUES (1, 'products:write', 'products', 'Crear y editar · Productos')"
    )
    connection.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES (5, 1)")

    class Cursor:
        def execute(self, sql, params=None):
            connection.execute(sql.replace("INSERT IGNORE", "INSERT OR IGNORE").replace("%s", "?"), params or ())

    cursor = Cursor()
    grant_view_cost(cursor)
    grant_view_cost(cursor)
    connection.commit()
    codes = [row[0] for row in connection.execute(
        "SELECT code FROM permissions WHERE code = ?", (PRODUCTS_VIEW_COST,)
    )]
    slugs = [row[0] for row in connection.execute(
        """
        SELECT r.slug FROM role_permissions rp
        JOIN roles r ON r.id = rp.role_id
        JOIN permissions p ON p.id = rp.permission_id
        WHERE p.code = ? ORDER BY r.slug
        """,
        (PRODUCTS_VIEW_COST,),
    )]
    auditor = [row[0] for row in connection.execute(
        """
        SELECT p.code FROM role_permissions rp
        JOIN permissions p ON p.id = rp.permission_id WHERE rp.role_id = 5 ORDER BY p.code
        """
    )]
    check("migración: el permiso existe una sola vez tras dos ejecuciones", codes == [PRODUCTS_VIEW_COST], str(codes))
    check("helper histórico inserta admin e inventario", slugs == ["admin", "inventario"], str(slugs))
    check("migración: el rol personalizado no cambia", auditor == ["products:write"], str(auditor))
    connection.close()


def _user(app, name, username, role, password):
    with app.app_context():
        user = User(name=name, email=f"{username}@core.test", username=username, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
    client = app.test_client()
    login(client, username, password)
    return client


def _role(app, slug, name, codes):
    with app.app_context():
        rbac_service.ensure_seeded()
        role = Role(slug=slug, name=name, is_system=False, is_active=True)
        role.permissions = Permission.query.filter(Permission.code.in_(codes)).all()
        db.session.add(role)
        db.session.commit()
        rbac_service.invalidate_cache()


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


def approved_category(app, name, color):
    proposed = app.core_roles["asistente"].post("/api/categories", json={"name": name, "icon": "tag", "color": color})
    category_id = require_json(proposed, 201)["id"]
    decided = app.core_roles["admin"].post(f"/api/categories/{category_id}/approve")
    return require_json(decided, 200)["id"]


def main() -> int:
    _grant_twice()
    check("mapa: inventario no ve costos ni precios",
          PRODUCTS_VIEW_COST in ROLE_PERMISSIONS["admin"]
          and PRODUCTS_VIEW_COST not in ROLE_PERMISSIONS["inventario"]
          and PRODUCTS_VIEW_PRICE not in ROLE_PERMISSIONS["inventario"]
          and PRODUCTS_VIEW_PRICE in ROLE_PERMISSIONS["admin"])
    check("mapa: vendedor ve precio; soporte_ti no ve montos",
          PRODUCTS_VIEW_PRICE in ROLE_PERMISSIONS["vendedor"]
          and PRODUCTS_VIEW_COST not in ROLE_PERMISSIONS["vendedor"]
          and PRODUCTS_VIEW_COST not in ROLE_PERMISSIONS["soporte_ti"]
          and PRODUCTS_VIEW_PRICE not in ROLE_PERMISSIONS["soporte_ti"])

    temp_root = Path(tempfile.mkdtemp(prefix="cost-visibility-"))
    app = None
    try:
        app, admin, inventario, vendedor, ids = setup_app(temp_root)
        for client in app.core_roles.values():
            enable_csrf(app, client)
        with app.app_context():
            rbac_service.ensure_seeded()
            stored = {role.slug: set(role.permission_codes) for role in Role.query}
            supplier = Supplier(name="Aceros", tax_id="J-20000000-2")
            db.session.add(supplier)
            db.session.commit()
            supplier_id = supplier.id
            rbac_service.invalidate_cache()
        check("semilla fresca: inventario sin costos ni precios",
              PRODUCTS_VIEW_COST in stored["admin"]
              and PRODUCTS_VIEW_COST not in stored["inventario"]
              and PRODUCTS_VIEW_PRICE not in stored["inventario"])
        check("semilla fresca: vendedor con precio y soporte_ti sin montos",
              PRODUCTS_VIEW_PRICE in stored["vendedor"]
              and PRODUCTS_VIEW_COST not in stored["vendedor"]
              and PRODUCTS_VIEW_COST not in stored["soporte_ti"]
              and PRODUCTS_VIEW_PRICE not in stored["soporte_ti"])

        created = create_via_api(
            admin, code="CLV-1", name="Clavo", category_id=ids["construccion"],
            current_stock=4, purchase_price=COST, sale_price=SALE,
        )
        product_id = require_json(created, 201)["id"]
        alfa = approved_category(app, "Alfa", "blue")
        zeta = approved_category(app, "Zeta", "green")
        create_via_api(admin, code="ALF-1", name="Pieza Alfa", category_id=alfa, current_stock=1,
                       purchase_price="10.00", sale_price="3.00")
        create_via_api(admin, code="ZET-1", name="Pieza Zeta", category_id=zeta, current_stock=1,
                       purchase_price="80.00", sale_price="4.00")
        asistente = app.core_roles["asistente"]
        link = asistente.post(f"/api/products/{product_id}/suppliers", json={
            "supplier_id": supplier_id, "purchase_price": SUPPLIER_COST,
            "purchase_price_currency": "USD",
        })
        require_json(link, 201)
        blocked_order = inventario.post("/api/purchases", json={
            "supplier_id": supplier_id,
            "items": [{"product_id": product_id, "quantity": 2, "unit_cost": UNIT_COST}],
        })

        vendor_item = vendedor.get(f"/api/products/{product_id}")
        vendor_list = vendedor.get("/api/products")
        vendor_find = vendedor.get("/api/products?search=Clavo")
        vendor_suppliers = vendedor.get(f"/api/products/{product_id}/suppliers")
        vendor_categories = vendedor.get("/api/categories?sort=value-desc")
        vendor_category = vendedor.get(f"/api/categories/{zeta}")
        vendor_summary = vendedor.get("/api/categories/summary")
        vendor_export = vendedor.get("/api/categories/export?format=csv&include_value=1&sort=value-desc")
        vendor_product_export = vendedor.post("/api/products/export", json={
            "format": "csv", "options": {"include_stock_prices": True, "include_description": False}, "filters": {},
        })
        vendor_products_page = vendedor.get("/products")
        vendor_categories_page = vendedor.get("/categories")
        texts = {
            "detalle": vendor_item.get_data(as_text=True),
            "lista": vendor_list.get_data(as_text=True),
            "busqueda": vendor_find.get_data(as_text=True),
            "proveedores del producto": vendor_suppliers.get_data(as_text=True),
            "categorias": vendor_categories.get_data(as_text=True),
            "categoria": vendor_category.get_data(as_text=True),
            "resumen": vendor_summary.get_data(as_text=True),
            "export categorias": vendor_export.get_data(as_text=True),
            "export productos": vendor_product_export.get_data(as_text=True),
            "html productos": vendor_products_page.get_data(as_text=True),
            "html categorias": vendor_categories_page.get_data(as_text=True),
        }
        authorized = (vendor_item, vendor_list, vendor_find, vendor_categories, vendor_category,
                      vendor_summary, vendor_export, vendor_product_export, vendor_products_page, vendor_categories_page)
        check("superficies autorizadas del vendedor responden 200", all(response.status_code == 200 for response in authorized),
              str([response.status_code for response in authorized]))
        for label, text in texts.items():
            check(f"vendedor sin costos en {label}", not _hits(text), str(_hits(text)[:6]))
        check("vendedor conserva el precio de venta",
              vendor_item.json.get("sale_price") == float(SALE)
              and require_json(vendor_find, 200)["items"][0]["sale_price"] == float(SALE)
              and "Precio de venta" in texts["html productos"]
              and "Precio de venta" in texts["export productos"])
        names = [item["name"] for item in vendor_categories.json["items"]]
        check("ordenar por valor cae en nombre", names[0] == "Alfa" and names[-1] == "Zeta", str(names))
        check("vendedor no consulta asociaciones ni recibe sus costos",
              link.status_code == 201 and vendor_suppliers.status_code == 403
              and not _hits(vendor_suppliers.get_data(as_text=True)))

        blocked = (
            vendedor.get("/api/purchases"),
            vendedor.get("/api/purchases/1"),
            vendedor.get("/api/purchases/export.csv"),
            vendedor.get("/purchases/1/print"),
            vendedor.get("/api/purchases/receptions"),
            vendedor.get("/api/suppliers"),
            vendedor.get(f"/api/suppliers/{supplier_id}"),
        )
        check("vendedor no entra a compras ni proveedores", all(item.status_code == 403 for item in blocked),
              str([item.status_code for item in blocked]))

        admin_item = admin.get(f"/api/products/{product_id}").json
        inv_item = inventario.get(f"/api/products/{product_id}").json
        inv_category = inventario.get(f"/api/categories/{zeta}").json
        inv_suppliers = inventario.get(f"/api/products/{product_id}/suppliers")
        admin_page = admin.get("/products").get_data(as_text=True)
        inv_page = inventario.get("/categories").get_data(as_text=True)
        check("admin conserva el costo de referencia", admin_item.get("purchase_price") == float(COST)
              and "Costo de referencia" in admin_page)
        check("inventario no ve referencia, proveedor ni valor",
              "purchase_price" not in inv_item and "sale_price" not in inv_item
              and inv_suppliers.status_code == 403 and not _hits(inv_suppliers.get_data(as_text=True))
              and "inventory_value" not in inv_category
              and "Valor en inventario" not in inv_page
              and blocked_order.status_code == 403)
        monetary_request = inventario.post("/api/replenishment-requests", json={
            "items": [{"product_id": product_id, "quantity": 2, "unit_cost": UNIT_COST}],
        })
        check("solicitud rechaza costos inyectados sin devolverlos",
              monetary_request.status_code == 400 and not _hits(monetary_request.get_data(as_text=True)))
        request = inventario.post("/api/replenishment-requests", json={
            "items": [{"product_id": product_id, "quantity": 2}],
        })
        check("inventario crea solicitud sin montos",
              request.status_code == 201 and "unit_cost" not in request.json["items"][0]
              and "total" not in request.json, str(request.get_json())[:240])
        today = commercial_today().isoformat()
        quote = asistente.post("/api/supplier-quotes", json={
            "supplier_id": supplier_id, "issued_on": today, "currency": "VES", "valid_until": "2099-01-01",
            "items": [{"product_id": product_id, "quantity": 2, "unit_price": UNIT_COST}],
        })
        prepared = asistente.post("/api/purchases", json={
            "supplier_id": supplier_id, "supplier_quote_id": require_json(quote, 201)["id"],
            "items": [{"product_id": product_id, "quantity": 2}],
        })
        early = inventario.post(f"/api/purchases/{require_json(prepared, 201)['id']}/receptions", json={
            "received_at": today, "items": [{"line_id": prepared.json["items"][0]["id"], "quantity": 1}],
        })
        check("asistente prepara cotización y OC; no aprueba ni recibe antes",
              quote.status_code == 201 and prepared.status_code == 201
              and prepared.json["status"] == "pending_approval"
              and asistente.post(f"/api/purchases/{require_json(prepared, 201)['id']}/approve").status_code == 403
              and early.status_code == 409,
              str([quote.status_code, prepared.status_code, early.status_code, prepared.get_json()]))
        approved = admin.post(f"/api/purchases/{require_json(prepared, 201)['id']}/approve", json={})
        received = inventario.post(f"/api/purchases/{require_json(approved, 200)['id']}/receptions", json={
            "received_at": today, "items": [{"line_id": prepared.json["items"][0]["id"], "quantity": 1}],
        })
        again = inventario.post(f"/api/purchases/{require_json(approved, 200)['id']}/receptions", json={
            "received_at": today, "items": [{"line_id": prepared.json["items"][0]["id"], "quantity": 1}],
        })
        inv_order = inventario.get(f"/api/purchases/{require_json(approved, 200)['id']}").json
        check("una recepción cierra y la segunda se rechaza",
              approved.status_code == 200 and received.status_code == 200
              and received.json["status"] == "closed_partial"
              and again.status_code == 409
              and "unit_cost" not in inv_order["items"][0]
              and "total" not in inv_order,
              str([received.status_code, received.get_json().get("status") if received.is_json else received.status_code, again.status_code]))
        priced = asistente.post("/api/purchases", json={
            "supplier_id": supplier_id, "supplier_quote_id": require_json(quote, 201)["id"],
            "items": [{"product_id": product_id, "quantity": 1}],
        })
        admin.post(f"/api/purchases/{require_json(priced, 201)['id']}/approve", json={})
        asked = asistente.post(f"/api/purchases/{require_json(priced, 201)['id']}/closure-requests", json={
            "kind": "price_change", "reason": "El proveedor cambió el precio",
        })
        check("asistente solicita cierre y no lo aprueba",
              asked.status_code == 200
              and asistente.post(f"/api/purchases/{require_json(priced, 201)['id']}/closure-requests/approve", json={}).status_code == 403)
        closed = admin.post(f"/api/purchases/{require_json(priced, 201)['id']}/closure-requests/approve", json={})
        check("admin aprueba el cierre comercial", closed.status_code == 200 and closed.json["status"] == "closed_price_change")
        proposed_supplier = asistente.post("/api/suppliers", json={
            "name": "Proveedor propuesto", "tax_id": "J-30000000-3", "contact_name": "Ana",
            "phone": "0212", "email": "ana@example.invalid", "category": "Ferretería", "is_active": True,
        })
        check("asistente propone proveedor y no lo aprueba",
              proposed_supplier.status_code == 201
              and proposed_supplier.json["approval_status"] == "pending"
              and asistente.post(f"/api/suppliers/{require_json(proposed_supplier, 201)['id']}/approve").status_code == 403)
        refused = admin.post(f"/api/suppliers/{require_json(proposed_supplier, 201)['id']}/reject", json={"reason": "RIF duplicado"})
        check("admin rechaza proveedor", refused.status_code == 200 and refused.json["approval_status"] == "rejected")

        # These modules are withdrawn. Check every former read/write surface for
        # denial and for absence of costs, without creating commercial documents.
        withdrawn = (
            ("get", "/api/sales/products?search=Clavo"),
            ("get", "/api/sales"), ("get", "/api/sales/TEST-WITHDRAWN"),
            ("post", "/api/sales"),
            ("get", "/api/quotes"), ("get", "/api/quotes/TEST-WITHDRAWN"),
            ("post", "/api/quotes"),
            ("get", "/api/orders"), ("get", "/api/orders/TEST-WITHDRAWN"),
            ("post", "/api/orders"),
            ("get", "/sales"), ("get", "/sales/quotes"), ("get", "/sales/orders"),
        )
        for role in ("admin", "asistente", "inventario", "vendedor"):
            for method, path in withdrawn:
                response = getattr(app.core_roles[role], method)(path, **({"json": {}} if method == "post" else {}))
                check(f"{role}: módulo retirado {method.upper()} {path}",
                      response.status_code == 403 and not _hits(response.get_data(as_text=True)),
                      str(response.status_code))

        note = vendedor.post("/api/delivery-notes", json={
            "customer_name": "Obra Norte", "items": [{"product_id": product_id, "quantity": 1, "expected_unit_price": SALE}],
        })
        note_body = require_json(note, 201).get("delivery_note") or {}
        check("nota de entrega conserva unit_price y no filtra costos",
              note.status_code == 201 and note_body["items"][0]["unit_price"] == float(SALE)
              and not _hits(note.get_data(as_text=True)), str(note.status_code))

        _role(app, "cost_writer", "Escritor sin costo", {"products:read", "products:write"})
        _role(app, "cost_reader", "Lector de costos", {"products:read", PRODUCTS_VIEW_COST})
        writer = _user(app, "Escritor", "cost_writer_u", "cost_writer", "writer123")
        reader = _user(app, "Lector", "cost_reader_u", "cost_reader", "reader123")
        written = writer.get(f"/api/products/{product_id}")
        updated = writer.put(f"/api/products/{product_id}", json={"sale_price": "9.00"})
        created_hidden = writer.post("/api/products", json={
            "code": "HID-1", "name": "Oculto", "category_id": ids["construccion"],
            "purchase_price": "55.55", "sale_price": "9.00",
        })
        read = reader.get(f"/api/products/{product_id}")
        denied = reader.post("/api/products", json={
            "code": "NO-1", "name": "No", "category_id": ids["construccion"], "sale_price": "1",
        })
        check("un rol ajeno no opera productos aunque tenga products:write",
              written.status_code == 200 and "purchase_price" not in written.json
              and "sale_price" not in written.json
              and updated.status_code == 403 and created_hidden.status_code == 403)
        check("ver costos no otorga editar productos",
              read.status_code == 200 and read.json["purchase_price"] == float(COST)
              and denied.status_code == 403 and reader.put(f"/api/products/{product_id}", json={"name": "No"}).status_code == 403)

        soporte = _user(app, "Soporte", "cost_soporte", ROLE_SOPORTE_TI, "Soporte123")
        soporte_calls = (
            soporte.get("/api/products"), soporte.get("/api/categories"),
            soporte.get("/api/sales"), soporte.get("/api/purchases"),
            soporte.get("/products"), soporte.get("/categories"),
        )
        check("soporte_ti sigue sin operación comercial", all(item.status_code == 403 for item in soporte_calls),
              str([item.status_code for item in soporte_calls]))
        check("soporte_ti no tiene el permiso", PRODUCTS_VIEW_COST not in stored["soporte_ti"])

        inv_detail = require_json(inventario.get(f"/api/purchases/{require_json(approved, 200)['id']}"), 200)
        check("inventario lee OC recibida sin costos",
              all(key not in inv_detail for key in ("subtotal", "total", "pending_amount"))
              and all(key not in inv_detail["items"][0] for key in ("unit_cost", "line_total"))
              and not _hits(str(inv_detail)))

        from test_products_core import CHECKS_RUN, FAILURES
        print(f"RESULTADO: {CHECKS_RUN - FAILURES}/{CHECKS_RUN}")
        return 1 if FAILURES else 0
    finally:
        if app is not None:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
        shutil.rmtree(temp_root)



if __name__ == "__main__":
    raise SystemExit(main())
