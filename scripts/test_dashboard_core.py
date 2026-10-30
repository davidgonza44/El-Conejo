#!/usr/bin/env python3
"""Suite aislada de Inicio (dashboard) fases 1 y 2: SQLite temporal, sin MySQL ni ferreteria_conejo.

Cubre semántica de KPIs, distribución, movimientos, alertas, comparación con cierre de
ayer, ventas reales de Sales Core (hoy/ayer, clientes atendidos con regla de mostrador,
serie de 7 días vs. 7 anteriores, más vendidos del mes), permisos y cero escrituras del GET.

Uso: python scripts/test_dashboard_core.py
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
from app.models import Category, Customer, DeliveryNote, InventorySnapshot, Product, Sale, SaleLine, StockMovement  # noqa: E402
from app.models.customer import WALK_IN_NAME  # noqa: E402
from app.services.inventory_snapshot_service import commercial_today  # noqa: E402
from app.services.sales_common import utc_boundary  # noqa: E402

FIXTURE = ROOT / "app" / "static" / "js" / "dashboard_reference_data.js"
FIXTURE_VALUES = ("$ 18,450.00", "3,245", "12.4%", '"47"', "Constructora del Norte", "Taladro Percutor")
ZERO_SALES = {"available": True, "value": "0.00", "comparison": {"available": True, "value": "0.00", "delta": "0.00", "change_percent": None, "reason": "no_activity"}}


def _counts(app):
    with app.app_context():
        return {m.__tablename__: db.session.query(m).count()
                for m in (Product, Category, StockMovement, InventorySnapshot, Sale, SaleLine, Customer, DeliveryNote)}


def _noon(day):
    """UTC naive a mediodía comercial de ese día (America/Caracas = UTC-4)."""
    return utc_boundary(day) + timedelta(hours=16)


def main() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="dashboard-core-"))
    app, admin, inventario, vendedor, ids = setup_app(temp_root)
    cat_a, cat_b = ids["construccion"], ids["electricidad"]
    with app.app_context():
        db.session.get(Category, cat_a).color = "orange"
        db.session.get(Category, cat_b).color = "teal"
        cat_c = Category(name="Pintura", color="pink")
        db.session.add(cat_c)
        db.session.commit()
        cat_c = cat_c.id

    # --- Permisos y vacío -------------------------------------------------------------
    check("API sin sesión -> 401", app.test_client().get("/api/dashboard/summary").status_code == 401)
    check("API vendedor -> 403", vendedor.get("/api/dashboard/summary").status_code == 403)
    denied_page = vendedor.get("/dashboard")
    check("página vendedor -> 403", denied_page.status_code == 403
          and "No tiene permisos para acceder a esta sección." in denied_page.get_data(as_text=True))
    empty = admin.get("/api/dashboard/summary").json
    check("vacío: 0 en stock y sin distribución", empty["kpis"]["products_in_stock"]["value"] == 0 and empty["inventory_distribution"]["items"] == [])
    check("vacío: movimientos [] disponible (vacío real, no error)", empty["recent_movements"] == {"available": True, "items": []})
    check("vacío: alertas []", empty["stock_alerts"] == {"available": True, "items": []})

    # --- Semilla: estados exclusivos -------------------------------------------------------
    ok = create_via_api(admin, code="OK-1", name="Disponible", category_id=cat_a, current_stock=30, minimum_stock=10)
    low = create_via_api(admin, code="LOW-1", name="Bajo", category_id=cat_a, current_stock=5, minimum_stock=10)
    out = create_via_api(admin, code="OUT-1", name="Agotado", category_id=cat_b, current_stock=0, minimum_stock=5)
    ok_b = create_via_api(admin, code="OK-2", name="Cable", category_id=cat_b, current_stock=7, minimum_stock=2)
    exact = create_via_api(admin, code="EQ-1", name="Igual al mínimo", category_id=cat_c, current_stock=4, minimum_stock=4)
    inactive = create_via_api(admin, code="INA-1", name="Inactivo con stock", category_id=cat_c, current_stock=9)
    admin.delete(f"/api/products/{inactive.json['id']}")
    check("semilla", all(r.status_code == 201 for r in (ok, low, out, ok_b, exact, inactive)))
    admin.post("/api/inventory/exit", json={"product_id": ok.json["id"], "quantity": 3, "reason": "Venta mostrador"})
    admin.post("/api/inventory/adjustment", json={"product_id": ok_b.json["id"], "new_stock": 6, "reason": "Conteo físico"})

    before = _counts(app)
    data = admin.get("/api/dashboard/summary").json
    check("GET no escribe", _counts(app) == before, str(before))
    check("zona horaria comercial", data["timezone"] == "America/Caracas" and data["today"] == commercial_today().isoformat())

    kpis = data["kpis"]
    # activos con stock > 0: OK-1, LOW-1, OK-2, EQ-1 (agotado e inactivo excluidos)
    check("products_in_stock = activos con stock > 0", kpis["products_in_stock"]["value"] == 4, str(kpis["products_in_stock"]))
    check("agotado excluido de en stock", kpis["products_in_stock"]["value"] != 5)
    # alertas exclusivas: bajo (LOW-1, EQ-1) + agotado (OUT-1) = 3; sin doble conteo
    check("active_alerts = bajo + agotado exclusivos", kpis["active_alerts"]["value"] == 3, str(kpis["active_alerts"]))
    # Sin snapshot y todo creado hoy: el universo de ayer está vacío -> 0 exacto (no "sin comparación").
    check("sin snapshot y universo de ayer vacío -> comparación exacta en 0",
          kpis["products_in_stock"]["comparison"] == {"available": True, "value": 0, "delta": 4, "change_percent": None, "reason": "ok"}
          and kpis["active_alerts"]["comparison"] == {"available": True, "value": 0, "delta": 3, "change_percent": None, "reason": "ok"})
    today = commercial_today()
    check("sin ventas: Ventas de hoy = 0.00 disponible (no 'módulo no disponible')", kpis["sales_today"] == ZERO_SALES, str(kpis["sales_today"]))
    check("sin ventas: Clientes atendidos = 0 disponible", kpis["customers_served"]["value"] == 0 and kpis["customers_served"]["available"] is True
          and kpis["customers_served"]["comparison"]["reason"] == "no_activity")
    chart = data["sales_chart"]
    check("sin ventas: gráfico con 7 ceros reales y 7 días anteriores", chart["available"] is True and chart["current"] == ["0.00"] * 7 == chart["previous"]
          and chart["days"][-1] == today.isoformat() and chart["days"][0] == (today - timedelta(days=6)).isoformat()
          and chart["previous_days"] == [(today - timedelta(days=13 - i)).isoformat() for i in range(7)], str(chart))
    check("sin ventas: más vendidos [] disponible, mes en curso, categoría actual declarada",
          data["top_products"] == {"available": True, "period": {"start": today.replace(day=1).isoformat(), "end": today.isoformat()},
                                   "grouping": "current_product_category", "items": []}, str(data["top_products"]))

    dist = data["inventory_distribution"]
    by_name = {i["name"]: i for i in dist["items"]}
    check("distribución mismo universo", dist["center_value"] == 4 and sum(i["value"] for i in dist["items"]) == 4 and dist["center_label"] == "Productos totales")
    check("distribución por categoría", by_name["Construcción"]["value"] == 2 and by_name["Electricidad"]["value"] == 1 and by_name["Pintura"]["value"] == 1)
    check("colores persistidos", by_name["Construcción"]["color"] == "orange" and by_name["Electricidad"]["color"] == "teal" and by_name["Pintura"]["color"] == "pink")
    check("porcentajes", by_name["Construcción"]["percent"] == 50.0 and by_name["Pintura"]["percent"] == 25.0)
    check("orden por conteo desc", dist["items"][0]["name"] == "Construcción")

    moves = data["recent_movements"]["items"]
    with app.app_context():
        expected = [m.id for m in StockMovement.query.order_by(StockMovement.created_at.desc(), StockMovement.id.desc()).limit(5)]
    check("movimientos: últimos 5 en orden created_at desc, id desc", [m["id"] for m in moves] == expected and len(moves) == 5)
    check("movimiento: referencia honesta", all(m["reference"] == f"MOV-{m['id']:06d}" for m in moves))
    check("movimiento: ajuste con delta negativo", moves[0]["movement_type"] == "ajuste" and moves[0]["delta"] == -1 and moves[0]["title"] == "Ajuste de inventario" and moves[0]["reason"] == "Conteo físico")
    check("movimiento: salida", moves[1]["movement_type"] == "salida" and moves[1]["delta"] == -3 and moves[1]["product_name"] == "Disponible")
    check("movimiento: sin campos inventados", not ({"supplier", "customer", "branch", "status"} & set(moves[0])))

    alerts = data["stock_alerts"]["items"]
    check("alertas: agotado primero, luego bajo, activos", [a["status"] for a in alerts] == ["agotado", "bajo_stock", "bajo_stock"] and {a["name"] for a in alerts} == {"Agotado", "Bajo", "Igual al mínimo"})
    check("alertas: campos", all({"id", "name", "image_url", "current_stock", "minimum_stock", "status"} <= set(a) for a in alerts))

    actions = {a["id"]: a for a in data["quick_actions"]}
    check("acciones: rutas reales", actions["new_product"]["href"] == "/products" and actions["new_sale"]["href"] == "/sales/new" and actions["reports"]["href"] == "/reports/sales")
    check("acciones: compra abre el flujo existente", actions["new_purchase"]["enabled"] is True
          and actions["new_purchase"]["href"] == "/purchases?new=1"
          and actions["new_purchase"]["description"] == "Crear una nueva orden de compra")
    check("acciones: Registrar venta habilitada para admin (sales:write) con copy real",
          actions["new_sale"]["enabled"] is True and actions["new_sale"]["description"] == "Crear una nueva venta")
    inv_actions = {a["id"]: a for a in inventario.get("/api/dashboard/summary").json["quick_actions"]}
    check("acciones: inventario sin sales:write -> Registrar venta deshabilitada honestamente", inv_actions["new_sale"]["enabled"] is False)

    # --- Sales Core: hoy/ayer, mostrador, 7 días, más vendidos ------------------------------------------
    yesterday = today - timedelta(days=1)
    with app.app_context():
        walk_in = Customer(name=WALK_IN_NAME, is_walk_in=True)
        c1 = Customer(name="Constructora A", tax_id="J-11111111-1")
        c2 = Customer(name="Ferretería B", tax_id="J-22222222-2")
        db.session.add_all([walk_in, c1, c2])
        db.session.commit()
        walk_in_id, c1_id, c2_id = walk_in.id, c1.id, c2.id
    # Productos con precio conocido y stock amplio; OK-1 (sale_price 2) ya existe.
    tal = create_via_api(admin, code="TAL-1", name="Taladro", category_id=cat_a, current_stock=500, sale_price="100.00").json["id"]
    cab = create_via_api(admin, code="CAB-1", name="Cable", category_id=cat_b, current_stock=500, sale_price="5.00").json["id"]
    with app.app_context():
        db.session.get(Product, tal).image_url = "/uploads/products/taladro.png"
        db.session.commit()

    def sell(customer_id, items):
        r = vendedor.post("/api/sales", json={"customer_id": customer_id, "payment_method": "cash", "payment_condition": "cash",
                                              "idempotency_key": str(uuid.uuid4()), "items": items})
        assert r.status_code == 201, r.json
        return r.json["id"]

    # Hoy: A x2 (100 + 200), B x1 (25), 3 tickets de mostrador (5 c/u) -> total 340, clientes 2 + 3 = 5.
    s_a1 = sell(c1_id, [{"product_id": tal, "quantity": 1}])
    s_a2 = sell(c1_id, [{"product_id": tal, "quantity": 2}])
    s_b = sell(c2_id, [{"product_id": cab, "quantity": 5}])
    walk_ins = [sell(walk_in_id, [{"product_id": cab, "quantity": 1}]) for _ in range(3)]
    # Ayer: B x1 (10) + 1 mostrador (5) -> 15, clientes 2. Hace 10 días (semana anterior): 20. Hace 20 días (fuera): 7.
    s_y1 = sell(c2_id, [{"product_id": cab, "quantity": 2}])
    s_y2 = sell(walk_in_id, [{"product_id": cab, "quantity": 1}])
    s_prev = sell(walk_in_id, [{"product_id": ok.json["id"], "quantity": 10}])
    s_out = sell(walk_in_id, [{"product_id": cab, "quantity": 1}, {"product_id": ok.json["id"], "quantity": 1}])
    with app.app_context():
        for sid in (s_a1, s_a2, s_b, *walk_ins):
            db.session.get(Sale, sid).sold_at = _noon(today)
        db.session.get(Sale, s_y1).sold_at = _noon(yesterday)
        db.session.get(Sale, s_y2).sold_at = utc_boundary(today) - timedelta(minutes=1)   # 23:59 Caracas de ayer = 03:59 UTC de hoy
        db.session.get(Sale, s_prev).sold_at = _noon(today - timedelta(days=10))
        db.session.get(Sale, s_out).sold_at = _noon(today - timedelta(days=20))
        db.session.commit()
    # Nota de entrega (salida de stock, NO venta): jamás debe sumar en Sales Core.
    note = admin.post("/api/delivery-notes", json={"customer_name": "Cliente de nota", "items": [{"product_id": tal, "quantity": 3}]})
    check("nota de entrega creada", note.status_code == 201, str(note.json))

    before = _counts(app)
    data = admin.get("/api/dashboard/summary").json
    check("GET con ventas no escribe", _counts(app) == before)
    k = data["kpis"]
    check("Ventas de hoy = SUM(total) completadas de hoy (UTC->Caracas), sin notas de entrega",
          k["sales_today"]["value"] == "340.00", str(k["sales_today"]))
    check("Ventas ayer: 15.00 incluye la venta a las 23:59 Caracas; delta y % reales",
          k["sales_today"]["comparison"] == {"available": True, "value": "15.00", "delta": "325.00", "change_percent": 2166.7, "reason": "ok"}, str(k["sales_today"]["comparison"]))
    check("Clientes atendidos = 2 identificados distintos + 3 tickets de mostrador = 5 (no 6 ni 3)",
          k["customers_served"]["value"] == 5, str(k["customers_served"]))
    check("Clientes atendidos ayer = 1 identificado + 1 mostrador = 2 -> +150%",
          k["customers_served"]["comparison"] == {"available": True, "value": 2, "delta": 3, "change_percent": 150.0, "reason": "ok"}, str(k["customers_served"]["comparison"]))

    chart = data["sales_chart"]
    check("7 días: hoy 340, ayer 15, resto 0", chart["current"] == ["0.00"] * 5 + ["15.00", "340.00"], str(chart["current"]))
    idx = chart["previous_days"].index((today - timedelta(days=10)).isoformat())
    check("7 anteriores: 20 hace 10 días, ceros reales, sin la venta de hace 20 días",
          chart["previous"][idx] == "20.00" and sum(float(v) for v in chart["previous"]) == 20.0, str(chart["previous"]))

    top = data["top_products"]["items"]
    # Mes en curso: puede excluir hace 10/20 días según el día del mes; recalculamos desde la BD.
    with app.app_context():
        month_start = utc_boundary(today.replace(day=1))
        expected = {}
        for line in SaleLine.query.join(Sale).filter(Sale.sold_at >= month_start).all():
            u, r = expected.get(line.product_id, (0, 0.0))
            expected[line.product_id] = (u + line.quantity, r + float(line.line_total))
    ranked = sorted(expected.items(), key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]))
    check("más vendidos: SUM(quantity), SUM(line_total), orden unidades desc / ingresos desc",
          [(i["product_id"], i["units"], float(i["revenue"])) for i in top] == [(pid, u, round(r, 2)) for pid, (u, r) in ranked], str(top))
    by_id = {i["product_id"]: i for i in top}
    check("más vendidos: cable lidera por unidades (>= 8 hoy) aunque el taladro ingrese más",
          top[0]["product_id"] == cab and by_id[cab]["units"] >= 8 and float(by_id[tal]["revenue"]) >= 300.0)
    check("más vendidos: imagen real del producto actual y categoría actual solo de presentación",
          by_id[tal]["image_url"] == "/uploads/products/taladro.png" and by_id[tal]["category"] == "Construcción" and by_id[cab]["image_url"] is None)
    check("más vendidos: nombre/código del snapshot", by_id[tal]["name"] == "Taladro" and by_id[tal]["code"] == "TAL-1")
    check("más vendidos: la nota de entrega (3 taladros) no suma", by_id[tal]["units"] == expected[tal][0] and expected[tal][0] in (3, 13))

    moves = data["recent_movements"]["items"]
    linked = [m for m in moves if m["sale_number"]]
    check("movimientos: salidas de venta muestran Venta #VTA-… y cliente real",
          linked and all(m["title"] == f"Venta #{m['sale_number']}" and m["sale_number"].startswith("VTA-") and m["customer_name"] for m in linked), str(moves[:2]))
    check("movimientos: la nota de entrega no se disfraza de venta",
          moves[0]["sale_number"] is None and moves[0]["title"] == "Salida de inventario"
          and moves[0]["source_type"] == "delivery_note"
          and moves[0]["reference"] == note.json["delivery_note"]["note_number"])

    # Denominador cero: ayer sin ventas -> "Nuevo", nunca infinito.
    with app.app_context():
        for sid in (s_y1, s_y2):
            db.session.get(Sale, sid).sold_at = _noon(today - timedelta(days=3))
        db.session.commit()
    k = admin.get("/api/dashboard/summary").json["kpis"]
    check("ayer = 0 y hoy > 0 -> reason new, sin porcentaje",
          k["sales_today"]["comparison"] == {"available": True, "value": "0.00", "delta": "340.00", "change_percent": None, "reason": "new"}
          and k["customers_served"]["comparison"]["reason"] == "new", str(k["sales_today"]))
    # Los productos de venta salen del universo "en stock" (inactivos) para no alterar los conteos de fase 1.
    admin.delete(f"/api/products/{tal}")
    admin.delete(f"/api/products/{cab}")

    # --- Cierre de ayer real -> delta real -----------------------------------------------------
    yesterday = commercial_today() - timedelta(days=1)
    with app.app_context():
        rows = []
        for pid, stock, minimum, active in ((ok.json["id"], 30, 10, True), (low.json["id"], 12, 10, True), (out.json["id"], 0, 5, True),
                                            (ok_b.json["id"], 0, 2, True), (exact.json["id"], 4, 4, True), (inactive.json["id"], 9, 0, False)):
            rows.append(InventorySnapshot(snapshot_date=yesterday, product_id=pid, stock=stock, minimum_stock=minimum,
                                          product_is_active=active, category_id=cat_a, category_is_active=True))
        db.session.add_all(rows)
        db.session.commit()
    kpis = admin.get("/api/dashboard/summary").json["kpis"]
    # ayer: activos 5, agotados 2 -> en stock 3; hoy 4 -> delta +1 (33.3%)
    check("ayer en stock desde snapshot", kpis["products_in_stock"]["comparison"] == {"available": True, "value": 3, "delta": 1, "change_percent": 33.3, "reason": "ok"}, str(kpis["products_in_stock"]))
    # ayer: bajo (EQ-1) 1 + agotado 2 = 3; hoy 3 -> delta 0
    check("ayer alertas desde snapshot (exclusivas)", kpis["active_alerts"]["comparison"] == {"available": True, "value": 3, "delta": 0, "change_percent": 0.0, "reason": "ok"}, str(kpis["active_alerts"]))

    # --- Otros: > 6 categorías ----------------------------------------------------------------------
    with app.app_context():
        for n in range(5):
            cat = Category(name=f"Extra {n}", color="blue")
            db.session.add(cat)
            db.session.flush()
            db.session.add(Product(code=f"EX-{n}", name=f"Extra {n}", category_id=cat.id, current_stock=1, minimum_stock=0))
        db.session.commit()
    dist = admin.get("/api/dashboard/summary").json["inventory_distribution"]
    otros = dist["items"][-1]
    check("más de 6 categorías -> top 5 + Otros", len(dist["items"]) == 6 and otros["name"] == "Otros" and otros["color"] == "gray" and otros["id"] is None)
    check("Otros suma real", sum(i["value"] for i in dist["items"]) == dist["center_value"] == 9 and otros["value"] == 3)

    # --- Páginas: live sin fixture, referencia intacta -------------------------------------------------
    html = admin.get("/dashboard").get_data(as_text=True)
    sidebar = html[html.find('id="ix-sidebar"'):html.find("</aside>")]
    check("live no carga fixture", "dashboard_reference_data.js" not in html and "dashboard.js" in html)
    check("live sin valores del fixture", not any(v in html for v in ("18,450", "3,245", "12.4%", "16,410")))
    check("live: cáscara Pro compartida y predictivo visible",
          'ix-brand-name">El Conejo</span>' in sidebar and "Sistema de Inventario" in sidebar
          and "Ferretería Pro" not in sidebar and "Pronóstico de demanda" in sidebar
          and "Proveedores" in sidebar and "Configuración" in sidebar and "?ref=1" not in sidebar)
    check("live: sin selector de sucursal y usuario real", "Sucursal" not in sidebar and "Admin" in sidebar and "admin@ferreteriapro.com" not in sidebar)
    check("live: sin filtros ni reportes viejos",
          "f-date-from" not in html and "chart-stock-minimum" not in html and 'id="ix-account-logout"' in html)
    nav_at = html.find('class="ix-actions"')
    nav = html[nav_at:html.find("</nav>", nav_at)]
    check("live: acciones aprobadas sin # muertos", all(t in nav for t in ("Nuevo producto", "Registrar venta", "Registrar compra", "Ver reportes"))
          and 'data-action="new_purchase" title' in nav and 'href="/purchases?new=1"' in nav and 'href="#"' not in nav
          and 'href="/products"' in nav and 'href="/sales/new"' in nav and 'href="/reports/sales"' in nav)
    check("live: títulos aprobados", all(t in html for t in ("Ventas de los últimos 7 días", "Inventario por categoría", "Movimientos recientes", "Productos con bajo stock", "Productos más vendidos")))
    check("live: selector Diario deshabilitado", 'class="ix-diario" aria-haspopup="listbox" aria-expanded="false"\n                                disabled' in html or "disabled title=\"Solo vista diaria" in html)

    app.debug = True
    ref = admin.get("/dashboard?ref=1").get_data(as_text=True)
    app.debug = False
    check("referencia carga fixture sin sucursal", "dashboard_reference_data.js" in ref and "Sucursal" not in ref and 'href="#"' in ref)
    fixture = FIXTURE.read_text(encoding="utf-8")
    check("fixture de referencia conserva sus valores", all(v in fixture for v in FIXTURE_VALUES) and "window.INICIO_REFERENCE" in fixture)

    print(f"\n{test_products_core.CHECKS_RUN} comprobaciones, {test_products_core.FAILURES} fallos")
    return 1 if test_products_core.FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
