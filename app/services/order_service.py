"""Pedidos (Orders Core v1).

Un pedido es un compromiso comercial: crear, editar, duplicar o cambiar su estado (operativo o
de pago) NO descuenta ni reserva stock, NO crea una venta ni StockMovement (limitación
explícita de v1; la conversión pedido -> venta queda fuera). Secuencia de ``create_order``:
rechazar campos autoritativos -> validar cliente/entrega/estado/metadatos/renglones -> insertar
con ``order_number`` NULL, ``flush`` y fijar ``PED-{id:06d}`` -> renglones con precio copiado ->
totales Decimal -> un único commit. Transiciones: pending -> processing|cancelled;
processing -> completed|cancelled; sin retrocesos.

Política de edición (KISS):
- pending: todo editable (cliente, renglones, entrega y metadatos);
- processing: solo entrega y metadatos (prioridad, pago, contacto, dirección, notas);
- completed / cancelled: sin ediciones (409).
El estado de pago (pending/paid) es independiente del operativo: completar no marca pagado.
"""
from decimal import Decimal

from sqlalchemy import func, select

from app.extensions import db
from app.models import Customer, Order, OrderLine
from app.models.order import (CONTACT_PHONE_MAX, CREATE_STATUSES, DELIVERY_ADDRESS_MAX, NOTES_MAX, NUMBER_PREFIX,
                              ORDER_STATUSES, PAYMENT_LABELS, PAYMENT_PENDING, PAYMENT_STATUSES, PRIORITIES,
                              PRIORITY_LABELS, PRIORITY_MEDIUM, STATUS_LABELS, STATUS_PENDING, STATUS_PROCESSING,
                              TRANSITIONS)
from app.services import sales_common as common
from app.services.exceptions import ConflictError, NotFoundError, ValidationError

_FORBIDDEN_FIELDS = {"seller_id", "order_number", "ordered_at", "subtotal", "total"}
_METADATA_FIELDS = {"priority", "payment_status", "contact_phone", "delivery_address", "notes"}
_UPDATE_FIELDS = _METADATA_FIELDS | {"estimated_delivery_date", "customer_id", "items"}
_STRUCTURAL_FIELDS = {"customer_id", "items"}
_UNITS = select(func.coalesce(func.sum(OrderLine.quantity), 0)).where(OrderLine.order_id == Order.id).scalar_subquery()
_SORT_COLUMNS = {"date": Order.ordered_at, "number": Order.id, "total": Order.total, "delivery": Order.estimated_delivery_date,
                 "customer": Customer.name, "status": Order.status, "units": _UNITS, "priority": Order.priority,
                 "payment_status": Order.payment_status}


def _delivery_date(data: dict, today):
    day = common.parse_date(data.get("estimated_delivery_date"), "estimated_delivery_date")
    if day < today:
        raise ValidationError("La entrega estimada no puede ser anterior a la fecha del pedido.")
    return day


def _metadata(data: dict, current: Order | None = None) -> dict:
    """Metadatos validados; en edición solo cambian las claves presentes en ``data``."""
    values = {}
    if current is None or "priority" in data:
        values["priority"] = common.choice(data, "priority", PRIORITIES, default=PRIORITY_MEDIUM)
    if current is None or "payment_status" in data:
        values["payment_status"] = common.choice(data, "payment_status", PAYMENT_STATUSES, default=PAYMENT_PENDING)
    for field, limit in (("contact_phone", CONTACT_PHONE_MAX), ("delivery_address", DELIVERY_ADDRESS_MAX), ("notes", NOTES_MAX)):
        if current is None or field in data:
            values[field] = common.optional_text(data, field, limit)
    return values


def _set_lines(order: Order, entries) -> None:
    for line in list(order.lines):
        db.session.delete(line)
    db.session.flush()
    lines = [common.snapshot_line(OrderLine, product, quantity, order_id=order.id) for product, quantity in entries]
    db.session.add_all(lines)
    order.lines = lines
    order.subtotal = common.quantize(sum((line.line_subtotal for line in lines), Decimal(0)))
    order.total = order.subtotal  # v1: sin impuestos ni descuentos de pedido


def _commit():
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def create_order(data: dict) -> Order:
    common.reject(data, _FORBIDDEN_FIELDS, "el pedido")
    customer = common.resolve_customer(data)
    today = common.commercial_today()
    delivery = _delivery_date(data, today)
    status = common.choice(data, "status", CREATE_STATUSES, default=STATUS_PENDING)
    metadata = _metadata(data)
    entries = common.parse_lines(data)
    try:
        order = Order(customer_id=customer.id, seller_id=common.seller_id(), ordered_at=common.utc_now(),
                      estimated_delivery_date=delivery, status=status, **metadata)
        common.assign_number(order, "order_number", NUMBER_PREFIX)
        _set_lines(order, entries)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    _notify(order, f"Nuevo pedido {order.order_number}", f"Pedido de {order.customer.name} por {order.total:.2f}.")
    return order


def _notify(order: Order, title: str, message: str) -> None:
    from app.services import notification_service

    notification_service.order_event(order, title, message)


def get_order(order_number: str) -> Order:
    order = Order.query.filter(Order.order_number == order_number).first()
    if order is None:
        raise NotFoundError(f"El pedido {order_number} no existe.")
    return order


def update_order(order_number: str, data: dict) -> Order:
    order = get_order(order_number)
    unknown = set(data) - _UPDATE_FIELDS
    if unknown:
        raise ValidationError(f"Campos no editables o desconocidos: {', '.join(sorted(unknown))}.")
    if not data:
        raise ValidationError("No se indicó ningún campo a editar.")
    if order.status not in (STATUS_PENDING, STATUS_PROCESSING):
        raise ConflictError(f"El pedido {order_number} está '{STATUS_LABELS[order.status]}' y no admite ediciones.")
    structural = _STRUCTURAL_FIELDS & set(data)
    if structural and order.status != STATUS_PENDING:
        raise ConflictError(f"El pedido {order_number} está 'En proceso': cliente y productos ya no pueden cambiar.")
    metadata = _metadata(data, current=order)
    delivery = _delivery_date(data, common.commercial_today()) if "estimated_delivery_date" in data else None
    customer = common.resolve_customer(data) if "customer_id" in data else None
    entries = common.parse_lines(data) if "items" in data else None
    for field, value in metadata.items():
        setattr(order, field, value)
    if delivery is not None:
        order.estimated_delivery_date = delivery
    if customer is not None:
        order.customer_id = customer.id
    if entries is not None:
        _set_lines(order, entries)
    _commit()
    return order


def change_status(order_number: str, data: dict) -> Order:
    order = get_order(order_number)
    new_status = common.choice(data, "status", ORDER_STATUSES)
    if new_status not in TRANSITIONS[order.status]:
        raise ConflictError(
            f"El pedido {order_number} está '{STATUS_LABELS[order.status]}' y no puede pasar a "
            f"'{STATUS_LABELS[new_status]}'."
        )
    order.status = new_status
    _commit()
    _notify(order, f"Pedido {order_number}: {STATUS_LABELS[new_status]}",
            f"El pedido de {order.customer.name} cambió a '{STATUS_LABELS[new_status]}'.")
    return order


def change_payment_status(order_number: str, data: dict) -> Order:
    """pending <-> paid, sin restricción por estado operativo (un pedido completado puede seguir pendiente de pago)."""
    order = get_order(order_number)
    order.payment_status = common.choice(data, "payment_status", PAYMENT_STATUSES)
    _commit()
    _notify(order, f"Pedido {order_number}: pago {PAYMENT_LABELS[order.payment_status].lower()}",
            f"El estado de pago del pedido de {order.customer.name} es '{PAYMENT_LABELS[order.payment_status]}'.")
    return order


def duplicate_order(order_number: str) -> Order:
    """Nuevo PED con el mismo cliente, renglones (precios vigentes) y metadatos; pago y estado vuelven a pendiente."""
    source = get_order(order_number)
    today = common.commercial_today()
    entries = common.parse_lines({"items": [{"product_id": line.product_id, "quantity": line.quantity} for line in source.lines]})
    if not source.customer.is_active:
        raise ConflictError(f"El cliente '{source.customer.name}' está inactivo; no puede duplicarse el pedido.")
    try:
        order = Order(customer_id=source.customer_id, seller_id=common.seller_id(), ordered_at=common.utc_now(),
                      estimated_delivery_date=max(source.estimated_delivery_date, today), status=STATUS_PENDING,
                      priority=source.priority, payment_status=PAYMENT_PENDING, contact_phone=source.contact_phone,
                      delivery_address=source.delivery_address, notes=source.notes)
        common.assign_number(order, "order_number", NUMBER_PREFIX)
        _set_lines(order, entries)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return order


def list_orders(params: dict) -> dict:
    query = Order.query.join(Customer, Order.customer_id == Customer.id)
    search = (params.get("search") or "").strip()
    if search:
        query = common.apply_search(query, Order, OrderLine, OrderLine.order_id, Order.order_number, search)
    for key, column, allowed in (("status", Order.status, ORDER_STATUSES), ("priority", Order.priority, PRIORITIES),
                                 ("payment_status", Order.payment_status, PAYMENT_STATUSES)):
        value = (params.get(key) or "").strip().lower()
        if value:
            if value not in allowed:
                raise ValidationError(f"'{key}' inválido. Valores permitidos: {', '.join(allowed)}.")
            query = query.filter(column == value)
    query = common.apply_datetime_range(query, Order.ordered_at, params, "date_from", "date_to")
    query = common.apply_date_range(query, Order.estimated_delivery_date, params, "delivery_from", "delivery_to")
    query = common.apply_id_filters(query, Order, OrderLine, OrderLine.order_id, params)
    low, high = common.int_param(params, "units_min", 0), common.int_param(params, "units_max", 0)
    if low is not None and high is not None and low > high:
        raise ValidationError("'units_min' no puede superar a 'units_max'.")
    if low is not None:
        query = query.filter(_UNITS >= low)
    if high is not None:
        query = query.filter(_UNITS <= high)
    query = query.order_by(common.order_clause(params, _SORT_COLUMNS, "-date"), Order.id.desc())
    return common.paginate(query, params, Order, OrderLine, Order.to_summary_dict)


def order_options() -> dict:
    return {
        "statuses": [{"value": k, "label": STATUS_LABELS[k]} for k in ORDER_STATUSES],
        "create_statuses": [{"value": k, "label": STATUS_LABELS[k]} for k in CREATE_STATUSES],
        "priorities": [{"value": k, "label": PRIORITY_LABELS[k]} for k in PRIORITIES],
        "payment_statuses": [{"value": k, "label": PAYMENT_LABELS[k]} for k in PAYMENT_STATUSES],
        "sellers": common.sellers_of(Order),
        "today": common.commercial_today().isoformat(),
    }
