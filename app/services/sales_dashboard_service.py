"""Agregado de SOLO LECTURA del Dashboard de Ventas (America/Caracas).

Solo ventas ``completed`` reales (Sales Core); nunca cotizaciones, pedidos, notas de
entrega ni importaciones históricas. El período por defecto es el mes en curso hasta hoy
y la comparación es el período inmediatamente anterior de igual duración. Dinero en
``Decimal`` serializado como texto con dos decimales.

Limitación histórica: SaleLine no guarda la categoría del producto al vender; "Ventas
por categoría" agrupa por la categoría ACTUAL del producto (``grouping`` lo declara).
"""
from datetime import date, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import Category, Customer, Product, Sale, SaleLine
from app.models.category import CATEGORY_COLORS, DEFAULT_CATEGORY_COLOR
from app.models.sale import PAYMENT_METHODS, STATUS_COMPLETED, STATUS_LABELS
from app.services.exceptions import ValidationError
from app.services.product_history_service import previous_range
from app.services.sales_common import commercial_today, parse_date, positive_int, quantize, restrict_own_sales, utc_boundary

MONTHS = 12
RECENT_PER_PAGE = 5
_TOP = 5
_MAX_SLICES = 6
_ZERO = Decimal("0.00")


def _money(value) -> str:
    return f"{quantize(Decimal(value or 0)):.2f}"


def _day(sold_at) -> date:
    return commercial_today(sold_at.replace(tzinfo=timezone.utc))


def _month_start(day: date, back: int) -> date:
    index = day.year * 12 + day.month - 1 - back
    return date(index // 12, index % 12 + 1, 1)


def period(params: dict, today: date) -> tuple[date, date]:
    """Rango comercial inclusivo; por defecto el mes en curso hasta hoy. Fin recortado a hoy."""
    start = parse_date(params["start"], "start") if params.get("start") else today.replace(day=1)
    end = parse_date(params["end"], "end") if params.get("end") else today
    if start > end:
        raise ValidationError("La fecha inicial no puede superar la final.")
    if start > today:
        raise ValidationError("El período no puede comenzar en el futuro.")
    return start, min(end, today)


def _completed(start: date, end: date):
    return restrict_own_sales(Sale.query.filter(
        Sale.status == STATUS_COMPLETED, Sale.sold_at >= utc_boundary(start),
        Sale.sold_at < utc_boundary(end + timedelta(days=1)),
    ), Sale.seller_id)


def _totals(start: date, end: date) -> dict:
    revenue, count = _completed(start, end).with_entities(func.coalesce(func.sum(Sale.total), 0), func.count(Sale.id)).one()
    # Clientes nuevos cuenta Customer creados en el período. No hay dueño vendedor del cliente,
    # así que este KPI no representa ventas propias y no se recorta por seller_id.
    new_customers = (
        db.session.query(func.count(Customer.id))
        .filter(Customer.is_walk_in.is_(False), Customer.created_at >= utc_boundary(start),
                Customer.created_at < utc_boundary(end + timedelta(days=1)))
        .scalar()
    )
    revenue = quantize(Decimal(revenue or 0))
    return {"revenue": revenue, "count": int(count or 0),
            "average": quantize(revenue / count) if count else _ZERO, "new_customers": int(new_customers or 0)}


def _comparison(current, previous, fmt) -> dict:
    """Sin denominador no hay porcentaje: ``new`` si antes era 0 y ahora hay actividad; si no, ``—``."""
    if previous > 0:
        percent = round(float((Decimal(current) - Decimal(previous)) * 100 / Decimal(previous)), 1)
        return {"available": True, "value": fmt(previous), "change_percent": percent, "reason": "ok"}
    return {"available": current > 0, "value": fmt(previous), "change_percent": None,
            "reason": "new" if current > 0 else "no_activity"}


def _kpis(current: dict, previous: dict) -> dict:
    return {
        "revenue": {"value": _money(current["revenue"]), "comparison": _comparison(current["revenue"], previous["revenue"], _money)},
        "sales_count": {"value": current["count"], "comparison": _comparison(current["count"], previous["count"], int)},
        "average_ticket": {"value": _money(current["average"]), "comparison": _comparison(current["average"], previous["average"], _money)},
        "new_customers": {"value": current["new_customers"],
                          "comparison": _comparison(current["new_customers"], previous["new_customers"], int)},
    }


def _monthly(today: date) -> dict:
    """SUM(total) por mes comercial de los últimos 12 meses y del año anterior; meses sin
    cobertura (antes de la primera venta) van como ``None``, nunca como 0 inventado."""
    months = [_month_start(today, MONTHS - 1 - i) for i in range(MONTHS)]
    prior = [_month_start(m, MONTHS) for m in months]
    earliest = restrict_own_sales(
        db.session.query(func.min(Sale.sold_at)).filter(Sale.status == STATUS_COMPLETED), Sale.seller_id,
    ).scalar()
    coverage_from = _day(earliest).replace(day=1) if earliest else None
    buckets = {}
    if coverage_from is not None:
        rows = restrict_own_sales(
            db.session.query(Sale.sold_at, Sale.total).filter(
                Sale.status == STATUS_COMPLETED, Sale.sold_at >= utc_boundary(prior[0])),
            Sale.seller_id,
        ).all()
        for sold_at, total in rows:
            day = _day(sold_at)
            buckets[(day.year, day.month)] = buckets.get((day.year, day.month), _ZERO) + Decimal(total)

    def series(items):
        return [_money(buckets.get((m.year, m.month), 0)) if coverage_from and m >= coverage_from else None for m in items]

    previous_year = series(prior)
    return {"available": coverage_from is not None,
            "coverage_from": coverage_from.isoformat() if coverage_from else None,
            "months": [m.isoformat() for m in months], "current": series(months),
            "previous_year": previous_year, "previous_year_available": any(v is not None for v in previous_year)}


def _by_category(start: date, end: date) -> dict:
    amount = func.sum(SaleLine.line_total)
    rows = restrict_own_sales(
        db.session.query(Category.id, Category.name, Category.color, amount)
        .join(Product, Product.category_id == Category.id)
        .join(SaleLine, SaleLine.product_id == Product.id)
        .join(Sale, Sale.id == SaleLine.sale_id)
        .filter(Sale.status == STATUS_COMPLETED, Sale.sold_at >= utc_boundary(start),
                Sale.sold_at < utc_boundary(end + timedelta(days=1))),
        Sale.seller_id,
    ).group_by(Category.id, Category.name, Category.color).order_by(amount.desc(), Category.name.asc()).all()
    items = [{"id": cid, "name": name, "color": color if color in CATEGORY_COLORS else DEFAULT_CATEGORY_COLOR,
              "revenue": quantize(Decimal(value or 0))} for cid, name, color, value in rows]
    if len(items) > _MAX_SLICES:
        rest = items[_MAX_SLICES - 1:]
        items = items[:_MAX_SLICES - 1] + [{"id": None, "name": "Otros", "color": "gray",
                                           "revenue": sum((i["revenue"] for i in rest), _ZERO)}]
    total = sum((i["revenue"] for i in items), _ZERO)
    for item in items:
        item["percent"] = round(float(item["revenue"] * 100 / total), 1) if total else 0
        item["revenue"] = _money(item["revenue"])
    return {"available": True, "grouping": "current_product_category", "total": _money(total), "items": items}


def _payment_methods(start: date, end: date) -> dict:
    amount = func.sum(Sale.total)
    rows = (_completed(start, end).with_entities(Sale.payment_method, amount, func.count(Sale.id))
            .group_by(Sale.payment_method).order_by(amount.desc(), Sale.payment_method.asc()).all())
    total = sum((Decimal(value or 0) for _, value, _ in rows), _ZERO)
    return {"available": True, "total": _money(total), "items": [{
        "method": method, "label": PAYMENT_METHODS.get(method, method), "revenue": _money(value), "count": int(count),
        "percent": round(float(Decimal(value or 0) * 100 / total), 1) if total else 0,
    } for method, value, count in rows]}


def _sale_row(sale: Sale) -> dict:
    return {
        "id": sale.id, "sale_number": sale.sale_number, "sold_at": sale.sold_at.isoformat() if sale.sold_at else None,
        "customer_id": sale.customer_id, "customer": sale.customer.name if sale.customer else None,
        "products": sum(line.quantity for line in sale.lines), "payment_method": sale.payment_method,
        "payment_method_label": PAYMENT_METHODS.get(sale.payment_method, sale.payment_method),
        "status": sale.status, "status_label": STATUS_LABELS.get(sale.status, sale.status), "total": _money(sale.total),
    }


def _with_rows(query):
    return query.options(joinedload(Sale.customer), selectinload(Sale.lines)).order_by(Sale.sold_at.desc(), Sale.id.desc())


def _latest() -> dict:
    """Últimas ventas registradas (sin filtro de período): actividad más reciente real."""
    sales = _with_rows(restrict_own_sales(Sale.query.filter(Sale.status == STATUS_COMPLETED), Sale.seller_id)).limit(_TOP).all()
    return {"available": True, "items": [_sale_row(s) for s in sales]}


def _recent(start: date, end: date, page: int) -> dict:
    """Ventas del período, más recientes primero; ``products`` = unidades vendidas (SUM(quantity))."""
    query = _completed(start, end)
    total = query.order_by(None).count()
    pages = (total + RECENT_PER_PAGE - 1) // RECENT_PER_PAGE
    sales = _with_rows(query).offset((page - 1) * RECENT_PER_PAGE).limit(RECENT_PER_PAGE).all()
    return {"available": True, "items": [_sale_row(s) for s in sales], "total": total,
            "page": page, "per_page": RECENT_PER_PAGE, "pages": pages}


def _top_customers(start: date, end: date) -> dict:
    amount = func.sum(Sale.total)
    rows = (
        _completed(start, end).join(Customer, Customer.id == Sale.customer_id)
        .with_entities(Customer.id, Customer.name, Customer.is_walk_in, amount, func.count(Sale.id))
        .group_by(Customer.id, Customer.name, Customer.is_walk_in)
        .order_by(amount.desc(), Customer.name.asc())
        .limit(_TOP)
        .all()
    )
    return {"available": True, "items": [{"id": cid, "name": name, "is_walk_in": bool(walk_in),
                                          "revenue": _money(value), "sales": int(count)}
                                         for cid, name, walk_in, value, count in rows]}


def dashboard(params: dict, now=None) -> dict:
    today = commercial_today(now)
    start, end = period(params, today)
    previous_start, previous_end = previous_range(start, end)
    page = positive_int(params.get("page", 1), "page")
    return {
        "timezone": "America/Caracas",
        "today": today.isoformat(),
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "comparison_period": {"start": previous_start.isoformat(), "end": previous_end.isoformat()},
        "kpis": _kpis(_totals(start, end), _totals(previous_start, previous_end)),
        "monthly": _monthly(today),
        "by_category": _by_category(start, end),
        "payment_methods": _payment_methods(start, end),
        "latest_sales": _latest(),
        "recent_sales": _recent(start, end, page),
        "top_customers": _top_customers(start, end),
    }
