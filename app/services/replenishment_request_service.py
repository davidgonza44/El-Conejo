"""Solicitud de reposición sin precios, costos ni totales.

ponytail: una SR guarda un solo purchase_order_id y la OC un solo proveedor.
Techo: no reparte los renglones entre varias OC. Para partirla hace falta
una tabla de enlace y cubrir cada renglón por separado.
"""
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask_login import current_user
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models.purchase import PurchaseOrder
from app.models.replenishment_request import (
    ORIGIN_LABELS, STATUSES, STATUS_LABELS, ReplenishmentRequest, ReplenishmentRequestLine, shortage_still_covered,
)
from app.models.supplier_quote import SupplierQuote
from app.models.user import ROLE_INVENTARIO
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.inventory_service import _utc_boundary
from app.services.purchase_query_service import page_values
from app.services.purchase_service import day, integer, lock_products, only, text_value
from app.services.product_supplier_service import approved_suppliers_for_products
from app.services.rbac_service import require_operational_permission

PREPARE_GAP = (
    "La cotización no cubre todos los productos de la solicitud. "
    "Cada solicitud solo puede generar una orden de compra y no se reparte entre proveedores."
)
SPLIT_NOTICE = (
    "Ninguna cotización vigente cubre todos los productos. "
    "Cada solicitud solo puede generar una orden de compra y no se reparte entre proveedores. "
    "Puede cotizar con un proveedor aprobado y activo asociado a todos los productos y después preparar la orden."
)
NO_ASSOCIATED_SUPPLIER = "No existe un proveedor aprobado y activo asociado a todos los productos de esta solicitud."
_CLOSED = ("fulfilled", "cancelled", "converted")


def create_from_selection(body, commit=True):
    """Confirmación humana del predictivo. Rechaza campos monetarios enviados."""
    require_operational_permission("replenishment:create")
    if not isinstance(body, dict):
        raise ValidationError("Solicitud inválida.")
    only(body, ("confirm", "items", "notes", "idempotency_key"))
    notes = text_value(str(body.get("notes") or ""), "Notas", 1000) if body.get("notes") else ""
    return _create(body.get("items"), notes, "predictive", body.get("idempotency_key"), commit=commit)


def create(data, commit=True):
    require_operational_permission("replenishment:create")
    only(data, ("items", "notes", "idempotency_key"))
    notes = text_value(data.get("notes") or "", "Notas", 1000) if data.get("notes") else ""
    return _create(data.get("items"), notes, "manual", data.get("idempotency_key"), commit=commit)


SHORTAGE_ORDER_STATUSES = ("closed_partial", "closed_unavailable", "closed_price_change")


def create_from_shortage(order_id, data):
    """Nueva solicitud con el faltante documentado de una OC cerrada. No modifica la OC original.

    El bloqueo de la OC serializa reintentos y peticiones simultáneas; la búsqueda de una
    solicitud previa usa lectura con bloqueo para ver lo confirmado por la transacción anterior.
    """
    require_operational_permission("replenishment:create")
    only(data, ())
    from app.services.purchase_service import get_order

    try:
        order = get_order(order_id, lock=True)
        if order.status not in SHORTAGE_ORDER_STATUSES:
            raise ConflictError("Solo una orden cerrada con faltante admite una solicitud de reposición del faltante.")
        items = [{"product_id": line.product_id, "quantity": line.quantity_not_supplied}
                 for line in order.lines if line.quantity_not_supplied > 0]
        if not items:
            raise ConflictError("La orden no tiene cantidades no suministradas.")
        existing = _covering_shortage_request(order.id)
        if existing is not None:
            if existing.created_by_user_id == current_user.id and existing.status in ("open", "processing"):
                db.session.rollback()
                return get_visible(existing.id)
            raise ConflictError(f"El faltante de {order.order_number} ya tiene la solicitud {existing.request_number}.")
        notes = (f"Reposición del faltante no suministrado de {order.order_number}. "
                 f"Motivo del cierre: {order.closure_reason or 'No registrado'}")[:1000]
        row = _create(items, notes, "shortage", commit=False)
        row.source_purchase_order_id = order.id
        db.session.commit()
        return row
    except Exception:
        db.session.rollback()
        raise


def _covering_shortage_request(source_order_id):
    """Solicitud previa que aún cubre el faltante, leída con bloqueo.

    Orden de bloqueo: OC de origen (ya tomada por el llamador), solicitudes, OC generada.
    Rechazada y anulada son estados terminales de la OC generada; si una de ellas libera el
    faltante, ninguna transición posterior puede volver a cubrirlo.
    """
    rows = (ReplenishmentRequest.query.filter(ReplenishmentRequest.source_purchase_order_id == source_order_id)
            .order_by(ReplenishmentRequest.id).populate_existing().with_for_update().all())
    for row in rows:
        order_status = None
        if row.purchase_order_id:
            generated = (PurchaseOrder.query.filter_by(id=row.purchase_order_id)
                         .populate_existing().with_for_update().first())
            order_status = generated.status if generated else None
        if shortage_still_covered(row.status, order_status):
            return row
    return None


def _retry(key, lines, notes, origin):
    row = ReplenishmentRequest.query.filter_by(created_by_user_id=current_user.id, idempotency_key=key).first()
    if row is not None:
        requested = sorted((line.product_id, line.quantity) for line in lines)
        existing = sorted((line.product_id, line.quantity) for line in row.lines)
        if requested != existing or row.notes != notes or row.origin != origin:
            raise ConflictError("El identificador de reintento ya corresponde a otra solicitud.")
    return row


def _request_lines(items):
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValidationError("Indique entre 1 y 100 productos.")
    seen, requested = set(), []
    for item in items:
        only(item, ("product_id", "quantity"))
        product_id = integer(item.get("product_id"), "Producto")
        if product_id in seen:
            raise ValidationError("No se permiten productos duplicados.")
        seen.add(product_id)
        requested.append((product_id, integer(item.get("quantity"), "Cantidad")))
    # Ascending id, the product lock order shared with quotes and approvals.
    products, lines = lock_products(seen), []
    for product_id, quantity in requested:
        product = products.get(product_id)
        if product is None or not product.is_active or product.approval_status != "approved":
            raise ValidationError("Todos los productos deben existir, estar aprobados y activos.")
        lines.append(ReplenishmentRequestLine(
            product_id=product.id, product_code=product.code, product_name=product.name,
            unit=product.unit, quantity=quantity, stock_on_hand=int(product.current_stock)))
    return lines


def _create(items, notes, origin, key=None, commit=True):
    if origin not in ORIGIN_LABELS:
        raise ValidationError("Origen de solicitud inválido.")
    if key is not None and (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", key)):
        raise ValidationError("Identificador de reintento inválido.")
    lines = _request_lines(items)
    if key:
        row = _retry(key, lines, notes, origin)
        if row:
            db.session.rollback()
            return get_visible(row.id)
    try:
        request = ReplenishmentRequest(
            created_by_user_id=current_user.id, notes=notes, status="open", origin=origin, idempotency_key=key)
        request.lines = lines
        db.session.add(request)
        db.session.flush()
        request.request_number = f"SR-{request.id:06d}"
        if commit:
            db.session.commit()
        return request
    except IntegrityError:
        if commit:
            db.session.rollback()
            if key:
                row = _retry(key, lines, notes, origin)
                if row:
                    return row
        raise ConflictError("La solicitud entró en conflicto. Consulte la bandeja antes de reintentar.") from None
    except Exception:
        if commit:
            db.session.rollback()
        raise


def get_request(request_id, lock=False):
    query = ReplenishmentRequest.query.filter_by(id=request_id)
    if lock:
        query = query.populate_existing().with_for_update()
    row = query.first()
    if row is None:
        raise NotFoundError("Solicitud de reposición no encontrada.")
    return row


def get_visible(request_id, lock=False):
    row = get_request(request_id, lock=lock)
    if current_user.role == ROLE_INVENTARIO and row.created_by_user_id != current_user.id:
        raise NotFoundError("Solicitud de reposición no encontrada.")
    return row


def _filters(params):
    if str(params.get("priority") or "").strip():
        raise ValidationError("Las solicitudes de reposición no tienen prioridad.")
    search = str(params.get("search") or "").strip()[:150]
    status = str(params.get("status") or "").strip()
    if status and status not in STATUSES:
        raise ValidationError("Estado de solicitud inválido.")
    start = day(params.get("start") or "", "Fecha inicio", optional=True)
    end = day(params.get("end") or "", "Fecha fin", optional=True)
    if start and end and start > end:
        raise ValidationError("La fecha inicial no puede superar la final.")
    return {"search": search, "status": status, "start": start, "end": end}


def _scoped(query):
    if current_user.role == ROLE_INVENTARIO:
        return query.filter(ReplenishmentRequest.created_by_user_id == current_user.id)
    return query


def _apply(query, values):
    if values["search"]:
        needle = values["search"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        match = f"%{needle}%"
        query = query.filter(or_(
            ReplenishmentRequest.request_number.ilike(match, escape="\\"),
            ReplenishmentRequest.lines.any(or_(
                ReplenishmentRequestLine.product_name.ilike(match, escape="\\"),
                ReplenishmentRequestLine.product_code.ilike(match, escape="\\")))))
    if values["status"]:
        query = query.filter(ReplenishmentRequest.status == values["status"])
    if values["start"]:
        query = query.filter(ReplenishmentRequest.created_at >= _utc_boundary(values["start"]))
    if values["end"]:
        query = query.filter(ReplenishmentRequest.created_at < _utc_boundary(values["end"] + timedelta(days=1)))
    return query


def list_requests(params):
    values = _filters(params)
    query = _apply(_scoped(ReplenishmentRequest.query), values)
    total = query.order_by(None).count()
    paging = page_values(params, total)
    rows = (query.options(
        joinedload(ReplenishmentRequest.creator), joinedload(ReplenishmentRequest.processor),
        selectinload(ReplenishmentRequest.lines),
    ).order_by(ReplenishmentRequest.id.desc())
            .offset((paging["page"] - 1) * paging["per_page"]).limit(paging["per_page"]).all())
    return {
        "items": [row.to_dict() for row in rows],
        "filters": {
            "search": values["search"], "status": values["status"],
            "start": values["start"].isoformat() if values["start"] else "",
            "end": values["end"].isoformat() if values["end"] else "",
        },
        "pagination": paging,
        "statuses": [{"value": key, "label": STATUS_LABELS[key]} for key in STATUSES],
    }


def start(request_id):
    require_operational_permission("replenishment:process")
    try:
        row = get_visible(request_id, lock=True)
        if row.status != "open":
            raise ConflictError("Solo una solicitud abierta puede iniciar procesamiento.")
        row.status = "processing"
        row.processed_by_user_id = current_user.id
        row.processed_at = datetime.utcnow()
        db.session.commit()
        return row
    except Exception:
        db.session.rollback()
        raise


def cancel(request_id):
    require_operational_permission("replenishment:process")
    try:
        row = get_visible(request_id, lock=True)
        if row.status not in ("open", "processing") or row.purchase_order_id:
            raise ConflictError("Solo se anula una solicitud que aún no tiene orden de compra.")
        row.status = "cancelled"
        row.cancelled_by_user_id = current_user.id
        row.cancelled_at = datetime.utcnow()
        db.session.commit()
        return row
    except Exception:
        db.session.rollback()
        raise


def _missing(row, quote):
    covered = {line.product_id for line in quote.lines}
    return [line.product_code for line in row.lines if line.product_id not in covered]


def quote_options(row):
    require_operational_permission("replenishment:process")
    today = datetime.now(ZoneInfo("America/Caracas")).date()
    needed = {line.product_id for line in row.lines}
    suppliers = {supplier['id'] for supplier in approved_suppliers(row)}
    quotes = (SupplierQuote.query.options(joinedload(SupplierQuote.supplier), selectinload(SupplierQuote.lines))
              .filter_by(status="vigente").filter(SupplierQuote.valid_until >= today)
              .filter(SupplierQuote.supplier_id.in_(suppliers), SupplierQuote.issued_on <= today,
                      *(SupplierQuote.lines.any(product_id=key) for key in sorted(needed)))
              .order_by(SupplierQuote.id.desc()).limit(100).all())
    from app.services.supplier_quote_service import eligibility_error
    covering = []
    for quote in quotes:
        if eligibility_error(quote, today):
            continue
        payload = {
            "id": quote.id, "quote_number": quote.quote_number,
            "supplier_name": quote.supplier.name if quote.supplier else "",
            "currency": quote.currency, "valid_until": quote.valid_until.isoformat(),
            "missing_products": [],
        }
        payload["prices"] = [
            {"product_code": line.product_code, "unit_price": f"{line.unit_price:.2f}"}
            for line in quote.lines if line.product_id in needed
        ]
        covering.append(payload)
    return {"covering": covering, "partial": [], "notice": "" if covering else SPLIT_NOTICE}


def approved_suppliers(row):
    return approved_suppliers_for_products(line.product_id for line in row.lines)


def create_quote(request_id, data):
    """Crea una cotización que cubre toda la solicitud. No crea la orden."""
    require_operational_permission("replenishment:process")
    only(data, ("supplier_id", "currency", "issued_on", "valid_until", "notes", "items"))
    row = get_visible(request_id, lock=True)
    if row.status != "processing":
        raise ConflictError("Inicie el procesamiento antes de cotizar.")
    today = datetime.now(ZoneInfo("America/Caracas")).date()
    if (day(data.get("issued_on"), "Emisión") > today
            or day(data.get("valid_until"), "Válida hasta") < today):
        raise ValidationError("Registre una cotización emitida y vigente hoy para esta solicitud.")
    sent = data.get("items")
    if not isinstance(sent, list):
        raise ValidationError("Indique el precio de cada producto.")
    prices, seen = {}, set()
    for item in sent:
        only(item, ("product_id", "unit_price"))
        product_id = integer(item.get("product_id"), "Producto")
        if product_id in seen:
            raise ValidationError("No se permiten productos duplicados.")
        seen.add(product_id)
        prices[product_id] = item.get("unit_price")
    if seen != {line.product_id for line in row.lines}:
        raise ValidationError("Indique el precio de cada producto de la solicitud, sin agregar otros.")
    from app.services.supplier_quote_service import create as create_supplier_quote

    return create_supplier_quote({
        "supplier_id": data.get("supplier_id"), "currency": data.get("currency"),
        "issued_on": data.get("issued_on"), "valid_until": data.get("valid_until"),
        "notes": data.get("notes") or f"Cotización para {row.request_number}",
        "items": [{"product_id": line.product_id, "quantity": line.quantity, "unit_price": prices[line.product_id]}
                  for line in row.lines],
    })


def prepare_order(request_id, data):
    """Prepara una OC explícita. No parte la solicitud entre proveedores."""
    require_operational_permission("replenishment:process")
    only(data, ("supplier_quote_id",))
    row = get_visible(request_id, lock=True)
    if row.status in _CLOSED or row.purchase_order_id:
        raise ConflictError("La solicitud ya fue procesada y no admite otra orden.")
    if row.status != "processing":
        raise ConflictError("Inicie el procesamiento de la solicitud antes de preparar la orden.")
    quote_id = integer(data.get("supplier_quote_id"), "Cotización")
    quote = db.session.get(SupplierQuote, quote_id)
    if quote is None:
        raise NotFoundError("Cotización de proveedor no encontrada.")
    if _missing(row, quote):
        raise ValidationError(PREPARE_GAP)
    from app.services.purchase_service import create_order

    create_order({
        "supplier_id": quote.supplier_id, "supplier_quote_id": quote.id,
        "replenishment_request_id": row.id,
        "notes": f"Solicitud de reposición {row.request_number}.",
        "items": [{"product_id": line.product_id, "quantity": line.quantity} for line in row.lines],
    })
    return get_visible(request_id)


def mark_converted(request_id, order):
    """Enlaza la OC ya armada. No modifica renglones, notas, creador ni stock."""
    row = get_request(request_id, lock=True)
    if (row.status in _CLOSED or row.purchase_order_id
            or PurchaseOrder.query.filter_by(replenishment_request_id=row.id).first() is not None):
        raise ConflictError("La solicitud ya fue procesada y no admite otra orden.")
    if row.status != "processing":
        raise ConflictError("Inicie el procesamiento de la solicitud antes de preparar la orden.")
    requested = {line.product_id: line.quantity for line in row.lines}
    ordered = {line.product_id: line.quantity_ordered for line in order.lines}
    if requested != ordered:
        raise ValidationError(PREPARE_GAP)
    row.status = "fulfilled"
    row.purchase_order_id = order.id
    order.replenishment_request_id = row.id
    return row
