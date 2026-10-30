#!/usr/bin/env python3
"""Suite aislada de Resumen de Inventario: SQLite temporal, sin MySQL ni ferreteria_conejo.

Cubre KPI reales (reglas de Product Core y valoración de Categorías), comparación con el
cierre de ayer (y su ausencia honesta), agrupación de 7 días, colores de categoría, alertas,
movimientos recientes con traza de venta, orden por urgencia de stock, rotación de 30 días
con ventas completadas y cero escrituras del GET y de la página.

Uso: python scripts/test_inventory_summary_core.py
"""
from __future__ import annotations

import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from test_products_core import check, create_via_api, setup_app  # noqa: E402
import test_products_core  # noqa: E402

from app.extensions import db  # noqa: E402
from app.models import Category, Customer, InventorySnapshot, Product, Sale, SaleLine, StockMovement  # noqa: E402
from app.models.customer import WALK_IN_NAME  # noqa: E402
from app.services.inventory_snapshot_service import commercial_today  # noqa: E402

API = "/api/inventory/summary-dashboard"
FIXTURE_VALUES = ("3,245", "524,830", "Importadora Ferremax", "Sucursal Norte", "1,284")


def _counts(app):
    with app.app_context():
        return {m.__tablename__: db.session.query(m).count()
                for m in (Product, Category, StockMovement, InventorySnapshot, Sale, SaleLine, Customer)}


def main() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="inventory-summary-"))
    app, admin, inventario, vendedor, ids = setup_app(temp_root)
    app.logger.disabled = True
    cat_a, cat_b = ids["construccion"], ids["electricidad"]
    with app.app_context():
        db.session.get(Category, cat_a).color = "orange"
        db.session.get(Category, cat_b).color = "teal"
        customer = Customer(name="Constructora Real C.A.", tax_id="J-12345678-9")
        db.session.add_all([Customer(name=WALK_IN_NAME, is_walk_in=True), customer])
        db.session.commit()
        customer_id = customer.id

    check("API sin sesión -> 401", app.test_client().get(API).status_code == 401)
    check("API vendedor (sin inventory:read) -> 403", vendedor.get(API).status_code == 403)
    empty = admin.get(API).json
    check("vacío: KPIs en 0 y valor 0.00", empty["kpis"]["products_in_stock"]["value"] == 0
          and empty["kpis"]["inventory_value"]["value"] == "0.00")
    check("vacío: rotación honesta sin filas", empty["top_rotation"]["items"] == [] and empty["top_rotation"]["total_units"] == 0)
    check("vacío: 7 días con ceros reales", len(empty["movement_chart"]["days"]) == 7 and empty["movement_chart"]["entries"] == [0] * 7)

    # Semilla: estados exclusivos con precios de compra conocidos.
    ok = create_via_api(admin, code="OK-1", name="Disponible", category_id=cat_a, current_stock=30, minimum_stock=10,
                        purchase_price="2.50", sale_price="4.00")
    low = create_via_api(admin, code="LOW-1", name="Bajo", category_id=cat_a, current_stock=5, minimum_stock=10,
                         purchase_price="10.00", sale_price="15.00")
    out = create_via_api(admin, code="OUT-1", name="Agotado", category_id=cat_b, current_stock=0, minimum_stock=5,
                         purchase_price="99.00", sale_price="120.00")
    ok_b = create_via_api(admin, code="OK-2", name="Cable", category_id=cat_b, current_stock=7, minimum_stock=2,
                          purchase_price="1.00", sale_price="3.00")
    inactive = create_via_api(admin, code="INA-1", name="Inactivo con stock", category_id=cat_b, current_stock=9,
                              purchase_price="1000.00")
    admin.delete(f"/api/products/{inactive.json['id']}")
    check("semilla", all(r.status_code == 201 for r in (ok, low, out, ok_b, inactive)))
    ok_id, low_id, out_id, ok_b_id = (r.json["id"] for r in (ok, low, out, ok_b))

    # Movimientos: ajuste (no entra al gráfico), salida manual y una venta real (traza SaleLine).
    admin.post("/api/inventory/adjustment", json={"product_id": ok_b_id, "new_stock": 6, "reason": "Conteo físico"})
    admin.post("/api/inventory/exit", json={"product_id": ok_id, "quantity": 3, "reason": "Salida manual"})
    sale = admin.post("/api/sales", json={"customer_id": customer_id, "payment_method": "cash", "payment_condition": "cash",
                                          "idempotency_key": str(uuid.uuid4()),
                                          "items": [{"product_id": ok_id, "quantity": 4}, {"product_id": low_id, "quantity": 1}]})
    check("venta real registrada", sale.status_code == 201, str(sale.json))

    before = _counts(app)
    data = admin.get(API).json
    check("GET API no escribe", _counts(app) == before, str(before))
    check("zona horaria comercial", data["timezone"] == "America/Caracas" and data["today"] == commercial_today().isoformat())

    k = data["kpis"]
    # activos con stock > 0: OK-1 (23), LOW-1 (4), OK-2 (6); agotado e inactivo excluidos
    check("Productos en stock = activos con stock > 0", k["products_in_stock"]["value"] == 3, str(k["products_in_stock"]))
    check("Bajo stock exclusivo (LOW-1)", k["low_stock"]["value"] == 1)
    check("Sin stock (OUT-1)", k["out_of_stock"]["value"] == 1)
    # valor = SUM(stock * purchase_price) de activos: 23*2.5 + 4*10 + 0*99 + 6*1 = 103.50 (inactivo excluido)
    check("Valor del inventario = convención de Categorías", k["inventory_value"]["value"] == "103.50", k["inventory_value"]["value"])
    check("sin cierre de ayer -> comparación no disponible, sin porcentaje inventado",
          all(k[key]["comparison"] == {"available": False, "value": None, "delta": None, "change_percent": None, "reason": "snapshot_missing"}
              for key in ("products_in_stock", "low_stock", "out_of_stock")))
    check("valor: sin histórico de precios -> nunca compara", k["inventory_value"]["comparison"]["available"] is False
          and k["inventory_value"]["comparison"]["reason"] == "price_history_missing")

    chart = data["movement_chart"]
    today = commercial_today()
    check("7 días calendario terminando hoy", chart["days"][-1] == today.isoformat()
          and chart["days"][0] == (today - timedelta(days=6)).isoformat())
    # entradas hoy: 30+5+7+9 (stock inicial) = 51 (el inactivo también entró); salidas: 3 + 4 + 1 = 8; el ajuste no cuenta
    check("entradas de hoy agrupadas", chart["entries"][-1] == 51 and chart["entries"][:-1] == [0] * 6, str(chart["entries"]))
    check("salidas de hoy (manual + venta), sin ajustes", chart["exits"][-1] == 8 and chart["exits"][:-1] == [0] * 6, str(chart["exits"]))

    dist = data["inventory_distribution"]
    by_name = {i["name"]: i for i in dist["items"]}
    check("distribución mismo universo que en stock", dist["center_value"] == 3 == sum(i["value"] for i in dist["items"]))
    check("colores persistidos de Category", by_name["Construcción"]["color"] == "orange" and by_name["Electricidad"]["color"] == "teal")

    alerts = data["stock_alerts"]["items"]
    check("alertas exclusivas: agotado primero, luego bajo", [a["status"] for a in alerts] == ["agotado", "bajo_stock"]
          and all("image_url" in a for a in alerts))

    moves = data["recent_movements"]["items"]
    with app.app_context():
        expected = [m.id for m in StockMovement.query.order_by(StockMovement.created_at.desc(), StockMovement.id.desc()).limit(5)]
        sale_number = Sale.query.one().sale_number
    check("movimientos: 5 más recientes primero", [m["id"] for m in moves] == expected)
    sale_rows = [m for m in moves if m["sale_number"]]
    check("salidas de venta trazadas por SaleLine.stock_movement_id",
          len(sale_rows) == 2 and all(m["title"] == f"Venta #{sale_number}" and m["customer_name"] == "Constructora Real C.A."
                                      and m["reference"] == sale_number for m in sale_rows))
    manual = next(m for m in moves if m["reason"] == "Salida manual")
    check("salida manual sin cliente inventado", manual["sale_number"] is None and manual["customer_name"] is None
          and manual["title"] == "Salida de inventario" and manual["reference"] == f"MOV-{manual['id']:06d}")
    check("movimiento: sin proveedor/sucursal", not ({"supplier", "branch"} & set(moves[0])))

    lowest = data["lowest_stock"]["items"]
    check("menor stock: agotado, bajo, luego disponibles por stock", [p["code"] for p in lowest] == ["OUT-1", "LOW-1", "OK-2", "OK-1"]
          and lowest[0]["status"] == "agotado" and lowest[1]["status"] == "bajo_stock" and lowest[0]["category"] == "Electricidad")
    check("menor stock: inactivos excluidos y campos completos", all(p["code"] != "INA-1" for p in lowest)
          and {"minimum_stock", "updated_at", "image_url"} <= set(lowest[0]))

    rotation = data["top_rotation"]
    check("rotación 30d: solo SaleLine de ventas completadas (no salidas manuales)",
          rotation["window_days"] == 30 and [(p["code"], p["units"]) for p in rotation["items"]] == [("OK-1", 4), ("LOW-1", 1)]
          and rotation["total_units"] == 5, str(rotation))
    check("rotación: barra relativa al máximo y stock real", rotation["items"][0]["percent"] == 100 and rotation["items"][1]["percent"] == 25
          and rotation["items"][0]["current_stock"] == 23)

    # Cotizaciones/pedidos y ventas viejas no cuentan en la rotación.
    with app.app_context():
        s = Sale.query.one()
        s.sold_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=45)
        db.session.commit()
    check("venta fuera de la ventana de 30 días excluida", admin.get(API).json["top_rotation"]["items"] == [])
    with app.app_context():
        Sale.query.one().sold_at = datetime.now(UTC).replace(tzinfo=None)
        db.session.commit()

    # Cierre de ayer real -> comparación real.
    yesterday = today - timedelta(days=1)
    with app.app_context():
        for pid, stock, minimum, active in ((ok_id, 30, 10, True), (low_id, 12, 10, True), (out_id, 0, 5, True),
                                            (ok_b_id, 0, 2, True), (inactive.json["id"], 9, 0, False)):
            db.session.add(InventorySnapshot(snapshot_date=yesterday, product_id=pid, stock=stock, minimum_stock=minimum,
                                             product_is_active=active, category_id=cat_a, category_is_active=True))
        db.session.commit()
    k = admin.get(API).json["kpis"]
    # ayer: activos 4, agotados 2 -> en stock 2; hoy 3 -> +1 (50%). bajo ayer 0 -> hoy 1. agotado ayer 2 -> hoy 1.
    check("en stock vs. cierre de ayer", k["products_in_stock"]["comparison"] == {"available": True, "value": 2, "delta": 1, "change_percent": 50.0, "reason": "ok"}, str(k["products_in_stock"]))
    check("bajo stock vs. ayer (denominador 0 -> sin %)", k["low_stock"]["comparison"]["value"] == 0 and k["low_stock"]["comparison"]["delta"] == 1
          and k["low_stock"]["comparison"]["change_percent"] is None)
    check("sin stock vs. ayer", k["out_of_stock"]["comparison"] == {"available": True, "value": 2, "delta": -1, "change_percent": -50.0, "reason": "ok"})

    # Página en vivo: sin fixture, sin sucursal, con rotación; referencia intacta.
    before = _counts(app)
    html = admin.get("/inventory/summary").get_data(as_text=True)
    check("GET página no escribe", _counts(app) == before)
    check("live: sin valores del fixture", not any(v in html for v in FIXTURE_VALUES), [v for v in FIXTURE_VALUES if v in html])
    check("live: Resumen por sucursal eliminado, rotación presente", "Resumen por sucursal" not in html and "Sucursal Norte" not in html
          and "Productos con mayor rotación" in html and "Total unidades movidas" in html and "5 uds." in html)
    check("live: KPI reales y sin comparación honesta", "$ 103.50" in html and "Sin comparación disponible" in html and "vs. ayer 2" in html)
    check("live: venta trazada y cliente real", f"Venta #{sale_number}" in html and "Cliente: Constructora Real C.A." in html)
    check("live: enlaces reales", 'href="/products?status=bajo_stock"' in html and 'href="/inventory"' in html)
    check("live: Resumen activo y predictivo completo",
          'aria-current="page">Resumen</a>' in html and "Pronóstico de demanda" in html and "Precisión del modelo" in html
          and "Sucursal" not in html)
    denied_summary = vendedor.get("/inventory/summary")
    check("vendedor sin inventory:read -> 403", denied_summary.status_code == 403
          and "No tiene permisos para acceder a esta sección." in denied_summary.get_data(as_text=True))

    app.debug = True
    ref = admin.get("/inventory/summary?ref=1").get_data(as_text=True)
    app.debug = False
    check("referencia: fixture determinista sin datos reales", all(v in ref for v in ("3,245", "524,830", "1,284")) and "Constructora Real" not in ref
          and "Resumen por sucursal" not in ref and "Productos con mayor rotación" in ref and "Sucursal" not in ref)
    check("referencia no escribe", _counts(app) == before)

    print(f"\n{test_products_core.CHECKS_RUN} comprobaciones, {test_products_core.FAILURES} fallos")
    return 1 if test_products_core.FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
