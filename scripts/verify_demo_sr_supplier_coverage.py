"""HTTP/MariaDB solo en DEMO 5001; fixtures propios, limpieza por IDs y checksums.

No lee contraseñas ni modifica registros originales. --preflight solo lee.
"""
import json
import re
import sys
import uuid
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docker_support.guard import validate_demo, validate_demo_url
from verify_demo_category_approval import authenticated, expect

NOTICE = "No existe un proveedor aprobado asociado a todos los productos de esta solicitud."
TABLES = ("suppliers", "product_suppliers", "products", "stock_movements", "product_state_histories",
          "categories", "purchase_orders", "purchase_order_lines", "purchase_receptions",
          "purchase_reception_lines", "supplier_quotes", "supplier_quote_lines", "replenishment_requests",
          "replenishment_request_lines", "sales", "sale_lines")


def snapshot(engine):
    with engine.connect() as connection:
        from docker_support.demo import require_ready
        require_ready(connection)
        rows = connection.execute(text("CHECKSUM TABLE " + ", ".join(TABLES))).all()
        assert len(rows) == len(TABLES) and all(row[1] is not None for row in rows)
        return {row[0].split(".")[-1]: row[1] for row in rows}


def options(html):
    select = re.search(r'<select\b[^>]*name="supplier_id"[^>]*>(.*?)</select>', html, re.S)
    return {int(value) for value in re.findall(r'<option value="(\d+)"', select.group(1))}


def quote_payload(supplier_id, product_ids):
    today = datetime.now(ZoneInfo("America/Caracas")).date()
    return {"supplier_id": supplier_id, "currency": "VES", "issued_on": today.isoformat(),
            "valid_until": (today + timedelta(days=30)).isoformat(),
            "items": [{"product_id": key, "unit_price": "8.00"} for key in product_ids]}


def expected_suppliers(product_ids):
    from app.models.purchase import ProductSupplier, Supplier
    needed = set(product_ids)
    eligible = set()
    for supplier in Supplier.query.filter_by(approval_status="approved", is_active=True).all():
        covered = {link.product_id for link in ProductSupplier.query.filter_by(
            supplier_id=supplier.id, is_active=True).all()}
        if needed and needed.issubset(covered):
            eligible.add(supplier.id)
    return eligible


def create_request(client, product_ids, prefix, created):
    row = expect(client, "POST", "/api/replenishment-requests", 201, json={"notes": prefix,
        "items": [{"product_id": key, "quantity": 2} for key in product_ids]})
    created["requests"].append(row["id"])
    return row["id"]


def verify_original_request(app, users):
    from app.models.purchase import Supplier
    with app.app_context():
        valid_id = Supplier.query.filter_by(name="DEMO Suministros Taller 04").one().id
        invalid = Supplier.query.filter_by(name="DEMO Suministros Taller 07").one()
        assert invalid.is_active and invalid.approval_status == "approved"
        invalid_id = invalid.id
    with authenticated(app, users["asistente"]) as client:
        row = expect(client, "GET", "/api/replenishment-requests/3", 200)
        assert row["status"] == "processing" and {item["product_code"] for item in row["items"]} == {"DEMO-003"}
        assert options(expect(client, "GET", "/purchases/requests/3", 200)) == {valid_id}
        data = quote_payload(invalid_id, [item["product_id"] for item in row["items"]])
        expect(client, "POST", "/api/replenishment-requests/3/quotes", 409, json=data)
        data["items"] = [{**item, "quantity": row["items"][0]["quantity"]} for item in data["items"]]
        expect(client, "POST", "/api/supplier-quotes", 409, json=data)
    print("PASS SR-000003/DEMO-003: solo Taller 04; supplier_id de Taller 07 recibe 409 en ambas APIs.", flush=True)


def check_request(app, clients, request_id, product_ids, suppliers, created, positive=False):
    client = clients["asistente"]
    path = f"/api/replenishment-requests/{request_id}"
    expect(client, "POST", path + "/start", 200, json={})
    with app.app_context():
        expected = expected_suppliers(product_ids)
    html = expect(client, "GET", f"/purchases/requests/{request_id}", 200)
    assert options(html) == expected
    if not expected:
        assert NOTICE in html and re.search(r'<fieldset[^>]* disabled aria-describedby="sr-supplier-notice"', html)
    else:
        assert NOTICE not in html
    for key, supplier_id in suppliers.items():
        if supplier_id in expected:
            continue
        for direct in (False, True):
            data = quote_payload(supplier_id, product_ids)
            endpoint = path + "/quotes"
            if direct:
                endpoint = "/api/supplier-quotes"
                data["items"] = [{**item, "quantity": 2} for item in data["items"]]
            expect(client, "POST", endpoint, 400 if key in ("pending", "rejected", "inactive") else 409, json=data)
    for actor in ("inventario", "admin"):
        expect(clients[actor], "POST", path + "/quotes", 403,
               json=quote_payload(suppliers["full"], product_ids))
    if positive:
        quote = expect(client, "POST", path + "/quotes", 201,
                       json=quote_payload(suppliers["full"], product_ids))
        created["quotes"].append(quote["id"])
        assert {item["product_id"] for item in quote["items"]} == set(product_ids)
        assert all(item["quantity"] == 2 for item in quote["items"])
        assert expect(client, "GET", path, 200)["purchase_order_id"] is None
    print(f"PASS HTTP 5001: SR de {len(product_ids)} producto(s), selector y rechazos manuales/directos.", flush=True)


def verify(app, users, created):
    from app.extensions import db
    from app.models import Product
    from app.models.purchase import Supplier
    prefix = "AUDIT-SR-" + uuid.uuid4().hex[:10]
    with app.app_context():
        products = [Product.query.filter_by(code=code, approval_status="approved", is_active=True).one().id
                    for code in ("DEMO-003", "DEMO-004")]
        suppliers = {}
        for key in ("full", "partial", "inactive_link", "pending", "rejected", "inactive", "unlinked"):
            row = Supplier(name=prefix + " " + key, tax_id="AUDIT-" + uuid.uuid4().hex[:16],
                           approval_status=key if key in ("pending", "rejected") else "approved", is_active=True)
            db.session.add(row)
            db.session.flush()
            suppliers[key] = row.id
            created["suppliers"].append(row.id)
        db.session.commit()
    with ExitStack() as stack:
        clients = {role: stack.enter_context(authenticated(app, users[role]))
                   for role in ("asistente", "inventario", "admin")}
        assistant = clients["asistente"]
        links = {}
        for key, supplier_id in suppliers.items():
            if key == "unlinked":
                continue
            for product_id in products[:1] if key == "partial" else products:
                link = expect(assistant, "POST", f"/api/products/{product_id}/suppliers", 201,
                              json={"supplier_id": supplier_id, "purchase_price": "2.00", "is_preferred": False})
                links[key, product_id] = link["id"]
        with app.app_context():
            db.session.get(Supplier, suppliers["inactive"]).is_active = False
            db.session.commit()
        expect(assistant, "PATCH", f"/api/products/{products[1]}/suppliers/{links['inactive_link', products[1]]}",
               200, json={"is_active": False})
        original = expect(assistant, "GET", "/api/replenishment-requests/3", 200)
        assert original["status"] == "processing" and {line["product_code"] for line in original["items"]} == {"DEMO-003"}
        check_request(app, clients, 3, products[:1], suppliers, created)
        single = create_request(clients["inventario"], products[:1], prefix, created)
        check_request(app, clients, single, products[:1], suppliers, created, positive=True)
        multiple = create_request(clients["inventario"], products, prefix, created)
        check_request(app, clients, multiple, products, suppliers, created, positive=True)
        stale = create_request(clients["inventario"], products, prefix, created)
        expect(assistant, "POST", f"/api/replenishment-requests/{stale}/start", 200, json={})
        assert suppliers["full"] in options(expect(assistant, "GET", f"/purchases/requests/{stale}", 200))
        expect(assistant, "PATCH", f"/api/products/{products[1]}/suppliers/{links['full', products[1]]}",
               200, json={"is_active": False})
        expect(assistant, "POST", f"/api/replenishment-requests/{stale}/quotes", 409,
               json=quote_payload(suppliers["full"], products))
        print("PASS HTTP 5001: vínculo desactivado después de abrir el selector se revalida.", flush=True)
        with app.app_context():
            uncovered = Product(code=prefix, name="Producto temporal sin asociaciones", category_id=
                db.session.get(Product, products[0]).category_id, current_stock=0, minimum_stock=0,
                purchase_price=1, sale_price=2, is_active=True, approval_status="approved")
            db.session.add(uncovered)
            db.session.commit()
            created["products"].append(uncovered.id)
            uncovered_id = uncovered.id
        empty = create_request(clients["inventario"], [uncovered_id], prefix, created)
        check_request(app, clients, empty, [uncovered_id], suppliers, created)


def cleanup(app, created):
    from app.extensions import db
    from app.models import Product
    from app.models.purchase import ProductSupplier, Supplier
    from app.models.replenishment_request import ReplenishmentRequest, ReplenishmentRequestLine
    from app.models.supplier_quote import SupplierQuote, SupplierQuoteLine
    with app.app_context():
        db.session.rollback()
        for model, column, ids in (
            (SupplierQuoteLine, SupplierQuoteLine.quote_id, created["quotes"]),
            (SupplierQuote, SupplierQuote.id, created["quotes"]),
            (ReplenishmentRequestLine, ReplenishmentRequestLine.request_id, created["requests"]),
            (ReplenishmentRequest, ReplenishmentRequest.id, created["requests"]),
            (ProductSupplier, ProductSupplier.supplier_id, created["suppliers"]),
            (Supplier, Supplier.id, created["suppliers"]), (Product, Product.id, created["products"]),
        ):
            if ids:
                model.query.filter(column.in_(ids)).delete(synchronize_session=False)
        db.session.commit()


def main():
    if sys.argv[1:] not in ([], ["--preflight"]):
        raise SystemExit("Uso: verify_demo_sr_supplier_coverage.py [--preflight]")
    validate_demo()
    from docker_support.bootstrap import engine
    from app import create_app
    from app.models import User
    target = engine()
    before = snapshot(target)
    if sys.argv[1:] == ["--preflight"]:
        print(json.dumps(before, sort_keys=True))
        target.dispose()
        return
    app = create_app()
    validate_demo_url(make_url(app.config["SQLALCHEMY_DATABASE_URI"]))
    with app.app_context():
        users = {role: User.query.filter_by(role=role, is_active=True).one().id
                 for role in ("asistente", "inventario", "admin")}
    created = {key: [] for key in ("suppliers", "requests", "quotes", "products")}
    try:
        verify_original_request(app, users)
        verify(app, users, created)
    finally:
        cleanup(app, created)
        assert snapshot(target) == before, "Cambió una tabla operativa original."
        target.dispose()
    print("PASS cleanup DEMO: 16 tablas conservan sus checksums; stock y registros originales intactos.", flush=True)


if __name__ == "__main__":
    main()
