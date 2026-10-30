#!/usr/bin/env python3
"""Suite aislada del Dashboard de Ventas: SQLite temporal, sin MySQL ni ferreteria_conejo.

Cubre período comercial (defecto mes en curso, validación, recorte a hoy), KPIs solo de
ventas completadas, comparación con el período anterior sin denominador cero, serie de 12
meses con cobertura honesta, categoría por producto actual, métodos de pago reales, últimas
ventas, ventas recientes paginadas, clientes destacados y cero escrituras.

Uso: python scripts/test_sales_dashboard_core.py
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
from app.models import Category, Customer, Product, Sale, SaleLine, StockMovement  # noqa: E402
from app.models.customer import WALK_IN_NAME  # noqa: E402
from app.services.sales_common import commercial_today, utc_boundary  # noqa: E402

API = "/api/sales/dashboard"
FIXTURE_VALUES = ("486,250", "3,245", "Importadora Ferremax", "Reportes de ventas", "Órdenes de venta")


def _counts(app):
    with app.app_context():
        return {m.__tablename__: db.session.query(m).count() for m in (Product, Category, StockMovement, Sale, SaleLine, Customer)}


def _noon(day) -> datetime:
    """UTC naive a mediodía comercial de ese día (America/Caracas = UTC-4)."""
    return utc_boundary(day) + timedelta(hours=16)


def main() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="sales-dashboard-"))
    app, admin, inventario, vendedor, ids = setup_app(temp_root)
    app.logger.disabled = True
    today = commercial_today()
    start, end = today - timedelta(days=6), today
    prev_start = today - timedelta(days=13)
    q = {"start": start.isoformat(), "end": end.isoformat()}

    with app.app_context():
        db.session.get(Category, ids["construccion"]).color = "orange"
        walk_in = Customer(name=WALK_IN_NAME, is_walk_in=True)
        c1 = Customer(name="Constructora Real C.A.", tax_id="J-12345678-9")
        c2 = Customer(name="Ferretería La Esquina", tax_id="J-98765432-1")
        db.session.add_all([walk_in, c1, c2])
        db.session.commit()
        walk_in_id, c1_id, c2_id = walk_in.id, c1.id, c2.id

    check("API sin sesión -> 401", app.test_client().get(API).status_code == 401)
    check("API inventario (con sales:read) -> 200", inventario.get(API).status_code == 200)
    empty = vendedor.get(API, query_string=q).json
    check("vacío: KPIs en 0 y comparación '—' (no_activity), sin infinito",
          empty["kpis"]["revenue"]["value"] == "0.00" and empty["kpis"]["revenue"]["comparison"] == {"available": False, "value": "0.00", "change_percent": None, "reason": "no_activity"})
    check("vacío: 12 meses sin cobertura -> None, no ceros inventados",
          empty["monthly"]["available"] is False and empty["monthly"]["current"] == [None] * 12 and empty["monthly"]["previous_year_available"] is False)
    check("vacío: categorías, pagos, tablas honestas", empty["by_category"]["items"] == [] and empty["payment_methods"]["items"] == []
          and empty["recent_sales"] == {"available": True, "items": [], "total": 0, "page": 1, "per_page": 5, "pages": 0})
    default = vendedor.get(API).json["period"]
    check("período por defecto: mes en curso hasta hoy", default == {"start": today.replace(day=1).isoformat(), "end": today.isoformat()})
    check("start > end -> 400", vendedor.get(API, query_string={"start": "2026-03-10", "end": "2026-03-01"}).status_code == 400)
    check("start futuro -> 400", vendedor.get(API, query_string={"start": (today + timedelta(days=1)).isoformat()}).status_code == 400)
    check("fecha inválida -> 400", vendedor.get(API, query_string={"start": "ayer"}).status_code == 400)
    check("end futuro se recorta a hoy", vendedor.get(API, query_string={"end": "2999-01-01"}).json["period"]["end"] == today.isoformat())

    cemento = create_via_api(admin, code="CEM-50", name="Cemento", category_id=ids["construccion"], current_stock=500, sale_price="10.00").json["id"]
    cable = create_via_api(admin, code="CAB-12", name="Cable", category_id=ids["electricidad"], current_stock=500, sale_price="5.00").json["id"]

    def sell(customer_id, items, method="cash"):
        r = vendedor.post("/api/sales", json={"customer_id": customer_id, "payment_method": method, "payment_condition": "cash",
                                              "idempotency_key": str(uuid.uuid4()), "items": items})
        assert r.status_code == 201, r.json
        return r.json["id"]

    # Período actual: 3 ventas (c1 x2 = 100 + 30, c2 = 25); anterior: 1 venta de 40; fuera: 1 de 1000 hace 40 días.
    s1 = sell(c1_id, [{"product_id": cemento, "quantity": 10}], "transfer")             # 100 (construcción)
    s2 = sell(c1_id, [{"product_id": cable, "quantity": 6}], "cash")                     # 30  (electricidad)
    s3 = sell(c2_id, [{"product_id": cemento, "quantity": 1}, {"product_id": cable, "quantity": 3}], "cash")  # 10 + 15 = 25
    s_prev = sell(walk_in_id, [{"product_id": cemento, "quantity": 4}])                  # 40, período anterior
    s_old = sell(walk_in_id, [{"product_id": cemento, "quantity": 100}])                 # 1000, hace 40 días
    with app.app_context():
        db.session.get(Sale, s1).sold_at = _noon(start)
        db.session.get(Sale, s2).sold_at = _noon(end)
        db.session.get(Sale, s3).sold_at = _noon(end) - timedelta(hours=1)
        db.session.get(Sale, s_prev).sold_at = _noon(prev_start + timedelta(days=2))
        db.session.get(Sale, s_old).sold_at = _noon(today - timedelta(days=40))
        db.session.get(Customer, c2_id).created_at = _noon(start)                       # nuevo en el período
        db.session.get(Customer, c1_id).created_at = _noon(prev_start)                  # nuevo en el anterior
        db.session.get(Customer, walk_in_id).created_at = _noon(start)                  # mostrador: excluido
        db.session.commit()

    before = _counts(app)
    data = vendedor.get(API, query_string=q).json
    check("GET API no escribe", _counts(app) == before)
    check("período y comparación de igual duración", data["period"] == q and data["comparison_period"] == {"start": prev_start.isoformat(), "end": (start - timedelta(days=1)).isoformat()})

    k = data["kpis"]
    check("Ventas del período = SUM(total) completadas", k["revenue"]["value"] == "155.00", str(k["revenue"]))
    check("comparación % real vs. anterior (40 -> 155 = +287.5%)", k["revenue"]["comparison"] == {"available": True, "value": "40.00", "change_percent": 287.5, "reason": "ok"})
    check("Ventas realizadas = COUNT", k["sales_count"]["value"] == 3 and k["sales_count"]["comparison"]["change_percent"] == 200.0)
    check("Ticket promedio = 155/3 redondeado", k["average_ticket"]["value"] == "51.67" and k["average_ticket"]["comparison"]["value"] == "40.00")
    check("Clientes nuevos excluye mostrador; anterior 1 -> 0%", k["new_customers"]["value"] == 1
          and k["new_customers"]["comparison"] == {"available": True, "value": 1, "change_percent": 0.0, "reason": "ok"})

    # Denominador cero honesto: período en que antes no hubo nada.
    lone = vendedor.get(API, query_string={"start": (today - timedelta(days=40)).isoformat(), "end": (today - timedelta(days=40)).isoformat()}).json["kpis"]
    check("anterior sin ventas y actual con -> 'Nuevo' (sin porcentaje)", lone["revenue"]["comparison"] == {"available": True, "value": "0.00", "change_percent": None, "reason": "new"}
          and lone["revenue"]["value"] == "1000.00", str(lone["revenue"]))

    m = data["monthly"]
    with app.app_context():
        totals = {}
        for sale in Sale.query.all():
            d = commercial_today(sale.sold_at.replace(tzinfo=UTC))
            totals[d.replace(day=1).isoformat()] = totals.get(d.replace(day=1).isoformat(), 0) + float(sale.total)
    first = min(totals)
    check("12 meses: último = mes actual, 12 puntos, cobertura desde la primera venta real",
          len(m["months"]) == 12 and m["months"][-1] == today.replace(day=1).isoformat() and m["coverage_from"] == first)
    check("12 meses: sumas por mes comercial coinciden y meses sin cobertura son None",
          all((v is None) if mo < first else (float(v) == totals.get(mo, 0)) for mo, v in zip(m["months"], m["current"])), str(list(zip(m["months"], m["current"]))))
    check("año anterior: sin ventas reales -> no disponible (nunca fixtures ni históricos)", m["previous_year_available"] is False)

    cat = data["by_category"]
    check("categoría por producto actual, colores persistidos, top primero",
          cat["grouping"] == "current_product_category" and cat["total"] == "155.00"
          and [(i["name"], i["revenue"], i["color"], i["percent"]) for i in cat["items"]] == [("Construcción", "110.00", "orange", 71.0), ("Electricidad", "45.00", "blue", 29.0)], str(cat))

    pay = data["payment_methods"]
    check("métodos de pago reales: solo existentes, etiquetas canónicas, % sobre el total",
          [(i["method"], i["label"], i["revenue"], i["count"]) for i in pay["items"]] == [("transfer", "Transferencia", "100.00", 1), ("cash", "Efectivo", "55.00", 2)]
          and pay["items"][0]["percent"] == 64.5 and pay["total"] == "155.00", str(pay))

    latest = data["latest_sales"]["items"]
    check("últimas ventas: 5 más recientes globales (incluye fuera de período), Completada",
          [s["id"] for s in latest] == [s2, s3, s1, s_prev, s_old] and all(s["status_label"] == "Completada" for s in latest)
          and latest[0]["customer"] == "Constructora Real C.A.")

    recent = data["recent_sales"]
    check("ventas recientes del período: orden, productos = unidades (SUM quantity), método, total",
          [(s["id"], s["products"], s["payment_method_label"], s["total"]) for s in recent["items"]] == [(s2, 6, "Efectivo", "30.00"), (s3, 4, "Efectivo", "25.00"), (s1, 10, "Transferencia", "100.00")]
          and recent["total"] == 3 and recent["pages"] == 1)
    for _ in range(4):
        sell(walk_in_id, [{"product_id": cable, "quantity": 1}])
    page2 = vendedor.get(API, query_string={**q, "page": 2}).json["recent_sales"]
    check("paginación real de 5", page2["total"] == 7 and page2["pages"] == 2 and page2["page"] == 2 and len(page2["items"]) == 2)
    check("página inválida -> 400", vendedor.get(API, query_string={**q, "page": 0}).status_code == 400)

    top = data["top_customers"]["items"]
    check("clientes destacados por ingreso con conteo", [(c["name"], c["revenue"], c["sales"]) for c in top] == [("Constructora Real C.A.", "130.00", 2), ("Ferretería La Esquina", "25.00", 1)])

    # Página en vivo.
    before = _counts(app)
    html = vendedor.get("/sales", query_string=q).get_data(as_text=True)
    check("GET página no escribe", _counts(app) == before)
    check("live: sin valores del fixture ni 'Órdenes de venta'", not any(v in html for v in FIXTURE_VALUES), [v for v in FIXTURE_VALUES if v in html])
    check("live: KPIs reales con etiquetas del contrato", "Ventas del período" in html and "Ventas realizadas" in html and "Ticket promedio" in html
          and "Clientes nuevos" in html and "$ 175.00" in html and "$ 25.00" in html)  # 155 + 4 ventas de 5 (paginación)
    check("live: filtro de fechas real del servidor", f'name="start" value="{start.isoformat()}"' in html and f'name="end" value="{end.isoformat()}"' in html)
    check("live: pagos, categorías, clientes reales", "Transferencia" in html and "Ferretería La Esquina" in html and "Métodos de pago" in html and "Clientes destacados" in html)
    check("live: submenú sin Reportes, Ventas activo", "Reportes de ventas" not in html and "Nueva venta" in html and "Cotizaciones" in html
          and "Pedidos" in html and "Clientes" in html)
    check("live: rango inválido -> 400 (ApiError)", vendedor.get("/sales", query_string={"start": "2026-03-10", "end": "2026-03-01"}).status_code == 400)
    app.debug = True
    ref = vendedor.get("/sales?ref=1").get_data(as_text=True)
    app.debug = False
    check("referencia: fixture determinista sin datos reales", "486,250" in ref and "Métodos de pago" in ref and "Clientes destacados" in ref
          and "Constructora Real" not in ref and "Reportes de ventas" not in ref)
    check("referencia no escribe", _counts(app) == before)

    print(f"\n{test_products_core.CHECKS_RUN} comprobaciones, {test_products_core.FAILURES} fallos")
    return 1 if test_products_core.FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
