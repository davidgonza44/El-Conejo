#!/usr/bin/env python3
"""Suite aislada de la cáscara compartida y la lista de ventas: SQLite temporal, sin MySQL.

Cubre búsqueda global (productos/clientes/ventas, permisos por rol, mínimo 2 caracteres,
sin purchase_price ni datos de usuarios), campana (conteo exacto agotado + bajo stock,
productos reales, estado vacío, sin notificaciones inventadas), lista de ventas en vivo
(paginación, búsqueda por número/cliente/producto, fechas, método, unidades, totales),
detalle de venta, "Ver todas" del dashboard, modo referencia sin llamadas vivas y cero
escrituras en todos los GET.

Uso: python scripts/test_shell_core.py
"""
from __future__ import annotations

import sys
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from test_products_core import check, create_via_api, setup_app  # noqa: E402
import test_products_core  # noqa: E402

from app.extensions import db  # noqa: E402
from app.models import Category, Customer, Product, Sale, SaleLine, StockMovement  # noqa: E402
from app.services.sales_common import commercial_today, utc_boundary  # noqa: E402

SEARCH, ALERTS = "/api/shell/search", "/api/shell/alerts"
LONG = "Producto con nombre extremadamente largo " * 3 + "para probar el recorte de la interfaz sin desbordes X"


def _counts(app):
    with app.app_context():
        return {m.__tablename__: db.session.query(m).count() for m in (Product, Category, StockMovement, Sale, SaleLine, Customer)}


def main() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="shell-core-"))
    app, admin, inventario, vendedor, ids = setup_app(temp_root)
    app.logger.disabled = True
    today = commercial_today()

    # ---- Campana: estado vacío honesto ----
    check("alertas sin sesión -> 401", app.test_client().get(ALERTS).status_code == 401)
    empty = vendedor.get(ALERTS).json
    check("sin productos: 0 alertas y lista vacía (sin notificaciones inventadas)",
          empty["count"] == 0 and empty["items"] == [] and empty["href"] == "/products?status=bajo_stock", str(empty))

    long_name = LONG[:150]
    p_ok = create_via_api(admin, code="OK-1", name="Taladro Percutor", category_id=ids["construccion"], current_stock=50, minimum_stock=5, sale_price="120.00", purchase_price="80.00").json["id"]
    p_low = create_via_api(admin, code="LOW-1", name=long_name, category_id=ids["construccion"], current_stock=3, minimum_stock=5, sale_price="8.90", purchase_price="6.00").json["id"]
    p_out = create_via_api(admin, code="OUT-1", name="Cable THW 12", category_id=ids["electricidad"], current_stock=0, minimum_stock=10, sale_price="5.00", purchase_price="3.00").json["id"]
    p_edge = create_via_api(admin, code="EDGE-1", name="Disco de corte", category_id=ids["construccion"], current_stock=5, minimum_stock=5, sale_price="2.50").json["id"]
    with app.app_context():
        long_customer = Customer(name=("Cliente Corporativo Internacional " * 5)[:150], tax_id="J-11111111-1", phone="0412-5551234", email="largo@example.com")
        short_customer = Customer(name="Ana Pérez", tax_id="V-22222222", phone="0414-0000000")
        inactive = Customer(name="Cliente Inactivo Pérez", is_active=False)
        db.session.add_all([long_customer, short_customer, inactive])
        db.session.commit()
        long_id, short_id = long_customer.id, short_customer.id

    before = _counts(app)
    data = vendedor.get(ALERTS).json
    check("GET alertas no escribe", _counts(app) == before)
    check("conteo = agotado(1) + bajo stock(2, incluye stock == mínimo), sin doble conteo",
          data == {**data, "count": 3, "low_stock": 2, "out_of_stock": 1} and len(data["items"]) == 3, str(data))
    check("productos reales, agotado primero y luego por nombre, etiquetas exclusivas",
          [(i["id"], i["status"], i["status_label"]) for i in data["items"]] == [(p_out, "agotado", "Agotado"), (p_edge, "bajo_stock", "Bajo stock"), (p_low, "bajo_stock", "Bajo stock")], str(data["items"]))
    check("alertas sin purchase_price ni campos ajenos", all("purchase_price" not in i and set(i) <= {"id", "name", "image_url", "current_stock", "minimum_stock", "status", "status_label"} for i in data["items"]))
    check("nombre largo íntegro (recorte solo en CSS)", data["items"][2]["name"] == long_name)

    # ---- Búsqueda global ----
    check("búsqueda sin sesión -> 401", app.test_client().get(SEARCH, query_string={"q": "ta"}).status_code == 401)
    check("menos de 2 caracteres -> 400", vendedor.get(SEARCH, query_string={"q": "t"}).status_code == 400 and vendedor.get(SEARCH).status_code == 400)
    check("más de 80 caracteres -> 400", vendedor.get(SEARCH, query_string={"q": "x" * 81}).status_code == 400)
    r = vendedor.get(SEARCH, query_string={"q": "tal"}).json
    check("producto por nombre con destino real", [(p["id"], p["code"], p["href"]) for p in r["groups"]["products"]] == [(p_ok, "OK-1", "/products?search=OK-1")], str(r))
    check("producto por código", [p["id"] for p in vendedor.get(SEARCH, query_string={"q": "low-"}).json["groups"]["products"]] == [p_low])
    check("productos sin purchase_price ni precio de venta", all(set(p) == {"id", "code", "name", "stock_status", "href"} for p in r["groups"]["products"]))
    check("cliente por nombre / RIF / teléfono / email (activos, sin mostrador)",
          [c["id"] for c in vendedor.get(SEARCH, query_string={"q": "corporativo"}).json["groups"]["customers"]] == [long_id]
          and [c["id"] for c in vendedor.get(SEARCH, query_string={"q": "V-2222"}).json["groups"]["customers"]] == [short_id]
          and [c["id"] for c in vendedor.get(SEARCH, query_string={"q": "0412-555"}).json["groups"]["customers"]] == [long_id]
          and [c["id"] for c in vendedor.get(SEARCH, query_string={"q": "largo@"}).json["groups"]["customers"]] == [long_id]
          and vendedor.get(SEARCH, query_string={"q": "Inactivo"}).json["groups"]["customers"] == [])
    cust = vendedor.get(SEARCH, query_string={"q": "Ana P"}).json["groups"]["customers"][0]
    check("cliente: solo campos de presentación y destino /sales/customers?search=", set(cust) == {"id", "name", "tax_id", "phone", "href"} and cust["href"] == "/sales/customers?search=Ana+P%C3%A9rez")
    check("comodines escapados (% y _ literales)", vendedor.get(SEARCH, query_string={"q": "%%"}).json["total"] == 0 and vendedor.get(SEARCH, query_string={"q": "__"}).json["total"] == 0)
    check("sin coincidencias -> grupos vacíos, total 0", vendedor.get(SEARCH, query_string={"q": "zzzz"}).json["total"] == 0)

    def sell(client, customer_id, items, method="cash"):
        resp = client.post("/api/sales", json={"customer_id": customer_id, "payment_method": method, "payment_condition": "cash",
                                               "idempotency_key": str(uuid.uuid4()), "items": items})
        assert resp.status_code == 201, resp.json
        return resp.json
    s1 = sell(vendedor, long_id, [{"product_id": p_ok, "quantity": 3}, {"product_id": p_edge, "quantity": 2}], "transfer")   # 360 + 5 = 365, 5 uds
    s2 = sell(vendedor, short_id, [{"product_id": p_ok, "quantity": 1}], "cash")                                              # 120, 1 ud
    with app.app_context():
        db.session.get(Sale, s1["id"]).sold_at = utc_boundary(today - timedelta(days=10)) + timedelta(hours=16)
        db.session.commit()

    r = vendedor.get(SEARCH, query_string={"q": s2["sale_number"][-4:]}).json
    check("venta por número con destino al detalle real",
          [(s["sale_number"], s["customer"], s["total"], s["href"]) for s in r["groups"]["sales"]] == [(s2["sale_number"], "Ana Pérez", "120.00", f"/sales/{s2['sale_number']}")], str(r))
    check("venta por cliente, más reciente primero",
          [s["sale_number"] for s in vendedor.get(SEARCH, query_string={"q": "corporativo"}).json["groups"]["sales"]] == [s1["sale_number"]])
    check("búsqueda no escribe", _counts(app) == {**before, "sales": 2, "sale_lines": 3, "stock_movements": before["stock_movements"] + 3})
    check("grupos según permisos del rol: todos los roles del sistema leen los tres",
          set(admin.get(SEARCH, query_string={"q": "an"}).json["groups"]) == {"products", "customers", "sales"}
          and set(inventario.get(SEARCH, query_string={"q": "an"}).json["groups"]) == {"products", "customers", "sales"})
    many = admin.get(SEARCH, query_string={"q": "er"})
    check("límite por grupo <= 5", many.status_code == 200 and all(len(v) <= 5 for v in many.json["groups"].values()), many.get_data(as_text=True)[:300])

    # ---- Lista de ventas (API + página) ----
    api = vendedor.get("/api/sales").json
    check("API: units = SUM(quantity) por venta", [(s["sale_number"], s["units"], s["line_count"]) for s in api["items"]] == [(s2["sale_number"], 1, 1), (s1["sale_number"], 5, 2)])
    check("API: búsqueda por producto (nombre/código de renglón)", [s["id"] for s in vendedor.get("/api/sales", query_string={"search": "disco"}).json["items"]] == [s1["id"]]
          and [s["id"] for s in vendedor.get("/api/sales", query_string={"search": "EDGE-1"}).json["items"]] == [s1["id"]])
    check("API: método de pago", [s["id"] for s in vendedor.get("/api/sales", query_string={"payment_method": "transfer"}).json["items"]] == [s1["id"]]
          and vendedor.get("/api/sales", query_string={"payment_method": "bitcoin"}).status_code == 400)
    check("API: rango de fechas comerciales inclusivo",
          [s["id"] for s in vendedor.get("/api/sales", query_string={"date_from": (today - timedelta(days=10)).isoformat(), "date_to": (today - timedelta(days=10)).isoformat()}).json["items"]] == [s1["id"]]
          and [s["id"] for s in vendedor.get("/api/sales", query_string={"date_from": today.isoformat()}).json["items"]] == [s2["id"]]
          and vendedor.get("/api/sales", query_string={"date_from": "ayer"}).status_code == 400)

    before = _counts(app)
    html = vendedor.get("/sales/list").get_data(as_text=True)
    check("GET lista no escribe", _counts(app) == before)
    check("lista viva: columnas del contrato", all(c in html for c in ("Venta", "Fecha", "Cliente", "Productos", "Método de pago", "Total", "Estado", "Acciones")))
    check("lista viva: filas reales con detalle enlazado, unidades, método y total",
          f'href="/sales/{s1["sale_number"]}"' in html and "$ 365.00" in html and "$ 120.00" in html and "Transferencia" in html and "Completada" in html
          and "Mostrando 1 a 2 de 2 ventas" in html and "En proceso" not in html and "Cancelada" not in html and "142 ventas" not in html)
    check("lista viva: cliente largo íntegro con title", f'title="{("Cliente Corporativo Internacional " * 5)[:150]}"' in html)
    check("lista viva: filtros del servidor conservan valores", 'value="transfer" selected' in vendedor.get("/sales/list", query_string={"payment_method": "transfer"}).get_data(as_text=True)
          and "Mostrando 1 a 1 de 1 ventas" in vendedor.get("/sales/list", query_string={"search": "Ana"}).get_data(as_text=True)
          and "No se encontraron ventas" in vendedor.get("/sales/list", query_string={"search": "zzz"}).get_data(as_text=True))
    check("lista viva: método inválido -> 400", vendedor.get("/sales/list", query_string={"payment_method": "x"}).status_code == 400)
    for _ in range(9):
        sell(vendedor, short_id, [{"product_id": p_ok, "quantity": 1}])
    page2 = vendedor.get("/sales/list", query_string={"page": 2}).get_data(as_text=True)
    check("lista viva: paginación real de 10 (11 ventas -> 2 páginas)", "Mostrando 11 a 11 de 11 ventas" in page2 and 'aria-current="page">2<' in page2 and "page=1" in page2)
    check("lista viva: página inválida -> 400", vendedor.get("/sales/list", query_string={"page": "0"}).status_code == 400)

    detail = vendedor.get(f"/sales/{s1['sale_number']}").get_data(as_text=True)
    check("detalle vivo: venta real (cliente, vendedor, renglones, totales)", f"Venta #{s1['sale_number']}" in detail and "Taladro Percutor" in detail
          and "Disco de corte" in detail and "$ 365.00" in detail and "Productos (5)" in detail and "RIF: J-11111111-1" in detail and "no disponible" not in detail)
    check("detalle vivo: inexistente -> 404", vendedor.get("/sales/VTA-999999").status_code == 404)
    check("detalle vivo: rol sin sales:read redirige", app.test_client().get(f"/sales/{s1['sale_number']}").status_code in (302, 401))

    dash = vendedor.get("/sales").get_data(as_text=True)
    check("dashboard vivo: 'Ver todas' -> /sales/list con el período", f'href="/sales/list?date_from={today.replace(day=1).isoformat()}&amp;date_to={today.isoformat()}" class="sa-link">Ver todas' in dash)
    summary = admin.get("/inventory/summary").get_data(as_text=True)
    shell_before = _counts(app)
    home = admin.get("/dashboard").get_data(as_text=True)
    predictive = admin.get("/predictive/demand").get_data(as_text=True)
    check("cáscara viva: búsqueda y campana reales en Inicio, Ventas, Inventario y Predictivo, sin '3 sin leer'",
          all('id="ix-search-input"' in h and 'id="ix-bell"' in h and "3 sin leer" not in h for h in (home, dash, summary, predictive)))
    check("GET Inicio / Predictivo vivos no escriben", _counts(app) == shell_before)

    # ---- Referencia: maqueta estática, sin ganchos vivos ----
    app.debug = True
    refs = {u: vendedor.get(u).get_data(as_text=True) for u in ("/sales/list?ref=1", "/sales?ref=1", f"/sales/VTA-000156?ref=1")}
    refs["/dashboard?ref=1"] = admin.get("/dashboard?ref=1").get_data(as_text=True)
    refs["/predictive/demand?ref=1"] = admin.get("/predictive/demand?ref=1").get_data(as_text=True)
    app.debug = False
    check("referencia: fixture determinista (142 ventas, VTA-000156) sin datos reales",
          "142 ventas" in refs["/sales/list?ref=1"] and "Ana Pérez" not in refs["/sales/list?ref=1"] and "Venta #VTA-000156" in refs[f"/sales/VTA-000156?ref=1"])
    check("referencia: sin combobox/campana vivos ni /api/shell", all('id="ix-search-input"' not in h and "/api/shell" not in h and "3 sin leer" in h for h in refs.values()))
    check("referencia no escribe", _counts(app) == {**before, "sales": 11, "sale_lines": before["sale_lines"] + 9, "stock_movements": before["stock_movements"] + 9})

    print(f"\n{test_products_core.CHECKS_RUN} comprobaciones, {test_products_core.FAILURES} fallos")
    return 1 if test_products_core.FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
