"""Atomic purchase intentions and receiving; only InventoryService changes stock."""
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask_login import current_user
from sqlalchemy import select
from sqlalchemy.orm.attributes import set_committed_value

from app.extensions import db
from app.models import Product
from app.models.purchase import Supplier, PurchaseOrder, PurchaseOrderLine, PurchaseReception, PurchaseReceptionLine, STATUS_LABELS
from app.services import inventory_service, notification_service
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.rbac_service import require_operational_permission

MAX_MONEY = Decimal("9999999999.99")


def integer(value, label, minimum=1):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]{1,10}", str(value)):
        raise ValidationError(f"{label}: debe ser un entero.")
    value = int(value)
    if not minimum <= value <= 2147483647:
        raise ValidationError(f"{label}: cantidad fuera del rango permitido.")
    return value


def text_value(value, label, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValidationError(f"{label}: texto requerido, máximo {maximum} caracteres.")
    return value.strip()


def money(value):
    if isinstance(value, (bool, float)):
        raise ValidationError("Costo: use un importe decimal, sin más de dos decimales.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError("Costo unitario inválido.")
    if not result.is_finite() or not 0 <= result <= MAX_MONEY or result != result.quantize(Decimal("0.01")):
        raise ValidationError("Costo: importe no negativo con un máximo de dos decimales.")
    return result


def day(value, label, optional=False):
    if optional and value in (None, ""):
        return None
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError
        result = date.fromisoformat(value)
        if result.year < 1900 or result.year > 9998:
            raise ValueError
        return result
    except ValueError:
        raise ValidationError(f"{label}: fecha inválida (AAAA-MM-DD).")


def only(data, keys):
    if not isinstance(data, dict) or set(data) - set(keys):
        raise ValidationError("Campos no permitidos en la solicitud.")


def get_order(order_id, lock=False):
    query = PurchaseOrder.query.filter_by(id=order_id)
    if lock:
        # Refresh after waiting on the lock; never use a stale identity-map status.
        query = query.populate_existing().with_for_update()
    order = query.first()
    if order is None:
        raise NotFoundError("Orden de compra no encontrada.")
    if lock:
        # Locking reads see the latest committed receipt quantities even when this
        # transaction already established a REPEATABLE READ snapshot via RBAC.
        locked_lines = db.session.execute(select(PurchaseOrderLine).where(
            PurchaseOrderLine.purchase_order_id == order.id).order_by(PurchaseOrderLine.id)
            .with_for_update().execution_options(populate_existing=True)).scalars().all()
        set_committed_value(order, "lines", locked_lines)
        db.session.expire(order, ["receptions"])
    return order


def lock_products(ids):
    """Products by id, locked in ascending order: the single product lock order of purchasing."""
    rows = db.session.execute(select(Product).where(Product.id.in_(sorted(ids))).order_by(Product.id)
                              .with_for_update().execution_options(populate_existing=True)).scalars()
    return {product.id: product for product in rows}


def _supplier(value):
    supplier = db.session.execute(select(Supplier).where(Supplier.id == integer(value, "Proveedor")).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if supplier is None or not supplier.is_active or supplier.approval_status != "approved":
        raise ValidationError("Seleccione un proveedor aprobado y activo.")
    return supplier


def _items(items):
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValidationError("Agregue entre 1 y 100 productos.")
    result, seen = [], set()
    for item in items:
        only(item, ("product_id", "quantity", "unit_cost"))
        product_id = integer(item.get("product_id"), "Producto")
        if product_id in seen:
            raise ValidationError("No se permiten productos duplicados; ajuste la cantidad del renglón existente.")
        seen.add(product_id)
        quantity = integer(item.get("quantity"), "Cantidad")
        cost = money(item.get("unit_cost"))
        if cost * quantity > MAX_MONEY:
            raise ValidationError("El importe del renglón supera el máximo permitido.")
        result.append((product_id, quantity, cost))
    products = lock_products(seen)
    lines = []
    for product_id, quantity, cost in result:
        product = products.get(product_id)
        if product is None or not product.is_active or product.approval_status != "approved":
            raise ValidationError("Todos los productos deben existir, estar aprobados y activos.")
        if getattr(product, "is_service", False):
            raise ValidationError("Los servicios no generan \u00f3rdenes de compra de stock.")
        lines.append(PurchaseOrderLine(product_id=product_id, product_code=product.code, product_name=product.name,
                                       quantity_ordered=quantity, quantity_received=0, unit_cost=cost, line_total=quantity * cost))
    if sum(line.line_total for line in lines) > MAX_MONEY:
        raise ValidationError("El total supera el máximo permitido.")
    return lines


def _apply_pending(order, data):
    order.supplier = _supplier(data.get("supplier_id"))
    new_lines = _items(data.get("items"))
    # Preserve existing IDs where possible; receipt history can never reference a deleted line.
    existing = {line.product_id: line for line in order.lines}
    merged = []
    for line in new_lines:
        target = existing.get(line.product_id)
        if target is not None:
            for key in ("quantity_ordered", "unit_cost", "line_total", "product_code", "product_name"):
                setattr(target, key, getattr(line, key))
            merged.append(target)
        else:
            merged.append(line)
    order.lines = merged
    order.subtotal = order.total = sum(line.line_total for line in merged)


def _metadata(order, data):
    if "estimated_delivery_date" in data:
        order.estimated_delivery_date = day(data["estimated_delivery_date"], "Entrega estimada", optional=True)
    if "notes" in data:
        order.notes = text_value(data["notes"], "Notas", 1000)


def _notify(order, action):
    # Business commit already succeeded. Channel/provider failures must not invite a duplicate retry.
    notification_service._safe(notification_service.emit, "replenishment", f"Orden de compra {action}",
                               f"{order.order_number}: {order.supplier.name} · {STATUS_LABELS[order.status]}",
                               permission="purchases:read", entity_type="purchase_order", entity_id=order.id,
                               href=f"/purchases?detail={order.id}", dedupe_key=None)


def _priced_from_quote(data):
    from app.services.supplier_quote_service import usable_for_order

    supplier_id = integer(data.get("supplier_id"), "Proveedor")
    # Supplier before quote, as approval and quote replacement do; a shared quote cannot cross locks.
    _supplier(supplier_id)
    quote = usable_for_order(data.get("supplier_quote_id"), supplier_id)
    prices = {line.product_id: line.unit_price for line in quote.lines}
    quantities = {line.product_id: line.quantity for line in quote.lines}
    items = []
    sent = data.get("items")
    if not isinstance(sent, list) or not 1 <= len(sent) <= 100:
        raise ValidationError("Agregue entre 1 y 100 productos.")
    for item in sent:
        only(item, ("product_id", "quantity", "unit_cost"))
        product_id = integer(item.get("product_id"), "Producto")
        if product_id not in prices:
            raise ValidationError("El producto no está en la cotización vigente.")
        if integer(item.get("quantity"), "Cantidad") != quantities[product_id]:
            raise ValidationError("La cantidad pedida debe coincidir con la cantidad cotizada de cada producto.")
        items.append({"product_id": product_id, "quantity": item.get("quantity"),
                      "unit_cost": f"{prices[product_id]:.2f}"})
    return {**data, "supplier_id": supplier_id, "items": items}, quote


def create_order(data, commit=True):
    require_operational_permission("purchases:write")
    only(data, ("supplier_id", "items", "estimated_delivery_date", "notes",
                "supplier_quote_id", "replenishment_request_id"))
    if not data.get("replenishment_request_id"):
        raise ConflictError("Toda orden de compra nace de una solicitud de reposición en proceso. "
                            "Prepárela desde la bandeja de solicitudes.")
    request_id = integer(data.get("replenishment_request_id"), "Solicitud")
    from app.services.replenishment_request_service import get_request, mark_converted
    try:
        row = get_request(request_id, lock=True)
        if row.status != "processing" or row.purchase_order_id:
            raise ConflictError("La solicitud no está en proceso o ya tiene una orden.")
        priced, quote = _priced_from_quote(data)
        order = PurchaseOrder(created_by_user_id=current_user.id, status="pending_approval",
                              supplier_quote_id=quote.id, currency=quote.currency)
        _apply_pending(order, priced)
        from app.services.product_supplier_service import require_active_associations
        require_active_associations(quote.supplier_id, (line.product_id for line in order.lines))
        from app.services.supplier_quote_service import eligibility_error
        reason = eligibility_error(quote)
        if reason:
            raise ValidationError(reason)
        _metadata(order, priced)
        db.session.add(order)
        db.session.flush()
        order.order_number = f"OC-{order.id:06d}"
        mark_converted(request_id, order)
        if commit:
            db.session.commit()
    except Exception:
        if commit:
            db.session.rollback()
        raise
    if commit:
        _notify(order, "creada")
    return order


def update_order(order_id, data):
    from app.services.supplier_quote_service import usable_for_order

    require_operational_permission("purchases:write")
    only(data, ("supplier_id", "items", "estimated_delivery_date", "notes", "supplier_quote_id"))
    try:
        order = get_order(order_id, lock=True)
        if order.status not in ("pending_approval", "pending"):
            raise ConflictError("Una orden aprobada es inmutable. Anule, solicite el cierre o cree otra.")
        if order.replenishment_request_id and set(data).intersection(("supplier_id", "items")):
            raise ConflictError("La orden proviene de una solicitud: sus productos, cantidades y proveedor se conservan. "
                                "Para otro proveedor o moneda, anule el borrador y tramite una nueva solicitud.")
        if order.replenishment_request_id and "supplier_quote_id" in data:
            _renew_quote(order, data["supplier_quote_id"])
        elif "supplier_id" in data or "items" in data or "supplier_quote_id" in data:
            priced, quote = _priced_from_quote({**{"supplier_id": order.supplier_id, "items": [],
                                                   "supplier_quote_id": order.supplier_quote_id}, **data})
            if quote.currency != order.currency:
                raise ValidationError("La moneda de la orden se conserva. Cree otra orden para una moneda diferente.")
            order.supplier_quote_id = quote.id
            _apply_pending(order, priced)
        else:
            usable_for_order(order.supplier_quote_id, order.supplier_id)
        _metadata(order, data)
        db.session.commit()
        return order
    except Exception:
        db.session.rollback()
        raise


def _renew_quote(order, quote_id):
    """Otra cotización del mismo proveedor y moneda; productos y cantidades de la solicitud se conservan."""
    from app.services.supplier_quote_service import usable_for_order

    quote = usable_for_order(quote_id, order.supplier_id)
    if quote.currency != order.currency:
        raise ValidationError("La moneda del borrador se conserva. Para otra moneda, anule el borrador "
                              "y tramite una nueva solicitud.")
    prices = {line.product_id: line.unit_price for line in quote.lines}
    quantities = {line.product_id: line.quantity for line in quote.lines}
    if any(line.product_id not in prices for line in order.lines):
        raise ValidationError("La cotización no cubre todos los productos del borrador.")
    for line in order.lines:
        if quantities[line.product_id] != line.quantity_ordered:
            raise ValidationError("La nueva cotización debe conservar las cantidades del borrador.")
        line.unit_cost = prices[line.product_id]
        line.line_total = line.quantity_ordered * line.unit_cost
        if line.line_total > MAX_MONEY:
            raise ValidationError("El importe del renglón supera el máximo permitido.")
    total = sum(line.line_total for line in order.lines)
    if total > MAX_MONEY:
        raise ValidationError("El total supera el máximo permitido.")
    order.supplier_quote_id = quote.id
    order.subtotal = order.total = total


def cancel_order(order_id, data=None):
    data = data or {}
    only(data, ("reason",))
    reason = text_value(data.get("reason"), "Motivo de anulación", 1000, required=True)
    try:
        order = get_order(order_id, lock=True)
        if order.status not in ("pending_approval", "approved", "pending") or order.receptions or any(line.quantity_received for line in order.lines):
            raise ConflictError("Solo se puede anular una orden sin recepción.")
        order.status = "cancelled"
        order.closure_reason = reason
        order.closed_at = datetime.utcnow()
        order.closed_by_user_id = current_user.id
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    _notify(order, "cancelada")
    return order


def duplicate_order(order_id):
    """La ruta se conserva, pero ninguna compra nueva se crea sin solicitud interna."""
    require_operational_permission("purchases:write")
    get_order(order_id)
    raise ConflictError("Las órdenes de compra no se duplican. Para volver a comprar, registre una nueva "
                        "solicitud de reposición; si la orden cerró con faltante, solicite la reposición del faltante.")


def _receipt_items(order, data):
    items = data.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValidationError("Indique al menos una cantidad recibida.")
    lines, seen, result = {line.id: line for line in order.lines}, set(), []
    for item in items:
        only(item, ("line_id", "quantity"))
        line_id = integer(item.get("line_id"), "Renglón")
        quantity = integer(item.get("quantity"), "Cantidad recibida")
        line = lines.get(line_id)
        if line is None or line_id in seen:
            raise ValidationError("Renglón inválido o duplicado.")
        if quantity > line.quantity_ordered - line.quantity_received:
            raise ConflictError("La recepción supera la cantidad pendiente.")
        seen.add(line_id)
        result.append((line, quantity))
    return sorted(result, key=lambda item: item[0].product_id)


def _partial_closure_reason(order, items, data):
    """None if everything arrives; otherwise the definitive closure needs confirmation and a concrete reason."""
    arriving = {line.id: quantity for line, quantity in items}
    if all(line.quantity_received + arriving.get(line.id, 0) == line.quantity_ordered for line in order.lines):
        return None
    if data.get("confirm_partial_closure") is not True:
        raise ValidationError("Recepción parcial: confirme que la orden se cierra definitivamente con el faltante. "
                              "No se registró la recepción.")
    reason = data.get("closure_reason")
    if not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 1000:
        raise ValidationError("Recepción parcial: indique el motivo concreto del faltante (entre 10 y 1000 caracteres). "
                              "No se registró la recepción.")
    return reason.strip()


def receive_order(order_id, data):
    require_operational_permission("purchases:receive")
    only(data, ("items", "received_at", "notes", "closure_reason", "confirm_partial_closure"))
    try:
        order = get_order(order_id, lock=True)
        if order.status != "approved" or order.receptions:
            raise ConflictError("Solo se recibe una vez una orden aprobada y pendiente de recepción.")
        received_at = day(data.get("received_at"), "Fecha de recepción")
        today = datetime.now(inventory_service.COMMERCIAL_ZONE).date()
        if not order.approved_at:
            raise ConflictError("La orden no tiene fecha de aprobación registrada; requiere revisión administrativa.")
        if received_at > today or received_at < order.approved_date:
            raise ValidationError("La recepción debe estar entre la fecha de aprobación de la orden y hoy.")
        items = _receipt_items(order, data)
        # Before any reception row or stock movement exists.
        partial_reason = _partial_closure_reason(order, items, data)
        reception = PurchaseReception(order=order, received_by_user_id=current_user.id, received_at=received_at,
                                       notes=text_value(data.get("notes", ""), "Notas", 1000))
        db.session.add(reception)
        db.session.flush()
        for line, quantity in items:
            movement = inventory_service.register_entry({"product_id": line.product_id, "quantity": quantity,
                                                         "reason": f"Recepción {reception.id} · {order.order_number}"}, commit=False)
            line.quantity_received += quantity
            db.session.add(PurchaseReceptionLine(reception_id=reception.id, purchase_order_line_id=line.id,
                                                 quantity=quantity, stock_movement_id=movement.id))
        for line in order.lines:
            line.quantity_not_supplied = line.quantity_ordered - line.quantity_received
        order.status = "closed_partial" if partial_reason else "closed_complete"
        order.closed_at = datetime.utcnow()
        order.closed_by_user_id = current_user.id
        order.closure_reason = partial_reason or "Recepción completa."
        _cancel_closure_on_receipt(order)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    _notify(order, "cerrada completa" if order.status == "closed_complete" else "cerrada parcial")
    return order


def _cancel_closure_on_receipt(order):
    """La recepción ganó el lock; el cierre solicitado queda sin efecto, sin decisión administrativa."""
    from app.models.purchase_closure import PurchaseClosureRequest

    pending = (PurchaseClosureRequest.query.filter_by(purchase_order_id=order.id, status="pending")
               .populate_existing().with_for_update().all())
    for row in pending:
        row.status = "cancelled"
        row.decided_at = order.closed_at
        row.decision_note = "Solicitud sin efecto: se registró la recepción de la orden antes de la decisión administrativa."


def request_closure(order_id, data):
    """El asistente solicita el cierre. La orden sigue aprobada hasta que el administrador decide."""
    from app.models.purchase_closure import CLOSURE_KINDS, PurchaseClosureRequest

    require_operational_permission("purchases:close_request")
    only(data, ("kind", "reason"))
    kind = data.get("kind")
    if kind not in CLOSURE_KINDS:
        raise ValidationError("Indique cambio de precio o falta de disponibilidad.")
    reason = text_value(data.get("reason"), "Motivo", 1000, required=True)
    try:
        order = get_order(order_id, lock=True)
        if order.status != "approved" or order.receptions:
            raise ConflictError("Solo una orden aprobada y sin recepción admite cierre comercial.")
        # Current read: a waiting transaction may have an older REPEATABLE READ snapshot.
        pending = (PurchaseClosureRequest.query.filter_by(purchase_order_id=order.id, status="pending")
                   .populate_existing().with_for_update().first())
        if pending is not None:
            raise ConflictError("Ya hay un cierre pendiente de aprobación.")
        row = PurchaseClosureRequest(purchase_order_id=order.id, kind=kind, reason=reason,
                                     requested_by_user_id=current_user.id)
        db.session.add(row)
        db.session.commit()
        return order
    except Exception:
        db.session.rollback()
        raise


def decide_closure(order_id, data, *, approve):
    from app.models.purchase_closure import PurchaseClosureRequest

    require_operational_permission("purchases:close_approve")
    only(data, () if approve else ("reason",))
    try:
        order = get_order(order_id, lock=True)
        row = (PurchaseClosureRequest.query.filter_by(purchase_order_id=order.id, status="pending")
               .populate_existing().with_for_update().first())
        if row is None:
            raise ConflictError("No hay un cierre comercial pendiente.")
        if row.requested_by_user_id == current_user.id:
            raise ValidationError("Quien solicita el cierre no puede aprobarlo ni rechazarlo.", status_code=403)
        if order.status != "approved" or order.receptions:
            raise ConflictError("La orden ya no admite esta decisión.")
        row.decided_by_user_id = current_user.id
        row.decided_at = datetime.utcnow()
        if approve:
            row.status = "approved"
            order.status = "closed_price_change" if row.kind == "price_change" else "closed_unavailable"
            order.closure_reason = row.reason
            order.closed_at = row.decided_at
            order.closed_by_user_id = current_user.id
            for line in order.lines:
                line.quantity_not_supplied = line.quantity_ordered - line.quantity_received
        else:
            row.status = "rejected"
            row.decision_note = text_value(data.get("reason"), "Motivo", 1000, required=True)
        db.session.commit()
        return order
    except Exception:
        db.session.rollback()
        raise


def create_supplier(_data):
    raise ValidationError(
        "El proveedor se propone en el módulo de proveedores y queda pendiente de aprobación. "
        "No se crea desde la orden de compra.",
        status_code=403,
    )


def _revalidate_for_approval(order):
    """Approval re-reads what the draft relied on, under locks.

    Lock order after the order row: supplier, products by id, associations, quote. Every path that
    locks a quote (preparing, editing, quoting, replacing) locks its supplier first, so they all
    serialize on the supplier; products are always locked in ascending id order.
    """
    from app.services.product_supplier_service import require_active_associations
    from app.services.supplier_quote_service import order_mismatch, usable_for_order

    try:
        _supplier(order.supplier_id)
        ids = sorted(line.product_id for line in order.lines)
        products = lock_products(ids)
        if any(products.get(key) is None or not products[key].is_active or products[key].approval_status != "approved"
               or getattr(products[key], "is_service", False) for key in ids):
            raise ValidationError("Todos los productos deben seguir aprobados y activos.")
        require_active_associations(order.supplier_id, ids)
        if not order.supplier_quote_id:
            raise ValidationError("El borrador no tiene una cotización registrada.")
        quote = usable_for_order(order.supplier_quote_id, order.supplier_id)
        reason = order_mismatch(quote, order)
        if reason:
            raise ValidationError(reason)
    except (ValidationError, ConflictError) as error:
        raise ConflictError(f"No se puede aprobar el borrador: {error.message} "
                            "Renueve la cotización del borrador o anúlelo.") from None


def decide_order(order_id, data, *, approve):
    """Serialize decisions with reception/edit; actor and timestamps are server-owned."""
    require_operational_permission("purchases:approve")
    only(data, () if approve else ("reason",))
    try:
        order = get_order(order_id, lock=True)
        if order.created_by_user_id == current_user.id:
            raise ValidationError("El creador no puede aprobar ni rechazar su propia orden.", status_code=403)
        if order.status != "pending_approval":
            raise ConflictError("La orden ya no está pendiente de aprobación.")
        if approve:
            _revalidate_for_approval(order)
            order.status = "approved"
            order.approved_by_user_id = current_user.id
            order.approved_at = datetime.utcnow()
            order.supplier_snapshot = order.supplier_document()
        else:
            order.rejection_reason = text_value(data.get("reason"), "Motivo de rechazo", 1000, required=True)
            order.status = "rejected"
            order.rejected_by_user_id = current_user.id
            order.rejected_at = datetime.utcnow()
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    _notify(order, "aprobada" if approve else "rechazada")
    return order
