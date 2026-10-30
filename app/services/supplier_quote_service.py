"""Cotizaciones de proveedor. Una vencida o reemplazada no puede alimentar una orden."""
from datetime import datetime
from zoneinfo import ZoneInfo

from flask_login import current_user

from app.extensions import db
from app.models.purchase import Supplier
from app.models.supplier_quote import SupplierQuote, SupplierQuoteLine
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.purchase_service import day, integer, lock_products, money, only, text_value
from app.services.product_supplier_service import require_active_associations
from app.services.rbac_service import require_operational_permission


def _today():
    return datetime.now(ZoneInfo("America/Caracas")).date()


def _lines(items):
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValidationError("Agregue entre 1 y 100 productos.")
    seen, lines = set(), []
    for item in items:
        only(item, ("product_id", "quantity", "unit_price"))
        product_id = integer(item.get("product_id"), "Producto")
        if product_id in seen:
            raise ValidationError("No se permiten productos duplicados.")
        seen.add(product_id)
    # Ascending id, like approval: a quote listing [P2, P1] cannot cross an approval of [P1, P2].
    products = lock_products(seen)
    for item in items:
        product = products.get(int(item["product_id"]))
        if product is None or not product.is_active or product.approval_status != "approved" or getattr(product, "is_service", False):
            raise ValidationError("Todos los productos deben existir, estar aprobados y activos.")
        price = money(item.get("unit_price"))
        if price <= 0:
            raise ValidationError("El precio cotizado debe ser mayor que cero.")
        lines.append(SupplierQuoteLine(
            product_id=product.id, product_code=product.code, product_name=product.name,
            quantity=integer(item.get("quantity"), "Cantidad"), unit_price=price))
    return lines


def _replacement(quote_id, supplier_id):
    if not quote_id:
        return None
    replaced = _get(integer(quote_id, "Cotización reemplazada"), lock=True)
    if replaced.supplier_id != supplier_id:
        raise ValidationError("Solo se reemplaza una cotización del mismo proveedor.")
    if replaced.effective_status() == "cancelada":
        raise ConflictError("Una cotización cancelada no se reemplaza.")
    return replaced


def create(data, commit=True):
    require_operational_permission("supplier_quotes:write")
    only(data, ("supplier_id", "issued_on", "currency", "valid_until", "notes", "items", "replaces_quote_id"))
    supplier = (Supplier.query.filter_by(id=integer(data.get("supplier_id"), "Proveedor"))
                .populate_existing().with_for_update().first())
    if supplier is None or not supplier.is_active or supplier.approval_status != "approved":
        raise ValidationError("Seleccione un proveedor aprobado y activo.")
    currency = data.get("currency")
    if not isinstance(currency, str) or currency not in ("USD", "VES"):
        raise ValidationError("La moneda debe ser USD o VES.")
    issued_on = day(data.get("issued_on"), "Fecha de emisión")
    valid_until = day(data.get("valid_until"), "Válida hasta")
    if valid_until < issued_on:
        raise ValidationError("La fecha de vencimiento no puede ser anterior a la emisión.")
    lines = _lines(data.get("items"))
    replaced = _replacement(data.get("replaces_quote_id"), supplier.id)
    try:
        require_active_associations(supplier.id, (line.product_id for line in lines))
        quote = SupplierQuote(
            supplier_id=supplier.id, issued_on=issued_on, currency=currency, valid_until=valid_until,
            status="vigente", notes=text_value(data.get("notes") or "", "Notas", 1000) if data.get("notes") else "",
            created_by_user_id=current_user.id, replaces_quote_id=replaced.id if replaced else None)
        quote.lines = lines
        db.session.add(quote)
        db.session.flush()
        quote.quote_number = f"CP-{quote.id:06d}"
        if replaced is not None and replaced.status == "vigente":
            replaced.status = "reemplazada"
        if commit:
            db.session.commit()
        return quote
    except Exception:
        if commit:
            db.session.rollback()
        raise


def cancel(quote_id):
    require_operational_permission("supplier_quotes:write")
    try:
        quote = _get(quote_id, lock=True)
        if quote.effective_status() in ("reemplazada", "cancelada"):
            raise ConflictError("La cotización ya no puede cancelarse.")
        quote.status = "cancelada"
        db.session.commit()
        return quote
    except Exception:
        db.session.rollback()
        raise


def _get(quote_id, lock=False):
    query = SupplierQuote.query.filter_by(id=quote_id)
    if lock:
        query = query.populate_existing().with_for_update()
    quote = query.first()
    if quote is None:
        raise NotFoundError("Cotización de proveedor no encontrada.")
    return quote


def usable_for_order(quote_id, supplier_id, lock=True):
    quote = _get(integer(quote_id, "Cotización"), lock=lock)
    if quote.supplier_id != supplier_id:
        raise ValidationError("La cotización no corresponde al proveedor.")
    reason = eligibility_error(quote)
    if reason:
        raise ValidationError(reason)
    return quote


def eligibility_error(quote, today=None):
    """Same date/line rules for options and submit, including altered legacy records."""
    today = today or _today()
    if (quote.status != "vigente" or quote.issued_on > today or quote.valid_until < today
            or quote.valid_until < quote.issued_on):
        return "La cotización no está vigente y no puede usarse en una orden."
    if quote.currency not in ("USD", "VES"):
        return "La cotización tiene una moneda inválida. Registre una cotización válida."
    ids = [line.product_id for line in quote.lines]
    if not ids or len(ids) != len(set(ids)) or any(line.unit_price <= 0 or line.quantity <= 0 for line in quote.lines):
        return "La cotización tiene productos o precios inválidos. Registre una cotización válida."
    return ""


def order_mismatch(quote, order):
    """La cotización que respalda un borrador coincide con él en proveedor, moneda, productos y precios."""
    if quote.supplier_id != order.supplier_id:
        return "La cotización no corresponde al proveedor del borrador."
    if quote.currency != order.currency:
        return "La moneda de la cotización no coincide con la del borrador."
    quoted = {line.product_id: line for line in quote.lines}
    for line in order.lines:
        if line.product_id not in quoted:
            return f"La cotización no incluye {line.product_code}."
        if quoted[line.product_id].quantity != line.quantity_ordered:
            return f"La cantidad cotizada de {line.product_code} no coincide con el borrador. Registre una cotización para esa cantidad."
        if quoted[line.product_id].unit_price != line.unit_cost:
            return f"El precio cotizado de {line.product_code} no coincide con el del borrador."
    return ""


def usable_options(supplier_id=None):
    if not supplier_id:
        return []
    supplier_id = integer(supplier_id, "Proveedor")
    today = _today()
    rows = (SupplierQuote.query.filter_by(status="vigente", supplier_id=supplier_id).filter(SupplierQuote.valid_until >= today)
            .order_by(SupplierQuote.id.desc()).limit(100).all())
    return [{"id": row.id, "supplier_id": row.supplier_id, "currency": row.currency,
             "valid_until": row.valid_until.isoformat(),
             "label": f"{row.quote_number} · {row.currency} · hasta {row.valid_until.isoformat()}"}
            for row in rows if not eligibility_error(row, today)]
