"""Read-only purchase filtering, period aggregates and export share one query contract."""
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import Product
from app.models.purchase import COMPLETED_STATUSES, ProductSupplier, Supplier, PurchaseOrder, PurchaseOrderLine, STATUS_LABELS
from app.models.purchase_closure import PurchaseClosureRequest
from app.services.inventory_service import COMMERCIAL_ZONE, _utc_boundary
from app.services.purchase_service import day, integer
from app.services.exceptions import ValidationError
from app.utils.schema import table_ready


def available():
    return all(table_ready(name) for name in ("suppliers", "purchase_orders", "purchase_order_lines", "purchase_receptions", "purchase_reception_lines"))


def filters(params, reference=False):
    today = datetime.now(COMMERCIAL_ZONE).date()
    default_start, default_end = today.replace(day=1).isoformat(), today.isoformat()
    if reference:
        default_start, default_end = "2024-05-01", "2024-05-31"
    result = {"search": str(params.get("search", "")).strip()[:150], "status": params.get("status", ""),
              "supplier_id": params.get("supplier_id", ""),
              "start": params.get("start", default_start), "end": params.get("end", default_end)}
    if params.get("closure_status"):
        if params["closure_status"] != "pending":
            raise ValidationError("Filtro de cierre inválido.")
        result["closure_status"] = "pending"
    if result["status"] and result["status"] not in STATUS_LABELS:
        raise ValidationError("Estado de orden inválido.")
    if result["supplier_id"]:
        result["supplier_id"] = integer(result["supplier_id"], "Proveedor")
    start, end = day(result["start"], "Fecha inicio", True), day(result["end"], "Fecha fin", True)
    if start and end and start > end:
        raise ValidationError("La fecha inicial no puede superar la final.")
    return result


def query(filters):
    result = PurchaseOrder.query.join(Supplier)
    if filters["search"]:
        needle = filters["search"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        match = f"%{needle}%"
        result = result.filter(or_(PurchaseOrder.order_number.ilike(match, escape="\\"), Supplier.name.ilike(match, escape="\\"),
                                   PurchaseOrder.lines.any(or_(PurchaseOrderLine.product_name.ilike(match, escape="\\"),
                                                              PurchaseOrderLine.product_code.ilike(match, escape="\\")))))
    if filters["status"]:
        result = result.filter(PurchaseOrder.status == filters["status"])
    if filters.get("closure_status") == "pending":
        result = result.filter(PurchaseOrder.status == "approved", PurchaseOrder.id.in_(
            db.session.query(PurchaseClosureRequest.purchase_order_id).filter_by(status="pending")))
    if filters["supplier_id"]:
        result = result.filter(PurchaseOrder.supplier_id == filters["supplier_id"])
    if filters["start"]:
        result = result.filter(PurchaseOrder.ordered_at >= _utc_boundary(day(filters["start"], "Inicio")))
    if filters["end"]:
        result = result.filter(PurchaseOrder.ordered_at < _utc_boundary(day(filters["end"], "Fin") + timedelta(days=1)))
    return result


def relations(result):
    return result.options(joinedload(PurchaseOrder.supplier), joinedload(PurchaseOrder.creator),
                          joinedload(PurchaseOrder.supplier_quote), selectinload(PurchaseOrder.lines))


def currency_totals(result):
    rows = (result.filter(PurchaseOrder.currency.in_(("USD", "VES")))
            .with_entities(PurchaseOrder.currency, func.sum(PurchaseOrder.total))
            .group_by(PurchaseOrder.currency).order_by(PurchaseOrder.currency).all())
    return [{"currency": currency, "amount": f"{amount:.2f}"} for currency, amount in rows]


def _comparable_totals(current, previous):
    return (len(current) == len(previous) == 1 and current[0]['currency'] in ('USD', 'VES')
            and current[0]['currency'] == previous[0]['currency'])


def _currency_comparisons(current, previous):
    """Variación por moneda; nunca convierte ni suma monedas distintas ni divide entre cero."""
    before = {row['currency']: Decimal(row['amount']) for row in previous}
    result = []
    for row in current:
        amount = before.get(row['currency'])
        if amount is None:
            values = {"previous_amount": None, "change_percent": None, "reason": "no_previous",
                      "text": "Sin compras en el período anterior"}
        elif not amount:
            values = {"previous_amount": f"{amount:.2f}", "change_percent": None, "reason": "previous_zero",
                      "text": "Período anterior con importe cero; sin variación porcentual"}
        else:
            change = f"{(Decimal(row['amount']) - amount) * 100 / amount:+.1f}"
            values = {"previous_amount": f"{amount:.2f}", "change_percent": change, "reason": "ok",
                      "text": f"{change.replace('.', ',')} % vs. período anterior"}
        result.append({"currency": row['currency'], **values})
    return result


def _supplier_amounts(result):
    suppliers = (result.filter(PurchaseOrder.currency.in_(("USD", "VES")))
                 .with_entities(Supplier.id, Supplier.name, PurchaseOrder.currency, func.sum(PurchaseOrder.total).label("amount"))
                 .group_by(Supplier.id, Supplier.name, PurchaseOrder.currency)
                 .order_by(PurchaseOrder.currency, func.sum(PurchaseOrder.total).desc(), Supplier.id).all())
    groups, top = {}, []
    for row in suppliers:
        amounts = groups.setdefault(row.currency, [])
        if len(amounts) < 3:
            amounts.append(row.amount)
            top.append({"name": row.name, "currency": row.currency, "amount": f"{row.amount:.2f}",
                        "percent": int(row.amount * 100 / amounts[0]) if amounts[0] else 0})
    return top


def page_values(params, count):
    per_page = integer(params.get("per_page", 8), "Por página")
    if per_page not in (8, 20, 50):
        raise ValidationError("Por página: seleccione 8, 20 o 50.")
    pages = max(1, (count + per_page - 1) // per_page)
    page = min(integer(params.get("page", 1), "Página"), pages)
    window = sorted({1, pages, *range(max(1, page - 2), min(pages, page + 2) + 1)})
    numbers = []
    for number in window:
        if numbers and number - numbers[-1] > 1:
            numbers.append(None)
        numbers.append(number)
    return {"page": page, "per_page": per_page, "pages": pages, "total": count, "numbers": numbers,
            "from": (page - 1) * per_page + 1 if count else 0, "to": min(page * per_page, count)}


def aggregates(result, values):
    counts = dict(result.with_entities(PurchaseOrder.status, func.count(PurchaseOrder.id)).group_by(PurchaseOrder.status).all())
    non_cancelled = result.filter(PurchaseOrder.status.notin_(("cancelled", "rejected")))
    totals = currency_totals(non_cancelled)
    completed = sum(counts.get(key, 0) for key in COMPLETED_STATUSES)
    relevant = sum(counts.get(key, 0) for key in ("pending_approval", "pending", "approved", "partial")) + completed
    comparison, comparison_by_currency = None, []
    if values["start"] and values["end"]:
        start, end = day(values["start"], "Inicio"), day(values["end"], "Fin")
        previous_values = {**values, "start": (start - (end - start) - timedelta(days=1)).isoformat(), "end": (start - timedelta(days=1)).isoformat()}
        previous_totals = currency_totals(query(previous_values).filter(PurchaseOrder.status.notin_(("cancelled", "rejected"))))
        comparison_by_currency = _currency_comparisons(totals, previous_totals)
        if _comparable_totals(totals, previous_totals):
            current, previous = Decimal(totals[0]['amount']), Decimal(previous_totals[0]['amount'])
            if previous:
                comparison = f"{(current - previous) * 100 / previous:+.1f}%"
    top = _supplier_amounts(non_cancelled)
    payload = {"count": sum(counts.values()), "total": None, "totals_by_currency": totals,
            "pending": counts.get("pending_approval", 0),
            "completed": completed,
            "completed_percent": f"{completed * 100 / relevant:.1f}%" if relevant else "Sin órdenes",
            "comparison": comparison, "comparison_by_currency": comparison_by_currency,
            "counts": {key: counts.get(key, 0) for key in STATUS_LABELS}, "relevant": relevant, "top_suppliers": top}
    from app.utils.permissions import response_includes_cost
    if not response_includes_cost():
        payload["total"] = None
        payload["totals_by_currency"] = []
        payload["comparison"] = None
        payload["comparison_by_currency"] = []
        payload["top_suppliers"] = []
    return payload


def list_orders(params):
    values = filters(params)
    if not available():
        return {"available": False, "filters": values, "items": [], "suppliers": [], "upcoming": [],
                "pagination": page_values(params, 0), "kpis": {"count": 0, "total": None, "totals_by_currency": [], "pending": 0, "completed": 0,
                    "completed_percent": "Sin órdenes", "comparison": None, "comparison_by_currency": [], "counts": dict.fromkeys(STATUS_LABELS, 0), "relevant": 0, "top_suppliers": []}}
    result = query(values)
    pagination = page_values(params, result.count())
    rows = relations(result).order_by(PurchaseOrder.ordered_at.desc(), PurchaseOrder.id.desc()).offset((pagination["page"] - 1) * pagination["per_page"]).limit(pagination["per_page"]).all()
    upcoming = relations(PurchaseOrder.query.filter(PurchaseOrder.status == "approved", PurchaseOrder.estimated_delivery_date.isnot(None),
                                                     PurchaseOrder.lines.any(PurchaseOrderLine.quantity_received < PurchaseOrderLine.quantity_ordered)))
    upcoming = upcoming.order_by(PurchaseOrder.estimated_delivery_date, PurchaseOrder.id).limit(3).all()
    return {"available": True, "filters": values, "items": [row.to_dict(False) for row in rows],
            "suppliers": [row.to_dict() for row in Supplier.query.order_by(Supplier.name).all()], "kpis": aggregates(result, values),
            "upcoming": [row.to_dict(False) for row in upcoming], "pagination": pagination}


def _supplier_links(supplier_id):
    """Asociaciones activas de un proveedor activo. Un inactivo no sugiere productos ni costos."""
    if not supplier_id or not table_ready("product_suppliers"):
        return {}
    supplier = Supplier.query.filter_by(id=integer(supplier_id, "Proveedor"), is_active=True).first()
    if supplier is None:
        return {}
    rows = ProductSupplier.query.filter_by(supplier_id=supplier.id, is_active=True).all()
    return {row.product_id: row for row in rows}


def _option_product(row, link):
    cost = link.purchase_price if link is not None else row.purchase_price
    currency = link.purchase_price_currency if link is not None else row.purchase_price_currency
    return {"id": row.id, "code": row.code, "name": row.name, "unit_cost": f"{cost:.2f}",
            "unit_cost_currency": currency,
            "cost_source": "supplier" if link is not None else "reference", "associated": link is not None,
            "lead_time_days": None if link is None else link.lead_time_days,
            "minimum_order_quantity": None if link is None else link.minimum_order_quantity}


def options(search="", supplier_id=None, supplier_quote_id=None):
    products = Product.query.filter(Product.is_active.is_(True))
    if search:
        match = "%" + search.replace("%", "\\%").replace("_", "\\_")[:150] + "%"
        products = products.filter(or_(Product.name.ilike(match, escape="\\"), Product.code.ilike(match, escape="\\")))
    links = _supplier_links(supplier_id)
    associated = products.filter(Product.id.in_(links)).all() if links else []
    associated.sort(key=lambda row: (not links[row.id].is_preferred, row.name, row.id))
    if len(associated) > 50:
        associated = associated[:50]
    room = 50 - len(associated)
    others = []
    if room > 0:
        rest = products.filter(Product.id.notin_(list(links))) if links else products
        others = rest.order_by(Product.name, Product.id).limit(room).all()
    rows = associated + others
    from app.services.supplier_quote_service import usable_options
    from app.utils.permissions import response_includes_cost
    products_out = [_option_product(row, links.get(row.id)) for row in rows]
    if supplier_quote_id:
        from app.services.supplier_quote_service import usable_for_order
        quote = usable_for_order(supplier_quote_id, integer(supplier_id, "Proveedor"), lock=False)
        prices = {line.product_id: line.unit_price for line in quote.lines}
        quoted = products.filter(Product.id.in_(prices), Product.approval_status == "approved").order_by(Product.name).all()
        products_out = [{**_option_product(row, links.get(row.id)), "unit_cost": f"{prices[row.id]:.2f}",
                         "unit_cost_currency": quote.currency, "cost_source": "quote"} for row in quoted]
    if not response_includes_cost():
        for item in products_out:
            item.pop("unit_cost", None)
            item.pop("unit_cost_currency", None)
            item.pop("cost_source", None)
    return {"suppliers": [row.to_dict() for row in Supplier.query.filter_by(is_active=True, approval_status="approved").order_by(Supplier.name).all()],
            "quotes": usable_options(supplier_id) if response_includes_cost() else [],
            "products": products_out}
