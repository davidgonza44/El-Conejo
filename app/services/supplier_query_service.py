"""Shared SQL aggregates, filtering and ordering for pages and complete exports."""
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, or_, case, inspect

from app.extensions import db
from app.models.purchase import COMPLETED_STATUSES, Supplier, PurchaseOrder
from app.models.supplier_change import SupplierChange
from app.utils.schema import table_ready
from app.services.supplier_service import FIELDS, get_supplier
from app.services.purchase_service import day, integer, money
from app.services.inventory_service import COMMERCIAL_ZONE, _utc_boundary
from app.services.exceptions import ValidationError
from app.utils.permissions import response_includes_cost

SORTS = {"name_asc": "Nombre A-Z", "name_desc": "Nombre Z-A", "total_desc": "Mayor importe (moneda seleccionada)",
         "total_asc": "Menor importe (moneda seleccionada)", "last_desc": "Compra más reciente", "last_asc": "Compra más antigua"}


def available():
    inspector = inspect(db.engine)
    if not inspector.has_table("suppliers") or not inspector.has_table("purchase_orders"):
        return False
    return ({*FIELDS, "is_preferred"} <= {c["name"] for c in inspector.get_columns("suppliers")}
            and "supplier_snapshot" in {c["name"] for c in inspector.get_columns("purchase_orders")}
            and (not inspector.has_table("supplier_changes") or "reason" in {c["name"] for c in inspector.get_columns("supplier_changes")}))


def filters(params):
    keys = ("search", "status", "approval_status", "category", "city", "preferred", "last_from", "last_to", "currency", "min_total", "max_total", "sort")
    result = {key: str(params.get(key, "")).strip() for key in keys}
    result["sort"] = result["sort"] or "name_asc"
    if result["approval_status"] not in ("", "all", "pending", "approved", "rejected"):
        raise ValidationError("Estado de aprobación inválido.")
    if result["status"] not in ("", "active", "inactive") or result["preferred"] not in ("", "yes", "no") or result["sort"] not in SORTS:
        raise ValidationError("Filtro inválido.")
    validate_amount_filters(result)
    start, end = [day(result[key], "Última compra", True) for key in ("last_from", "last_to")]
    if start and end and start > end:
        raise ValidationError("La fecha inicial no puede superar la final.")
    if any(len(result[key]) > 150 for key in ("search", "category", "city")):
        raise ValidationError("Filtro demasiado largo.")
    return result


def validate_amount_filters(values):
    monetary = values["min_total"] or values["max_total"] or values["sort"].startswith("total")
    if monetary and not response_includes_cost():
        raise ValidationError("No tiene permiso para filtrar u ordenar por montos.", status_code=403)
    if values["currency"] not in ("", "USD", "VES"):
        raise ValidationError("La moneda debe ser USD o VES.")
    if monetary and not values["currency"]:
        raise ValidationError("Seleccione USD o VES para filtrar u ordenar por importe.")
    amounts = [money(values[key]) if values[key] else None for key in ("min_total", "max_total")]
    if all(value is not None for value in amounts) and amounts[0] > amounts[1]:
        raise ValidationError("El monto mínimo no puede superar el máximo.")


def aggregated():
    po = PurchaseOrder
    included = po.status.notin_(("cancelled", "rejected"))
    # The persisted order currency owns its amounts; never join lines/products into sums.
    # NULL sums distinguish no eligible purchases from a valid zero amount.
    summary = db.session.query(po.supplier_id.label("supplier_id"),
        func.sum(case((included & (PurchaseOrder.currency == "USD"), po.total))).label("total_usd"),
        func.sum(case((included & (PurchaseOrder.currency == "VES"), po.total))).label("total_ves"),
        func.max(case((included, po.ordered_at), else_=None)).label("last"),
        func.count(po.id).label("orders"),
        func.sum(case((po.status.in_(("pending_approval", "pending", "approved", "partial")), 1), else_=0)).label("pending"),
        func.sum(case((po.status.in_(COMPLETED_STATUSES), 1), else_=0)).label("completed"),
        func.sum(case((PurchaseOrder.currency.is_(None), 1), else_=0)).label("unknown_currency_orders")
    ).group_by(po.supplier_id).subquery()
    totals = {"USD": summary.c.total_usd, "VES": summary.c.total_ves}
    return db.session.query(Supplier, summary.c.total_usd, summary.c.total_ves, summary.c.last, summary.c.orders,
                            summary.c.pending, summary.c.completed, summary.c.unknown_currency_orders).outerjoin(
                                summary, summary.c.supplier_id == Supplier.id), totals, summary.c.last


def query(values):
    result, totals, last = aggregated()
    total = totals.get(values["currency"])
    if values["search"]:
        needle = values["search"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        result = result.filter(or_(*(column.ilike("%" + needle + "%", escape="\\") for column in (Supplier.name, Supplier.tax_id, Supplier.contact_name))))
    for key in ("category", "city"):
        if values[key]:
            result = result.filter(getattr(Supplier, key) == values[key])
    if values["status"]:
        result = result.filter(Supplier.is_active.is_(values["status"] == "active"))
    if values["approval_status"] not in ("", "all"):
        pending = Supplier.id.in_(db.session.query(SupplierChange.supplier_id).filter_by(status="pending")) if table_ready("supplier_changes") else False
        condition = (Supplier.approval_status == "pending") | pending
        result = result.filter(condition if values["approval_status"] == "pending" else
                               (Supplier.approval_status == values["approval_status"]) & ~condition)
    if values["preferred"]:
        result = result.filter(Supplier.is_preferred.is_(values["preferred"] == "yes"))
    if values["last_from"]:
        result = result.filter(last >= _utc_boundary(day(values["last_from"], "Desde")))
    if values["last_to"]:
        result = result.filter(last < _utc_boundary(day(values["last_to"], "Hasta") + timedelta(days=1)))
    if values["min_total"]:
        result = result.filter(total >= money(values["min_total"]))
    if values["max_total"]:
        result = result.filter(total <= money(values["max_total"]))
    orders = {"name_asc": Supplier.name.asc(), "name_desc": Supplier.name.desc(),
              "last_desc": last.desc(), "last_asc": last.asc()}
    if values["sort"].startswith("total"):
        # No eligible purchases in this currency always come last in either direction.
        # Supplier.id breaks ties across pages, including absent purchases.
        return result.order_by(total.is_(None), total.desc() if values["sort"] == "total_desc" else total.asc(), Supplier.id)
    if values["sort"].startswith("last"):
        result = result.order_by(last.is_(None))
    return result.order_by(orders[values["sort"]], Supplier.id)


def serialize(row):
    supplier = row[0]
    result = {key: getattr(supplier, key) or "" for key in FIELDS}
    result.update(id=supplier.id, is_active=supplier.is_active, is_preferred=supplier.is_preferred,
                  approval_status=supplier.approval_status,
                  proposed_by_user_id=supplier.proposed_by_user_id,
                  approved_by_user_id=supplier.approved_by_user_id,
                  approved_at=supplier.approved_at.isoformat() if supplier.approved_at else None,
                  rejection_reason=supplier.rejection_reason,
                  created_at=supplier.created_at.isoformat(), updated_at=supplier.updated_at.isoformat(),
                  last=row.last.replace(tzinfo=timezone.utc).astimezone(COMMERCIAL_ZONE).date().isoformat() if row.last else "",
                  orders=int(row.orders or 0), pending=int(row.pending or 0), completed=int(row.completed or 0),
                  unknown_currency_orders=int(row.unknown_currency_orders or 0))
    if response_includes_cost():
        result["totals_by_currency"] = [{"currency": currency, "amount": f"{amount:.2f}"}
                                        for currency, amount in (("USD", row.total_usd), ("VES", row.total_ves))
                                        if amount is not None]
    from app.services.supplier_change_service import pending_for
    pending = pending_for(supplier.id)
    result["pending_change"] = pending.to_dict() if pending else None
    result["has_pending_approval"] = supplier.approval_status == "pending" or pending is not None
    return result


def pagination(params, count):
    size = integer(params.get("per_page", 8), "Por página")
    if size not in (8, 16, 24):
        raise ValidationError("Seleccione 8, 16 o 24 por página.")
    pages = max(1, (count + size - 1) // size)
    page = min(integer(params.get("page", 1), "Página"), pages)
    return {"page": page, "pages": pages, "per_page": size, "total": count,
            "from": (page - 1) * size + 1 if count else 0, "to": min(page * size, count),
            "numbers": sorted({1, pages, *range(max(1, page - 2), min(pages, page + 2) + 1)})}


def kpis():
    today = datetime.now(COMMERCIAL_ZONE).date()
    start = today.replace(day=1)
    previous_end = start - timedelta(days=1)
    previous_start = previous_end.replace(day=1)
    comparable_end = previous_start.replace(day=min(today.day, monthrange(previous_start.year, previous_start.month)[1]))
    def denominations(first, last):
        rows = db.session.query(PurchaseOrder.currency,
            func.sum(case((PurchaseOrder.currency.in_(("USD", "VES")), PurchaseOrder.total))),
            func.count(PurchaseOrder.id)).select_from(PurchaseOrder).filter(
                PurchaseOrder.status.notin_(("cancelled", "rejected")),
                PurchaseOrder.ordered_at >= _utc_boundary(first), PurchaseOrder.ordered_at < _utc_boundary(last + timedelta(days=1))
            ).group_by(PurchaseOrder.currency).order_by(PurchaseOrder.currency).all()
        return ([{"currency": currency, "amount": f"{amount:.2f}"} for currency, amount, _ in rows if currency in ("USD", "VES")],
                sum(count for currency, _, count in rows if currency not in ("USD", "VES")))
    totals, incomplete = denominations(start, today)
    previous_totals, previous_incomplete = denominations(previous_start, comparable_end)
    comparable_currency = (len(totals) == len(previous_totals) == 1
                           and totals[0]['currency'] in ('USD', 'VES')
                           and totals[0]['currency'] == previous_totals[0]['currency']
                           and not incomplete and not previous_incomplete)
    counts = dict(db.session.query(PurchaseOrder.status, func.count(PurchaseOrder.id)).group_by(PurchaseOrder.status).all())
    suppliers = Supplier.query.count()
    active = Supplier.query.filter_by(is_active=True).count()
    show_money = response_includes_cost()
    comparison = None
    if show_money and comparable_currency and Decimal(previous_totals[0]["amount"]):
        total, previous = Decimal(totals[0]["amount"]), Decimal(previous_totals[0]["amount"])
        comparison = f"{(total - previous) * 100 / previous:+.1f}% vs. mes anterior comparable"
    # This all-history count includes every status, like the order count above.
    unknown = PurchaseOrder.query.filter(
        PurchaseOrder.currency.is_(None)).count()
    return {"suppliers": suppliers, "active": active, "inactive": suppliers - active,
            "preferred": Supplier.query.filter_by(is_preferred=True).count(),
            "pending_approvals": query(filters({"approval_status": "pending"})).count(),
            "totals_by_currency": totals if show_money else [],
            "unknown_currency_orders": unknown,
            "comparison": comparison,
            "orders": sum(counts.values()), "pending": sum(counts.get(key, 0) for key in ("pending_approval", "pending", "approved", "partial")), "completed": sum(counts.get(key, 0) for key in COMPLETED_STATUSES)}


def listing(params):
    values = filters(params)
    ready = available()
    result = query(values) if ready else None
    p = pagination(params, result.count() if ready else 0)
    return {"available": ready, "filters": values, "pagination": p,
            "items": [serialize(row) for row in result.offset((p["page"] - 1) * p["per_page"]).limit(p["per_page"]).all()] if ready else [],
            "categories": distinct("category") if ready else [], "cities": distinct("city") if ready else [],
            "kpis": kpis() if ready else dict(suppliers=0, active=0, inactive=0, preferred=0, totals_by_currency=[], unknown_currency_orders=0, comparison=None, orders=0, pending=0, completed=0)}


def distinct(field):
    column = getattr(Supplier, field)
    return [r[0] for r in db.session.query(column).filter(column.isnot(None), column != "").distinct().order_by(column).all()]


def detail(supplier_id):
    from app.services import supplier_change_service

    get_supplier(supplier_id)
    result, _, _ = aggregated()
    data = serialize(result.filter(Supplier.id == supplier_id).one())
    pending = supplier_change_service.pending_for(supplier_id)
    data["pending_change"] = pending.to_dict() if pending else None
    data["changes_history"] = [row.to_dict() for row in SupplierChange.query.filter_by(supplier_id=supplier_id)
                               .order_by(SupplierChange.id.desc()).limit(100).all()] if table_ready("supplier_changes") else []
    all_orders = PurchaseOrder.query.filter_by(supplier_id=supplier_id)
    orders = all_orders.order_by(PurchaseOrder.ordered_at.desc(), PurchaseOrder.id.desc()).limit(10).all()
    show_money = response_includes_cost()
    def order_detail(o):
        return {"id": o.id, "number": o.order_number, "date": o.commercial_order_date.isoformat(),
                "status": o.status, "currency": o.currency,
                **({"total": f"{o.total:.2f}"} if show_money else {})}
    data["recent_orders"] = [order_detail(o) for o in orders]
    # Historical omissions must remain discoverable even beyond the recent ten.
    unknown_orders = all_orders.filter(
        PurchaseOrder.currency.is_(None)).order_by(PurchaseOrder.ordered_at.desc(), PurchaseOrder.id.desc()).all()
    data["unknown_currency_order_details"] = [order_detail(o) for o in unknown_orders]
    from app.services import product_supplier_service
    data["supplied_products"] = product_supplier_service.list_for_supplier(supplier_id)
    from app.services.purchase_query_service import stage_totals
    data["stage_totals"] = stage_totals(PurchaseOrder.query.filter_by(supplier_id=supplier_id))
    return data
