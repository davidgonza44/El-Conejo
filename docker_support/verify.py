"""HTTP and database acceptance tests, ONLY on the generated disposable stack.

Creates synthetic records. Subsequent --persist checks never reseed: they compare
business row fingerprints and stored uploads across restart/down/up cycles.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import secrets
import time

from openpyxl import load_workbook
from PIL import Image
import requests
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from docker_support.bootstrap import engine, verify_installation
from docker_support.guard import wait_database

BASE = "http://app:5000"
STATE = Path("/app/instance/docker-validation.json")
BUSINESS = ("users", "auth_identities", "roles", "permissions", "role_permissions", "customers", "categories", "products",
    "stock_movements", "sales", "sale_lines", "delivery_notes", "delivery_note_items", "suppliers",
    "purchase_orders", "purchase_order_lines", "purchase_receptions", "purchase_reception_lines",
    "historical_imports", "historical_demand_records", "reconciliation_runs", "reconciliation_lines",
    "reconciliation_cuts", "reconciliation_cut_stock", "app_settings")


def check(condition, label):
    if not condition:
        raise AssertionError(label)
    print("PASS: " + label, flush=True)


class Client:
    def __init__(self):
        self.session = requests.Session()

    def call(self, method, path, expected=200, **kwargs):
        response = self.session.request(method, BASE + path, timeout=180, allow_redirects=False, **kwargs)
        if response.status_code != expected:
            raise AssertionError(f"{method} {path}: HTTP {response.status_code}, expected {expected}; "
                + (str(response.json().get("error", "")) if response.headers.get("Content-Type", "").startswith("application/json") else "HTML error"))
        return response

    def json(self, method, path, expected=200, **kwargs):
        return self.call(method, path, expected, **kwargs).json()


def login():
    client = Client()
    body = client.json("POST", "/api/auth/login", json={"identifier": os.environ["ADMIN_USERNAME"],
        "password": os.environ["ADMIN_PASSWORD"]})
    check(body["user"]["role"] == "admin", "login real en Gunicorn, administrador inicial")
    return client


def pages(client):
    redirect = client.call("GET", "/reports", 302)
    check(redirect.headers["Location"] == "/dashboard", "reportes conserva su redirección existente al dashboard")
    for path in ("/dashboard", "/products", "/categories", "/inventory", "/sales", "/sales/list",
            "/delivery-notes", "/purchases", "/purchases/receptions", "/reports/sales",
            "/predictive/demand", "/predictive/replenishment", "/predictive/critical",
            "/predictive/trends", "/predictive/accuracy", "/reconciliation", "/settings", "/historical-imports"):
        response = client.call("GET", path)
        check("text/html" in response.headers["Content-Type"], "HTML autenticado " + path)
    for path in ("/api/reports/dashboard-summary", "/api/reports/stock-vs-minimum",
            "/api/reports/entries-vs-exits", "/api/reports/movements-by-category",
            "/api/predictions/readiness", "/api/predictions/products"):
        client.json("GET", path)
        check(True, "API de consulta " + path)
    for page in ("demand_forecast", "replenishment"):
        deadline = time.monotonic() + 120
        while True:
            response = client.session.get(BASE + "/api/predictions/export/" + page, timeout=180)
            if response.status_code != 202 or time.monotonic() >= deadline:
                break
            check(response.json()["status"] in ("pending", "busy"), "estado predictivo pendiente explícito")
            time.sleep(2)
        check(response.status_code == 200 and "csv" in response.headers["Content-Type"],
            "exportación predictiva CSV " + page)


def commercial(client, tag):
    category = client.json("POST", "/api/categories", 201, json={"name": "Sintética " + tag})
    product = client.json("POST", "/api/products", 201, json={"code": tag, "name": "Producto sintético " + tag,
        "category_id": category["id"], "unit": "unidad", "current_stock": 100, "minimum_stock": 10,
        "purchase_price": "5.00", "sale_price": "8.00"})
    pid = product["id"]
    client.json("POST", "/api/inventory/entry", 201, json={"product_id": pid, "quantity": 5, "reason": "Prueba Docker"})
    customer = client.json("POST", "/api/customers", 201, json={"name": "Cliente sintético " + tag})
    sale_body = {"customer_id": customer["id"], "payment_method": "cash", "payment_condition": "cash",
        "idempotency_key": secrets.token_hex(16), "items": [{"product_id": pid, "quantity": 3}]}
    sale = client.json("POST", "/api/sales", 201, json=sale_body)
    replay = client.json("POST", "/api/sales", 200, json=sale_body)
    check(replay["sale_number"] == sale["sale_number"], "ventas e idempotencia sin doble salida")
    note = client.json("POST", "/api/delivery-notes", 201, json={"customer_name": "Destinatario sintético",
        "items": [{"product_id": pid, "quantity": 2, "expected_unit_price": "8.00"}]})["delivery_note"]
    check(client.call("GET", f"/api/delivery-notes/{note['id']}/pdf").content.startswith(b"%PDF"), "nota de entrega y PDF")
    supplier = client.json("POST", "/api/purchases/suppliers", 201,
        json={"name": "Proveedor sintético " + tag, "tax_id": "J-" + str(secrets.randbelow(90000000) + 10000000) + "-1"})
    purchasing = staff(client, tag + "-inv", "inventario")
    order = purchasing.json("POST", "/api/purchases", 201, json={"supplier_id": supplier["id"],
        "items": [{"product_id": pid, "quantity": 7, "unit_cost": "5.00"}]})
    check(order["status"] == "pending_approval", "compra requiere aprobación")
    purchasing.call("POST", f"/api/purchases/{order['id']}/approve", 403, json={})
    client.json("POST", f"/api/purchases/{order['id']}/approve", json={})
    order = client.json("GET", f"/api/purchases/{order['id']}")
    client.json("POST", f"/api/purchases/{order['id']}/receptions", json={
        "received_at": datetime.now().date().isoformat(), "items": [{"line_id": order["items"][0]["id"], "quantity": 7}]})
    check(client.json("GET", f"/api/products/{pid}")["current_stock"] == 107,
        "categorías, productos, entradas, ventas, entrega y recepción: stock exacto 107")
    for fmt, signature in (("pdf", b"%PDF"), ("xlsx", b"PK")):
        output = client.call("GET", "/api/purchases/receptions/export?format=" + fmt)
        check(output.content.startswith(signature), "exportación de recepciones " + fmt)
        if fmt == "xlsx":
            check(load_workbook(BytesIO(output.content)).active.max_row > 1, "Excel contiene datos reales")
    return pid


def staff(admin, username, role):
    password = "Aa1!" + secrets.token_urlsafe(24)
    admin.json("POST", "/api/users", 201, json={"name": "Personal sintético", "username": username.lower(),
        "email": username.lower() + "@example.invalid", "password": password, "role": role})
    client = Client()
    client.json("POST", "/api/auth/login", json={"identifier": username.lower(), "password": password})
    return client


def upload(client, pid):
    image = BytesIO()
    Image.new("RGB", (32, 32), (20, 80, 150)).save(image, format="PNG")
    client.json("POST", f"/api/products/{pid}/image", files={"image": ("synthetic.png", image.getvalue(), "image/png")})
    product = client.json("GET", f"/api/products/{pid}")
    url = product["image_url"]
    response = client.call("GET", url)
    check(response.content.startswith(b"\x89PNG"), "upload y descarga autenticada de imagen")
    return {"url": url, "sha256": hashlib.sha256(response.content).hexdigest()}


def historical(client, tag, pid):
    html = client.call("GET", "/historical-imports").text
    token = re.search(r'csrfToken:\s*"([^"]+)"', html).group(1)
    headers = {"X-CSRFToken": token}
    csv = ("event_date;product_code;product_name;quantity;record_type;record_status;document_number;source_record_id;source_line_id;unit_price\n"
        f"2025-06-15;{tag};Producto sintético;3;sale;issued;DOC-{tag};SRC-{tag};1;8.00\n").encode("utf-8-sig")
    batch = client.json("POST", "/api/historical-imports/upload", 201, headers=headers,
        data={"source_system": "Docker-synthetic", "document_type": "historical_demand"},
        files={"file": ("synthetic.csv", csv, "text/csv")})["historical_import"]
    path = "/api/historical-imports/" + batch["id"]
    client.json("POST", path + "/preview", headers=headers, json={})
    dry = client.json("POST", path + "/dry-run", headers=headers, json={})
    client.json("POST", path + "/confirm", headers=headers, json={"confirmation_token": dry["confirmation_token"]})
    check(client.json("GET", f"/api/products/{pid}")["current_stock"] == 107,
        "histórico confirmado sin modificar existencias")
    return batch["id"]


def rejected_sql(connection, statement, parameters, code, label):
    try:
        connection.execute(text(statement), parameters)
    except DBAPIError as error:
        connection.rollback()
        check(error.orig.args[0] == code, label)
    else:
        connection.rollback()
        raise AssertionError("Database failed to reject: " + label)


def database_contract(public_id, pid):
    with engine().connect() as connection:
        check(connection.execute(text("SELECT COUNT(*) FROM customers WHERE is_walk_in=1")).scalar_one() == 1,
            "migración conserva un único cliente Público en general")
        rid = connection.execute(text("SELECT r.id FROM historical_demand_records r JOIN historical_imports h "
            "ON h.id=r.import_id WHERE h.public_id=:id"), {"id": public_id}).scalar_one()
        connection.rollback()
        rejected_sql(connection, "UPDATE historical_demand_records SET quantity=quantity+1 WHERE id=:id", {"id": rid},
            1644, "trigger bloquea UPDATE directo de histórico confirmado")
        rejected_sql(connection, "DELETE FROM historical_demand_records WHERE id=:id", {"id": rid},
            1644, "trigger bloquea DELETE directo de histórico confirmado")
        rejected_sql(connection, "UPDATE reconciliation_runs SET status='invalid'", {}, 4025, "CHECK de conciliación aplicado por MariaDB")
        rejected_sql(connection, "UPDATE products SET category_id=2147483647 WHERE id=:id", {"id": pid},
            1452, "FK de productos aplicada por MariaDB")


def reconciliation(client, tag):
    run = client.json("POST", "/api/reconciliation", 201,
        files={"file": ("synthetic.csv", f"product_code;quantity\n{tag};107\n".encode(), "text/csv")},
        data={"cutoff_at": datetime.now(timezone.utc).isoformat()})
    client.json("POST", f"/api/reconciliation/{run['id']}/confirm", json={})
    check(client.json("GET", f"/api/reconciliation/{run['id']}")["status"] == "confirmed", "conciliación CSV y confirmación")


def cuts(pid):
    from datetime import date, timedelta
    from unittest.mock import patch
    from sqlalchemy import event
    from app import create_app
    from app.extensions import db
    from app.models.reconciliation import ReconciliationCut
    from app.services import reconciliation_automation_service as automatic
    app = create_app()
    app.config["RECONCILIATION_ACTIVATED_ON"] = date.today().isoformat()
    when = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0) - timedelta(seconds=10)
    observed = []

    def transaction(connection, cursor, statement, parameters, context, many):
        if statement.upper().startswith("START TRANSACTION WITH CONSISTENT SNAPSHOT"):
            observed.append(connection.exec_driver_sql("SELECT @@tx_isolation, @@autocommit").one())

    with app.app_context(), patch.object(automatic, "scheduled_at", return_value=when):
        event.listen(db.engine, "after_cursor_execute", transaction)
        try:
            cut = automatic.capture_cut(date.today(), "0800")
            cut_id = cut.id
            stock = {row.product_id: row.quantity for row in cut.stock}
            again = automatic.capture_cut(date.today(), "0800")
            check(cut.snapshot_status == "captured" and stock[pid] == 107 and again.id == cut_id,
                "corte MariaDB: fotografía congelada e idempotencia")
            check(len(observed) == 1 and tuple(observed[0]) == ("REPEATABLE-READ", 0),
                "corte usa transacción real CONSISTENT SNAPSHOT, sin autocommit")
            db.session.add(ReconciliationCut(source=cut.source, scheduled_for=cut.scheduled_for))
            try:
                db.session.commit()
            except DBAPIError as error:
                db.session.rollback()
                check(error.orig.args[0] == 1062, "UNIQUE rechaza duplicar un corte")
            else:
                raise AssertionError("Duplicate cut was accepted")
        finally:
            event.remove(db.engine, "after_cursor_execute", transaction)


def permissions(client, tag):
    seller = Client()
    password = "Aa1!" + secrets.token_urlsafe(24)
    seller.json("POST", "/api/auth/register", 201, json={"name": "Vendedor sintético", "username": tag.lower(),
        "email": tag.lower() + "@example.invalid", "password": password})
    seller.json("POST", "/api/auth/login", json={"identifier": tag.lower(), "password": password})
    seller.call("POST", "/api/categories", 403, json={"name": "Prohibido"})
    seller.call("GET", "/api/reconciliation", 403)
    Client().call("GET", "/api/products", 401)
    client.call("GET", "/api/settings")
    check(True, "RBAC persistido: admin permitido, vendedor 403, anónimo 401; configuración disponible")


def fingerprint():
    hashes = {}
    with engine().connect() as connection:
        for name in BUSINESS:
            rows = [dict(row) for row in connection.execute(text(f"SELECT * FROM `{name}`")).mappings()]
            # Login timestamps legitimately change when validating another restart.
            if name == "users":
                for row in rows:
                    row.pop("last_login_at", None)
                    row.pop("updated_at", None)
            hashes[name] = {"count": len(rows), "sha256": hashlib.sha256(json.dumps(sorted(
                json.dumps(row, sort_keys=True, default=str) for row in rows)).encode()).hexdigest()}
    return hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args()
    wait_database()
    if os.environ["DOCKER_ENV_KIND"] != "disposable":
        raise RuntimeError("Tests only run on a disposable environment, never demo or valuable data.")
    verify_installation()
    client = login()
    pages(client)
    if args.persist:
        saved = json.loads(STATE.read_text())
        check(saved["environment_id"] == os.environ["ENVIRONMENT_ID"], "identidad del volumen persistente")
        check(saved["rows"] == fingerprint(), "persistencia: mismas filas, hashes y administrador sin duplicados")
        content = client.call("GET", saved["upload"]["url"]).content
        check(hashlib.sha256(content).hexdigest() == saved["upload"]["sha256"], "uploads persisten byte a byte")
        return
    tag = "DOCKER-" + secrets.token_hex(5).upper()
    pid = commercial(client, tag)
    uploaded = upload(client, pid)
    batch = historical(client, tag, pid)
    reconciliation(client, tag)
    cuts(pid)
    database_contract(batch, pid)
    permissions(client, tag)
    STATE.write_text(json.dumps({"environment_id": os.environ["ENVIRONMENT_ID"], "rows": fingerprint(),
        "upload": uploaded}, indent=2))
    print("PASS: snapshot de aceptación guardado únicamente en el volumen desechable")


if __name__ == "__main__":
    main()
