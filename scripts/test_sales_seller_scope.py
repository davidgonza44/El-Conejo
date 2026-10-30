"""Alcance de ventas del vendedor: SQLite temporal, nunca la base operativa.

vendedor_A solo consulta sus ventas. Admin e inventario conservan la vista global.
El KPI de clientes nuevos no se recorta: no existe dueño vendedor del cliente.

Uso: .\\.venv\\Scripts\\python.exe -B scripts/test_sales_seller_scope.py
"""
import sys
import tempfile
import uuid
from pathlib import Path

import test_products_core
from test_products_core import check, create_via_api, login, setup_app

from app.extensions import db
from app.models import Customer, Notification, Sale, User
from app.models.customer import WALK_IN_NAME
from app.models.user import ROLE_VENDEDOR
from app.services.sales_common import commercial_today


def _numbers(payload: dict) -> set[str]:
    return {item["sale_number"] for item in payload["items"]}


def run() -> int:
    root = Path(tempfile.mkdtemp(prefix="sales-scope-"))
    app, admin, inventario, vendedor_a, ids = setup_app(root)
    app.logger.disabled = True
    with app.app_context():
        seller_b = User(name="Vendedor B", email="venb@core.test", username="core_ven_b", role=ROLE_VENDEDOR)
        seller_b.set_password("ven123")
        customer = Customer(name="Cliente Alcance", tax_id="J-55500011-2")
        walk_in = Customer(name=WALK_IN_NAME, is_walk_in=True)
        db.session.add_all([seller_b, customer, walk_in])
        db.session.commit()
        b_id, customer_id = seller_b.id, customer.id
        a_id = User.query.filter_by(username="core_ven").one().id
        admin_id = ids["admin_id"]
        inventario_id = User.query.filter_by(username="core_inv").one().id
    vendedor_b = app.test_client()
    login(vendedor_b, "core_ven_b", "ven123")
    product = create_via_api(
        admin, code="ALC-1", name="Producto Alcance", category_id=ids["construccion"],
        current_stock=40, sale_price="10.00",
    ).json["id"]
    api = "/api/sales"
    today = commercial_today().isoformat()

    def sell(client, method, key=None):
        response = client.post(api, json={
            "customer_id": customer_id, "payment_method": method, "payment_condition": "cash",
            "idempotency_key": key or str(uuid.uuid4()),
            **({"cash_received": "10.00"} if method == "cash" else {}),
            "items": [{"product_id": product, "quantity": 1}],
        })
        assert response.status_code == 201, response.get_data(as_text=True)
        return response.json

    own = [sell(vendedor_a, "cash") for _ in range(11)]
    b_key = "clave-ajena-b-0001"
    foreign = sell(vendedor_b, "transfer", b_key)
    foreign_cash = sell(vendedor_b, "cash")
    a_numbers = {sale["sale_number"] for sale in own}
    b_numbers = {foreign["sale_number"], foreign_cash["sale_number"]}
    probe = foreign["sale_number"]

    listed = vendedor_a.get(api).json
    check("A ve sus ventas", _numbers(listed) <= a_numbers and listed["count"] == 10)
    check("A no ve las de B", _numbers(listed).isdisjoint(b_numbers))
    listed_b = vendedor_b.get(api).json
    check("B no ve las de A", _numbers(listed_b) == b_numbers and listed_b["total"] == 2)
    check("total paginado de A cuenta solo las de A", listed["total"] == 11 and listed["pages"] == 2)

    found = vendedor_a.get(api, query_string={"search": probe}).json
    check("búsqueda de A no encuentra ventas de B", found["total"] == 0 and found["items"] == [])
    own_hit = vendedor_a.get(api, query_string={"search": own[0]["sale_number"]}).json
    check("búsqueda de A sí encuentra una venta propia", _numbers(own_hit) == {own[0]["sale_number"]})

    by_method = vendedor_a.get(api, query_string={"payment_method": "transfer"}).json
    by_date = vendedor_a.get(api, query_string={"date_from": today, "date_to": today}).json
    by_customer = vendedor_a.get(api, query_string={"customer_id": customer_id}).json
    check("método, fecha y customer_id no amplían el scope",
          by_method["total"] == 0 and by_date["total"] == 11 and by_customer["total"] == 11
          and _numbers(by_date).isdisjoint(b_numbers) and _numbers(by_customer).isdisjoint(b_numbers))

    page2 = vendedor_a.get(api, query_string={"page": 2, "per_page": 10}).json
    check("página 2 tampoco mezcla ventas", _numbers(page2) <= a_numbers and page2["count"] == 1 and page2["total"] == 11)

    missing = vendedor_a.get(f"{api}/VTA-999999")
    stolen = vendedor_a.get(f"{api}/{probe}")
    stolen_html = vendedor_a.get(f"/sales/{probe}")
    missing_html = vendedor_a.get("/sales/VTA-999999")
    # El 404 cita solo el número pedido, igual que una venta inexistente, sin cliente ni monto.
    check("A abre venta de B por API -> 404 igual que una inexistente",
          stolen.status_code == 404 and missing.status_code == 404
          and stolen.json["error"] == f"La venta {probe} no existe."
          and missing.json["error"] == "La venta VTA-999999 no existe."
          and "Cliente Alcance" not in stolen.get_data(as_text=True)
          and "10.00" not in stolen.get_data(as_text=True))
    check("A abre venta de B por HTML -> 404",
          stolen_html.status_code == 404 and missing_html.status_code == 404
          and "Cliente Alcance" not in stolen_html.get_data(as_text=True))
    check("A sí abre su propia venta", vendedor_a.get(f"{api}/{own[0]['sale_number']}").status_code == 200)

    everyone = admin.get(api, query_string={"per_page": 20}).json
    inventory_view = inventario.get(api, query_string={"per_page": 20}).json
    check("admin ve ventas de ambos", a_numbers | b_numbers <= _numbers(everyone) and everyone["total"] == 13)
    check("inventario conserva vista de ambos", _numbers(inventory_view) == _numbers(everyone) and inventory_view["total"] == 13)
    check("admin e inventario abren la venta de B",
          admin.get(f"{api}/{probe}").status_code == 200 and inventario.get(f"{api}/{probe}").status_code == 200)

    period = {"start": today, "end": today}
    dash_a = vendedor_a.get(f"{api}/dashboard", query_string=period).json
    dash_admin = admin.get(f"{api}/dashboard", query_string=period).json
    dash_inv = inventario.get(f"{api}/dashboard", query_string=period).json
    check("dashboard A usa exclusivamente ventas de A",
          dash_a["kpis"]["revenue"]["value"] == "110.00" and dash_a["kpis"]["sales_count"]["value"] == 11
          and dash_a["kpis"]["average_ticket"]["value"] == "10.00"
          and {item["sale_number"] for item in dash_a["latest_sales"]["items"]}.isdisjoint(b_numbers)
          and dash_a["recent_sales"]["total"] == 11
          and dash_a["payment_methods"]["items"] == [{"method": "cash", "label": "Efectivo", "revenue": "110.00", "count": 11, "percent": 100.0}]
          and dash_a["top_customers"]["items"][0]["sales"] == 11
          and dash_a["by_category"]["total"] == "110.00")
    check("dashboard admin conserva A+B",
          dash_admin["kpis"]["revenue"]["value"] == "130.00" and dash_admin["kpis"]["sales_count"]["value"] == 13
          and b_numbers <= {item["sale_number"] for item in dash_admin["latest_sales"]["items"]})
    check("dashboard inventario conserva A+B", dash_inv["kpis"]["revenue"]["value"] == "130.00" and dash_inv["kpis"]["sales_count"]["value"] == 13)
    check("Clientes nuevos no representa ventas propias: A y admin ven el mismo conteo de clientes creados",
          dash_a["kpis"]["new_customers"]["value"] == dash_admin["kpis"]["new_customers"]["value"])

    with app.app_context():
        before = Sale.query.count()
    for field in ("seller_id", "user_id", "created_by", "responsible_id"):
        rejected = vendedor_a.post(api, json={
            "customer_id": customer_id, "payment_method": "cash", "payment_condition": "cash",
            "idempotency_key": str(uuid.uuid4()), field: b_id,
            "items": [{"product_id": product, "quantity": 1}],
        })
        with app.app_context():
            unchanged = Sale.query.count() == before
        check(f"{field} no cambia el responsable", rejected.status_code == 400 and unchanged)

    replay = vendedor_a.post(api, json={
        "customer_id": customer_id, "payment_method": "cash", "payment_condition": "cash",
        "idempotency_key": b_key, "items": [{"product_id": product, "quantity": 1}],
    })
    body = replay.get_data(as_text=True)
    with app.app_context():
        rows = Sale.query.filter_by(idempotency_key=b_key).all()
        still_one = len(rows) == 1 and rows[0].seller_id == b_id and Sale.query.count() == before
    check("idempotency_key de B enviada por A -> 409 sin datos y sin otra venta",
          replay.status_code == 409 and still_one and "VTA-" not in body and "Cliente Alcance" not in body
          and "10.00" not in body and set(replay.json) == {"error"})

    search_a = vendedor_a.get("/api/shell/search", query_string={"q": probe}).json
    search_admin = admin.get("/api/shell/search", query_string={"q": probe}).json
    check("shell search de A no muestra venta de B",
          search_a["groups"].get("sales") == [] and "Cliente Alcance" not in vendedor_a.get("/api/shell/search", query_string={"q": probe}).get_data(as_text=True))
    check("shell search de admin sí muestra la venta de B",
          [item["sale_number"] for item in search_admin["groups"]["sales"]] == [probe])

    with app.app_context():
        def mentions(user_id):
            return [row for row in Notification.query.filter_by(user_id=user_id, entity_type="sale") if probe in row.title]
        check("notificación de la venta de B no llega a A", mentions(a_id) == [])
        check("notificación de una venta de A no llega a B",
              Notification.query.filter(Notification.user_id == b_id, Notification.entity_type == "sale",
                                        Notification.title.contains(own[0]["sale_number"])).count() == 0)
        check("admin sí recibe la notificación de la venta", len(mentions(admin_id)) == 1)
        check("inventario sí recibe la notificación de la venta", len(mentions(inventario_id)) == 1)
        db.session.remove()
        db.engine.dispose()

    print(f"\n{test_products_core.CHECKS_RUN} comprobaciones, {test_products_core.FAILURES} fallos")
    return 1 if test_products_core.FAILURES else 0


if __name__ == "__main__":
    sys.exit(run())
