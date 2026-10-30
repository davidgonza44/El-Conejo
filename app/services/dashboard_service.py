"""Agregados de SOLO LECTURA para Inicio (dashboard).

Orquesta reglas que ya existen en Product Core, Inventory Core, el histórico de cierres
y Sales Core; no redefine estados de stock ni interpreta notas de entrega como ventas.
Ventas: solo ``Sale.status == completed`` por día comercial (America/Caracas) con
límites UTC reales sobre ``sold_at``. Cero ventas es un valor válido (0.00), no un
módulo ausente.

Limitación histórica: SaleLine no guarda la categoría al vender; "Productos más
vendidos" muestra la categoría ACTUAL del producto solo para presentación
(``grouping: current_product_category``). Nombre, código y dinero salen del snapshot.
"""
from datetime import timedelta, timezone
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Category, Customer, Product, Sale, SaleLine, StockMovement
from app.models.category import CATEGORY_COLORS, DEFAULT_CATEGORY_COLOR
from app.models.sale import STATUS_COMPLETED
from app.models.stock_movement import MOVEMENT_AJUSTE, MOVEMENT_ENTRADA, MOVEMENT_SALIDA
from app.services import inventory_service, product_service, product_state_history_service
from app.services.inventory_snapshot_service import commercial_today
from app.services.product_history_service import snapshot_kpis
from app.services.movement_traceability_service import movement_presentations
from app.services.sales_common import utc_boundary
from app.utils.permissions import PRODUCTS_READ, REPORTS_READ, SALES_CREATE, role_has_permission

MOVEMENT_TITLES = {
    MOVEMENT_ENTRADA: "Entrada de inventario",
    MOVEMENT_SALIDA: "Salida de inventario",
    MOVEMENT_AJUSTE: "Ajuste de inventario",
}
_RECENT_MOVEMENTS = 5
_MAX_SLICES = 6
CHART_DAYS = 7
_TOP_PRODUCTS = 5
_ZERO = Decimal("0.00")


def _money(value) -> str:
    return f"{Decimal(value or 0).quantize(_ZERO):.2f}"


def _commercial_day(sold_at):
    return commercial_today(sold_at.replace(tzinfo=timezone.utc))


def _completed(start, end):
    """Ventas completadas con ``sold_at`` dentro de [start, end] (días comerciales inclusivos)."""
    return (Sale.status == STATUS_COMPLETED, Sale.sold_at >= utc_boundary(start),
            Sale.sold_at < utc_boundary(end + timedelta(days=1)))


def _in_stock_by_category():
    """Mismo universo que "Productos en stock": activos con current_stock > 0, por categoría."""
    count = func.count(Product.id)
    return (
        db.session.query(Category.id, Category.name, Category.color, count)
        .join(Product, Product.category_id == Category.id)
        .filter(Product.is_active.is_(True), Product.current_stock > 0)
        .group_by(Category.id, Category.name, Category.color)
        .order_by(count.desc(), Category.name.asc())
        .all()
    )


def _distribution(rows, total):
    items = [
        {"id": cid, "name": name, "color": color if color in CATEGORY_COLORS else DEFAULT_CATEGORY_COLOR, "value": int(n)}
        for cid, name, color, n in rows
    ]
    if len(items) > _MAX_SLICES:
        rest = items[_MAX_SLICES - 1:]
        items = items[: _MAX_SLICES - 1] + [
            {"id": None, "name": "Otros", "color": "gray", "value": sum(i["value"] for i in rest)}
        ]
    for item in items:
        item["percent"] = round(item["value"] * 100 / total, 1) if total else 0
    return {"available": True, "center_value": total, "center_label": "Productos totales", "items": items}


def _comparison(current, previous):
    if previous is None:
        return {"available": False, "value": None, "delta": None, "change_percent": None, "reason": "snapshot_missing"}
    delta = current - previous
    return {
        "available": True,
        "value": previous,
        "delta": delta,
        "change_percent": round(delta * 100 / previous, 1) if previous else None,
        "reason": "ok",
    }


def _movement(movement):
    product = movement.product
    return {
        "id": movement.id,
        "movement_type": movement.movement_type,
        "title": MOVEMENT_TITLES.get(movement.movement_type, movement.movement_type),
        "product_name": product.name if product else None,
        "product_code": product.code if product else None,
        "reason": movement.reason,
        "delta": movement.new_stock - movement.previous_stock,
        "created_at": movement.created_at.isoformat() if movement.created_at else None,
    }


def recent_movements(limit=_RECENT_MOVEMENTS) -> dict:
    """Últimos movimientos; una salida se muestra como venta solo si un SaleLine la enlaza."""
    movements = inventory_service.list_movements(limit=limit)
    presentations = movement_presentations(movements)
    lines = []
    if movements:
        lines = (SaleLine.query.options(joinedload(SaleLine.sale).joinedload(Sale.customer))
                 .filter(SaleLine.stock_movement_id.in_([m.id for m in movements])).all())
    sales = {line.stock_movement_id: line.sale for line in lines}
    items = []
    for movement in movements:
        item = _movement(movement)
        item.update(presentations[movement.id])
        item["reference"] = item["display_reference"]
        sale = sales.get(movement.id)
        item["sale_number"] = sale.sale_number if sale else None
        item["customer_name"] = sale.customer.name if sale and sale.customer else None
        if sale:
            item["title"] = f"Venta #{sale.sale_number}"
        items.append(item)
    return {"available": True, "items": items}


# --- Sales Core ----------------------------------------------------------------------

def _sales_comparison(current, previous, fmt) -> dict:
    """Sin denominador no hay porcentaje: ``new`` si ayer fue 0 y hoy hay actividad; si no, ``no_activity``."""
    delta = current - previous
    if previous > 0:
        percent, reason = round(float(Decimal(delta) * 100 / Decimal(previous)), 1), "ok"
    else:
        percent, reason = None, "new" if current > 0 else "no_activity"
    return {"available": True, "value": fmt(previous), "delta": fmt(delta), "change_percent": percent, "reason": reason}


def _daily_sales(today, yesterday) -> dict:
    """Una consulta para hoy y ayer: total vendido y clientes atendidos por día comercial.

    Clientes atendidos = clientes identificados distintos + un ticket por cada venta a
    "Público en general" (``is_walk_in``), que representa compradores anónimos.
    """
    rows = (db.session.query(Sale.sold_at, Sale.total, Sale.customer_id, Customer.is_walk_in)
            .join(Customer, Customer.id == Sale.customer_id).filter(*_completed(yesterday, today)).all())
    totals = {today: _ZERO, yesterday: _ZERO}
    identified = {today: set(), yesterday: set()}
    walk_ins = {today: 0, yesterday: 0}
    for sold_at, total, customer_id, is_walk_in in rows:
        day = _commercial_day(sold_at)
        if day not in totals:
            continue
        totals[day] += Decimal(total)
        if is_walk_in:
            walk_ins[day] += 1
        else:
            identified[day].add(customer_id)
    served = {d: len(identified[d]) + walk_ins[d] for d in totals}
    return {
        "sales_today": {"available": True, "value": _money(totals[today]),
                        "comparison": _sales_comparison(totals[today], totals[yesterday], _money)},
        "customers_served": {"available": True, "value": served[today],
                             "comparison": _sales_comparison(served[today], served[yesterday], int)},
    }


def _sales_chart(today) -> dict:
    """SUM(total) por día comercial: últimos 7 días (hoy incluido) y los 7 inmediatamente anteriores."""
    days = [today - timedelta(days=2 * CHART_DAYS - 1 - i) for i in range(2 * CHART_DAYS)]
    rows = db.session.query(Sale.sold_at, Sale.total).filter(*_completed(days[0], today)).all()
    buckets = dict.fromkeys(days, _ZERO)
    for sold_at, total in rows:
        day = _commercial_day(sold_at)
        if day in buckets:
            buckets[day] += Decimal(total)
    previous, current = days[:CHART_DAYS], days[CHART_DAYS:]
    return {"available": True, "days": [d.isoformat() for d in current], "current": [_money(buckets[d]) for d in current],
            "previous_days": [d.isoformat() for d in previous], "previous": [_money(buckets[d]) for d in previous]}


def _top_products(today) -> dict:
    """Mes comercial en curso: SUM(quantity), SUM(line_total) por producto (ventas completadas).

    Una consulta agregada + una de productos para imagen/categoría actuales (sin N+1).
    ponytail: MAX(product_name) del snapshot; si un producto se renombró dentro del mes
    gana el nombre alfabéticamente mayor. Techo aceptado: presentación, no dinero.
    """
    start = today.replace(day=1)
    units, revenue = func.sum(SaleLine.quantity), func.sum(SaleLine.line_total)
    rows = (
        db.session.query(SaleLine.product_id, func.max(SaleLine.product_code), func.max(SaleLine.product_name), units, revenue)
        .join(Sale, Sale.id == SaleLine.sale_id)
        .filter(*_completed(start, today))
        .group_by(SaleLine.product_id)
        .order_by(units.desc(), revenue.desc(), SaleLine.product_id.asc())
        .limit(_TOP_PRODUCTS)
        .all()
    )
    products = {}
    if rows:
        products = {p.id: p for p in Product.query.options(joinedload(Product.category))
                    .filter(Product.id.in_([pid for pid, *_ in rows])).all()}
    items = []
    for pid, code, name, sold, amount in rows:
        product = products.get(pid)
        items.append({"product_id": pid, "code": code, "name": name,
                      "category": product.category.name if product and product.category else None,
                      "image_url": product.image_url if product else None,
                      "units": int(sold), "revenue": _money(amount)})
    return {"available": True, "period": {"start": start.isoformat(), "end": today.isoformat()},
            "grouping": "current_product_category", "items": items}


def _alert(product):
    return {
        "id": product["id"],
        "name": product["name"],
        "image_url": product["image_url"],
        "current_stock": product["current_stock"],
        "minimum_stock": product["minimum_stock"],
        "status": product["stock_status"],
    }


def _yesterday_inventory_reconstruction(today):
    """(en_stock_ayer, alertas_ayer) o (None, None). Solo lectura, sin escrituras.

    Cierre de ayer = estado inmediatamente anterior a C = utc_boundary(today).
    Límites exclusivos: universo created_at < C, historial changed_at < C,
    movimientos posteriores created_at >= C. stock_ayer = actual - SUM(delta).
    Sin cobertura para cualquier producto necesario, o cadena incoherente
    (stock_ayer < 0), no se afirma nada: (None, None).
    """
    cutoff = utc_boundary(today)
    existed = db.session.query(Product.id, Product.current_stock).filter(Product.created_at < cutoff).all()
    if not existed:
        return 0, 0
    current = {pid: stock for pid, stock in existed}
    pids = sorted(current)
    net = dict(db.session.query(StockMovement.product_id,
                                func.sum(StockMovement.new_stock - StockMovement.previous_stock))
               .filter(StockMovement.product_id.in_(pids), StockMovement.created_at >= cutoff)
               .group_by(StockMovement.product_id).all())
    states = product_state_history_service.states_before(pids, cutoff)
    if any(states[pid] is None for pid in pids):
        return None, None
    in_stock_yesterday = alerts_yesterday = 0
    for pid in pids:
        state = states[pid]
        if not state["is_active"]:
            continue
        past = current[pid] - int(net.get(pid, 0) or 0)
        if past < 0:
            return None, None
        if past > 0:
            in_stock_yesterday += 1
            if past <= state["minimum_stock"]:
                alerts_yesterday += 1
        else:
            alerts_yesterday += 1
    return in_stock_yesterday, alerts_yesterday


def quick_actions(role) -> list[dict]:
    """Cuatro acciones aprobadas, con rutas reales y permisos dinámicos."""
    return [
        {"id": "new_product", "label": "Nuevo producto", "description": "Agregar un nuevo producto al inventario",
         "href": "/products", "enabled": role_has_permission(role, PRODUCTS_READ)},
        {"id": "new_sale", "label": "Registrar venta", "description": "Crear una nueva venta",
         "href": "/sales/new", "enabled": role_has_permission(role, SALES_CREATE)},
        {"id": "new_purchase", "label": "Registrar compra", "description": "Crear una nueva orden de compra",
         "href": "/purchases?new=1", "enabled": role_has_permission(role, "purchases:write")},
        {"id": "reports", "label": "Reportes y análisis", "description": "Gráficas, ventas, inventario y pronósticos",
         "href": "/reportes", "enabled": role_has_permission(role, REPORTS_READ)},
    ]


def pending_proposals(role) -> list[dict]:
    """Accesos al listado existente; sin permisos o esquema no se inventan conteos."""
    if role != "admin":
        return []
    from app.models import Category, Product
    from app.models.purchase import Supplier
    from app.services import category_service
    from app.utils.schema import table_ready

    items = []
    for label, model, path, permission in (
        ("Productos pendientes", Product, "/products", "products:approve"),
        ("Proveedores pendientes", Supplier, "/suppliers", "suppliers:approve"),
        ("Categorías pendientes", Category, "/categories", "categories:approve"),
    ):
        read_permission = "suppliers:read" if model is Supplier else PRODUCTS_READ
        if not role_has_permission(role, permission) or not role_has_permission(role, read_permission):
            continue
        ready = table_ready(model.__tablename__) and "approval_status" in {
            column["name"] for column in db.inspect(db.engine).get_columns(model.__tablename__)}
        if model is Product:
            ready = ready and table_ready("product_changes")
        if model is Category:
            ready = ready and table_ready("category_changes")
        count = None
        if ready:
            query = (product_service.filtered_products_query(include_inactive=True, approval_status="pending")
                     if model is Product else model.query.filter_by(approval_status="pending"))
            if model is Category:
                query = category_service.approval_filter(model.query, "pending")
            count = query.order_by(None).count()
        items.append({"label": label, "count": count, "href": path + "?approval_status=pending"})
    return items


def summary(role, now=None) -> dict:
    today = commercial_today(now)
    rows = _in_stock_by_category()
    in_stock = sum(int(n) for *_, n in rows)
    products = product_service.products_summary()
    alerts = products["low_stock"] + products["out_of_stock"]

    yesterday = snapshot_kpis(today - timedelta(days=1))
    in_stock_yesterday = alerts_yesterday = None
    if yesterday is not None:
        # Cierre de ayer con las mismas reglas exclusivas: activos - agotados; bajo + agotado.
        in_stock_yesterday = yesterday["total_products"] - yesterday["out_of_stock"]
        alerts_yesterday = yesterday["low_stock"] + yesterday["out_of_stock"]
    else:
        # Sin snapshot: reconstrucción exacta con movimientos + historial;
        # sin cobertura necesaria no se afirma nada (comparación no disponible).
        in_stock_yesterday, alerts_yesterday = _yesterday_inventory_reconstruction(today)

    sales = _daily_sales(today, today - timedelta(days=1))
    view = {
        "timezone": "America/Caracas",
        "today": today.isoformat(),
        "kpis": {
            "sales_today": sales["sales_today"],
            "products_in_stock": {"available": True, "value": in_stock, "comparison": _comparison(in_stock, in_stock_yesterday)},
            "customers_served": sales["customers_served"],
            "active_alerts": {"available": True, "value": alerts, "comparison": _comparison(alerts, alerts_yesterday)},
        },
        "sales_chart": _sales_chart(today),
        "inventory_distribution": _distribution(rows, in_stock),
        "recent_movements": recent_movements(),
        "stock_alerts": {"available": True, "items": [_alert(p) for p in products["inventory_alerts"]]},
        "top_products": _top_products(today),
        "quick_actions": quick_actions(role),
    }
    from app.utils.permissions import response_includes_sale_price
    if not response_includes_sale_price():
        view["kpis"]["sales_today"] = {"available": False, "value": None, "comparison": {"available": False}}
        view["sales_chart"] = {"available": False, "days": [], "current": [], "previous_days": [], "previous": []}
        view["top_products"] = {"available": False, "items": []}
    return view
