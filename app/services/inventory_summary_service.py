"""Agregado de SOLO LECTURA de Resumen de Inventario (America/Caracas).

Orquesta reglas que ya existen: estados exclusivos de Product Core, valoración de
Categorías (SUM(current_stock * purchase_price) de activos), cierres diarios de
``inventory_snapshots`` y la traza SaleLine -> StockMovement de Sales Core. No
redefine ninguna regla ni escribe en la base de datos.
"""
from datetime import timedelta, timezone

from sqlalchemy import case, func
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Product, Sale, SaleLine, StockMovement
from app.models.sale import STATUS_COMPLETED
from app.models.stock_movement import MOVEMENT_ENTRADA, MOVEMENT_SALIDA
from app.services import product_service
from app.services import catalog_valuation_service as valuation
from app.services.dashboard_service import _alert, _comparison, _distribution, _in_stock_by_category, recent_movements
from app.services.inventory_snapshot_service import commercial_today
from app.services.product_history_service import snapshot_kpis
from app.services.sales_common import utc_boundary

CHART_DAYS = 7
ROTATION_DAYS = 30
_LIST_LIMIT = 5
_LOW_STOCK_LIMIT = 6
# Mismo orden que Product.stock_status: agotado, bajo stock, disponible.
_URGENCY = case((Product.current_stock == 0, 0), (Product.current_stock <= Product.minimum_stock, 1), else_=2)


def _commercial_day(created_at):
    return commercial_today(created_at.replace(tzinfo=timezone.utc))


def _kpi(value, previous):
    return {"available": True, "value": value, "comparison": _comparison(value, previous)}


def _movement_chart(today):
    """Entradas y salidas por día comercial; los ajustes no pertenecen a estas series."""
    days = [today - timedelta(days=CHART_DAYS - 1 - i) for i in range(CHART_DAYS)]
    rows = (
        db.session.query(StockMovement.created_at, StockMovement.movement_type, StockMovement.quantity)
        .filter(StockMovement.created_at >= utc_boundary(days[0]),
                StockMovement.movement_type.in_((MOVEMENT_ENTRADA, MOVEMENT_SALIDA)))
        .all()
    )
    totals = {kind: dict.fromkeys(days, 0) for kind in (MOVEMENT_ENTRADA, MOVEMENT_SALIDA)}
    for created_at, kind, quantity in rows:
        day = _commercial_day(created_at)
        if day in totals[kind]:
            totals[kind][day] += quantity
    return {"available": True, "days": [d.isoformat() for d in days],
            "entries": [totals[MOVEMENT_ENTRADA][d] for d in days],
            "exits": [totals[MOVEMENT_SALIDA][d] for d in days]}


def _lowest_stock():
    products = (
        Product.query.options(joinedload(Product.category))
        .filter(Product.is_active.is_(True))
        .order_by(_URGENCY, Product.current_stock.asc(), Product.name.asc())
        .limit(_LOW_STOCK_LIMIT)
        .all()
    )
    return {"available": True, "items": [{
        "id": p.id, "code": p.code, "name": p.name, "image_url": p.image_url,
        "category": p.category.name if p.category else None,
        "current_stock": p.current_stock, "minimum_stock": p.minimum_stock, "status": p.stock_status,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    } for p in products]}


def _rotation(today):
    """Unidades vendidas (SaleLine.quantity) en ventas completadas de los últimos 30 días comerciales."""
    start = today - timedelta(days=ROTATION_DAYS - 1)
    window = (Sale.status == STATUS_COMPLETED, Sale.sold_at >= utc_boundary(start))
    units = func.sum(SaleLine.quantity)
    total = (db.session.query(func.coalesce(units, 0)).join(Sale, Sale.id == SaleLine.sale_id)
             .filter(*window).scalar())
    rows = (
        db.session.query(SaleLine.product_id, units)
        .join(Sale, Sale.id == SaleLine.sale_id)
        .filter(*window)
        .group_by(SaleLine.product_id)
        .order_by(units.desc(), SaleLine.product_id.asc())
        .limit(_LIST_LIMIT)
        .all()
    )
    products = {p.id: p for p in Product.query.filter(Product.id.in_([pid for pid, _ in rows])).all()} if rows else {}
    top = int(rows[0][1]) if rows else 0
    items = []
    for pid, sold in rows:
        p = products[pid]
        items.append({"id": p.id, "code": p.code, "name": p.name, "image_url": p.image_url,
                      "current_stock": p.current_stock, "status": p.stock_status,
                      "units": int(sold), "percent": round(int(sold) * 100 / top) if top else 0})
    return {"available": True, "window_days": ROTATION_DAYS, "start": start.isoformat(), "end": today.isoformat(),
            "total_units": int(total or 0), "items": items}


def summary(now=None) -> dict:
    today = commercial_today(now)
    products = product_service.products_summary()
    rows = _in_stock_by_category()
    in_stock = sum(int(n) for *_, n in rows)
    yesterday = snapshot_kpis(today - timedelta(days=1))
    in_stock_y = low_y = out_y = None
    if yesterday is not None:
        in_stock_y = yesterday["total_products"] - yesterday["out_of_stock"]
        low_y, out_y = yesterday["low_stock"], yesterday["out_of_stock"]
    from app.utils.permissions import response_includes_cost
    kpis = {
        "products_in_stock": _kpi(in_stock, in_stock_y),
        "low_stock": _kpi(products["low_stock"], low_y),
        "out_of_stock": _kpi(products["out_of_stock"], out_y),
    }
    if response_includes_cost():
        monetary, _ = valuation.valuations()
        value, currency = valuation.scalar_valuation(monetary)
        # The stock snapshots do not record historical prices.
        kpis["inventory_value"] = {**monetary, "available": bool(monetary["totals_by_currency"]),
            "value": f"{value:.2f}" if value is not None else None, "currency": currency, "comparison": {
            "available": False, "value": None, "delta": None, "change_percent": None,
            "reason": "price_history_missing"}}
    return {
        "timezone": "America/Caracas",
        "today": today.isoformat(),
        "kpis": kpis,
        "movement_chart": _movement_chart(today),
        "inventory_distribution": _distribution(rows, in_stock),
        "stock_alerts": {"available": True, "items": [_alert(p) for p in products["inventory_alerts"]]},
        "recent_movements": recent_movements(_LIST_LIMIT),
        "lowest_stock": _lowest_stock(),
        "top_rotation": _rotation(today),
    }
