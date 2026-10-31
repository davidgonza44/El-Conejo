"""Lógica de negocio de notas de entrega.

Reglas clave:
- Emitir una nota descuenta stock y crea movimientos de salida, todo en una
  sola transacción (si un producto falla, no se crea nada).
- Cancelar una nota devuelve el stock con movimientos de entrada, también de
  forma transaccional.
- Los items guardan copia de código, nombre y precio del producto para
  preservar el histórico de la nota.
"""
import re
import unicodedata
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask_login import current_user
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Customer, DeliveryNote, DeliveryNoteItem, DocumentSequence, Product
from app.models.delivery_note import (
    DELIVERY_NOTE_STATUSES,
    STATUS_CANCELLED,
    STATUS_ISSUED,
)
from app.models.document_sequence import DELIVERY_NOTE_SEQUENCE
from app.services import inventory_service
from app.services.customer_service import PHONE_MESSAGE, PHONE_RE
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.sales_common import utc_boundary


def _atomic(operation):
    """Rollback the complete service-owned transaction, including flush/commit failures."""
    @wraps(operation)
    def wrapped(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except Exception:
            db.session.rollback()
            raise
    return wrapped


def _reject_movement_ids(data):
    if {"issue_stock_movement_id", "reversal_stock_movement_id"}.intersection(data):
        raise ValidationError("Los vínculos de movimientos son asignados por el servidor.")


# Límites de las columnas de delivery_notes y formatos aceptados para identificar al cliente.
MAX_NOTE_AMOUNT = Decimal("9999999999.99")  # NUMERIC(12,2) de delivery_notes.total_amount
_FIELD_LIMITS = {"customer_name": ("nombre del cliente", 150), "customer_document": ("documento", 30),
                 "customer_phone": ("teléfono", 30), "customer_address": ("dirección", 255)}
_DOCUMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\- ]{2,29}")
_IDEMPOTENCY_RE = re.compile(r"[A-Za-z0-9\-]{8,64}")
_DEFAULT_PER_PAGE = 20
_MAX_PER_PAGE = 100


def _customer_field(data: dict, field: str) -> str | None:
    label, limit = _FIELD_LIMITS[field]
    value = (data.get(field) or "")
    if not isinstance(value, str):
        raise ValidationError(f"El campo {label} debe ser texto.")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValidationError(f"El {label} contiene caracteres de control no admitidos.")
    value = value.strip()
    if len(value) > limit:
        raise ValidationError(f"El {label} admite como máximo {limit} caracteres.")
    if value and field == "customer_document" and not _DOCUMENT_RE.fullmatch(value):
        raise ValidationError("El documento solo admite letras, números, puntos y guiones (p. ej. V-12345678 o J-12345678-9).")
    if value and field == "customer_phone" and not PHONE_RE.fullmatch(value):
        raise ValidationError(PHONE_MESSAGE)
    return value or None


def _linked_customer(value) -> Customer | None:
    """Cliente del directorio elegido en el formulario; la nota copia sus datos al emitirse."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError("El campo 'customer_id' debe ser un entero positivo.")
    customer = db.session.get(Customer, value)
    if customer is None:
        raise NotFoundError(f"El cliente con id {value} no existe.")
    if not customer.is_active:
        raise ConflictError(f"El cliente '{customer.name}' está inactivo; no admite nuevas notas.")
    return customer


def _note_customer(data: dict) -> tuple[Customer | None, str, dict]:
    """(cliente del directorio, nombre, documento/teléfono/dirección) que la nota guardará como copia."""
    linked = _linked_customer(data.get("customer_id"))
    name = linked.name if linked else _customer_field(data, "customer_name")
    if not name:
        raise ValidationError("El campo 'customer_name' es obligatorio.")
    fields = {field: _customer_field(data, field) for field in ("customer_document", "customer_phone", "customer_address")}
    if linked:  # copia los datos del directorio: una sola versión del cliente
        fields["customer_document"], fields["customer_phone"] = linked.tax_id, linked.phone
    return linked, name, fields


def _idempotency_key(data: dict) -> str | None:
    key = data.get("idempotency_key")
    if key is None:
        return None
    if not isinstance(key, str) or not _IDEMPOTENCY_RE.fullmatch(key.strip()):
        raise ValidationError("El campo 'idempotency_key' debe tener de 8 a 64 letras, números o guiones.")
    return key.strip()


def _replayed(key: str) -> DeliveryNote | None:
    """La misma clave devuelve la nota ya emitida por el mismo usuario; la de otro no revela datos."""
    existing = DeliveryNote.query.filter(DeliveryNote.idempotency_key == key).first()
    if existing is not None and existing.created_by_user_id != current_user.id:
        raise ConflictError("No se pudo emitir la nota de entrega.")
    return existing


def _next_note_number() -> str:
    """Número correlativo tomado con bloqueo en la transacción de la nota: sin huecos ni espera por 'PENDIENTE'."""
    sequence = (db.session.query(DocumentSequence).filter(DocumentSequence.name == DELIVERY_NOTE_SEQUENCE)
                .with_for_update().populate_existing().first())
    if sequence is None:  # bases creadas sin la migración 0010: continúa desde la última nota
        start = db.session.query(func.max(DeliveryNote.id)).scalar() or 0
        sequence = DocumentSequence(name=DELIVERY_NOTE_SEQUENCE, last_value=start)
        db.session.add(sequence)
    sequence.last_value += 1
    return f"NE-{sequence.last_value:06d}"


def _item_integer(value, message: str) -> int:
    try:
        return inventory_service._integer(value, "item")
    except ValidationError:
        raise ValidationError(message) from None


def _shown_prices(items) -> dict[int, Decimal]:
    """Precio que el usuario vio y confirmó por producto (obligatorio). Detecta cambios antes de emitir."""
    shown = {}
    for item in items:
        # expected_unit_price es el nombre canónico; unit_price se conserva para clientes existentes.
        value = item.get("expected_unit_price", item.get("unit_price"))
        if value is None:
            # Sin el precio confirmado no hay emisión: el cliente debe ver el importe antes de emitir.
            raise ValidationError("Cada producto requiere expected_unit_price: el precio mostrado al confirmar.")
        if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
            raise ValidationError("El precio mostrado de un item no es válido.")
        try:
            price = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ValidationError("El precio mostrado de un item no es válido.") from None
        # Un precio con más de dos decimales se rechaza; nunca se redondea en silencio.
        if not price.is_finite() or price < 0 or price > MAX_NOTE_AMOUNT or price != price.quantize(Decimal("0.01")):
            raise ValidationError("El precio mostrado de un item no es válido (hasta dos decimales).")
        shown[int(item["product_id"])] = price
    return shown


def _validate_items_payload(items) -> list[tuple[int, int]]:
    """Valida la lista de items y devuelve pares (product_id, quantity)."""
    if not isinstance(items, list) or len(items) == 0:
        raise ValidationError("El campo 'items' es obligatorio y debe tener al menos un producto.")

    parsed: list[tuple[int, int]] = []
    seen: set[int] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValidationError(f"El item #{index} debe ser un objeto JSON.")

        _reject_movement_ids(item)
        product_id = _item_integer(item.get("product_id"), f"El item #{index} requiere un 'product_id' entero positivo.")
        if product_id <= 0:
            raise ValidationError(f"El item #{index} requiere un 'product_id' entero positivo.")
        quantity = _item_integer(item.get("quantity"), f"El item #{index} requiere una 'quantity' entera.")
        if quantity <= 0:
            raise ValidationError(f"La cantidad del item #{index} debe ser mayor que 0.")

        if product_id in seen:
            raise ValidationError(
                f"El producto {product_id} aparece repetido en los items; "
                "consolide las cantidades en un solo item."
            )
        seen.add(product_id)
        parsed.append((product_id, quantity))

    return parsed


def create_note(data: dict) -> DeliveryNote:
    return issue_note(data)[0]


def issue_note(data: dict) -> tuple[DeliveryNote, bool]:
    """Devuelve ``(nota, creada)``; ``creada=False`` es un reintento con la misma clave (no emite otra)."""
    key = _idempotency_key(data)
    existing = _replayed(key) if key else None
    if existing is not None:
        return existing, False
    try:
        return _create_note(data, key), True
    except IntegrityError:
        winner = _replayed(key) if key else None  # otra petición con la misma clave confirmó primero
        if winner is None:
            raise
        return winner, False


@_atomic
def _create_note(data: dict, key: str | None) -> DeliveryNote:
    _reject_movement_ids(data)
    if "created_by_user_id" in data:
        raise ValidationError(
            "El campo 'created_by_user_id' no se acepta en el body: la nota se "
            "registra a nombre del usuario autenticado."
        )

    linked, customer_name, customer = _note_customer(data)

    parsed_items = _validate_items_payload(data.get("items"))
    shown_prices = _shown_prices(data.get("items"))

    # Valida y bloquea todos los productos ANTES de crear nada (transaccional).
    # El bloqueo sigue el orden de id para no chocar con otra nota o venta simultánea.
    locked = inventory_service.lock_products(product_id for product_id, _ in parsed_items)
    products: list[tuple[Product, int]] = []
    for product_id, quantity in parsed_items:
        product = locked.get(product_id)
        if product is None:
            raise NotFoundError(f"El producto con id {product_id} no existe.")
        if not product.is_active:
            raise ConflictError(
                f"El producto '{product.name}' está inactivo; no puede incluirse en la nota."
            )
        if quantity > product.current_stock:
            raise ConflictError(
                f"Stock insuficiente para '{product.name}': disponible "
                f"{product.current_stock}, solicitado {quantity}."
            )
        products.append((product, quantity))

    # The current document cannot persist its denomination. Until the separate
    # header migration is authorized, only explicitly USD catalog lines can use
    # its existing USD presentation. Reject before creating notes or movements.
    if any(product.sale_price_currency != "USD" for product, _ in products):
        raise ValidationError(
            "La nota de entrega solo admite precios USD explícitos mientras su moneda documental esté pendiente. "
            "No se mezclan monedas ni se convierten precios.")

    # Con las filas bloqueadas, el precio no puede cambiar entre esta verificación y la emisión.
    changes = [{"product_id": product.id, "code": product.code, "name": product.name,
                "shown": str(shown_prices[product.id]), "current": str(product.sale_price)}
               for product, _ in products
               if product.id in shown_prices and shown_prices[product.id] != product.sale_price]
    if changes:
        raise ConflictError("El precio de uno o más productos cambió. Revise y confirme los importes vigentes.",
                            payload={"code": "price_changed", "price_changes": changes})
    if sum((product.sale_price * quantity for product, quantity in products), Decimal(0)) > MAX_NOTE_AMOUNT:
        raise ValidationError("El importe supera el máximo admitido por la nota.")

    note = DeliveryNote(
        note_number=_next_note_number(),
        customer_id=linked.id if linked else None,
        idempotency_key=key,
        customer_name=customer_name,
        customer_document=customer["customer_document"],
        customer_phone=customer["customer_phone"],
        customer_address=customer["customer_address"],
        status=STATUS_ISSUED,
        total_amount=0,
        created_by_user_id=current_user.id,
    )
    db.session.add(note)
    db.session.flush()  # asigna note.id para las líneas

    total = 0
    for product, quantity in products:
        unit_price = product.sale_price
        line_total = unit_price * quantity
        total += line_total

        # Único camino del stock: valida, crea la salida y descuenta en esta transacción.
        movement = inventory_service.register_exit(
            {"product_id": product.id, "quantity": quantity, "reason": f"Nota de entrega #{note.note_number}"},
            commit=False,
        )
        db.session.add(DeliveryNoteItem(
            delivery_note_id=note.id,
            product_id=product.id,
            product_code=product.code,
            product_name=product.name,
            quantity=quantity,
            unit_price=unit_price,
            line_total=line_total,
            issue_stock_movement=movement,
        ))

    note.total_amount = total
    db.session.commit()
    _notify_stock(product for product, _ in products)
    return note


def _notify_stock(products) -> None:
    """Tras confirmar: aviso de stock bajo, agotado o crítico; nunca rompe la operación."""
    from app.services import notification_service

    for product in products:
        notification_service.product_stock_changed(product)


def get_note_or_404(note_id: int) -> DeliveryNote:
    note = db.session.get(DeliveryNote, note_id)
    if note is None:
        raise NotFoundError(f"La nota de entrega con id {note_id} no existe.")
    return note


def _positive_param(value, field: str) -> int:
    text = str(value).strip()
    if not text.isdigit() or int(text) < 1 or len(text) > 10:
        raise ValidationError(f"El parámetro '{field}' debe ser un entero positivo.")
    return int(text)


def _filtered_query(
    status: str | None = None,
    customer_name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    customer_id=None,
):
    query = DeliveryNote.query

    if customer_id not in (None, ""):
        query = query.filter(DeliveryNote.customer_id == _positive_param(customer_id, "customer_id"))

    if status:
        status = status.strip().lower()
        if status not in DELIVERY_NOTE_STATUSES:
            raise ValidationError(
                f"Estado inválido: '{status}'. Valores permitidos: "
                f"{', '.join(DELIVERY_NOTE_STATUSES)}."
            )
        query = query.filter(DeliveryNote.status == status)

    if customer_name:
        query = query.filter(DeliveryNote.customer_name.ilike(f"%{customer_name.strip()}%"))

    def _parse_date(value: str, field: str):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            raise ValidationError(f"El parámetro '{field}' debe tener formato YYYY-MM-DD.")

    # created_at se guarda en UTC; los días del filtro son días comerciales de Caracas.
    if date_from:
        query = query.filter(DeliveryNote.created_at >= utc_boundary(_parse_date(date_from, "date_from")))
    if date_to:
        # Inclusivo: hasta el final del día indicado.
        query = query.filter(
            DeliveryNote.created_at < utc_boundary(_parse_date(date_to, "date_to") + timedelta(days=1))
        )
    return query


def _newest_first(query):
    return query.order_by(DeliveryNote.created_at.desc(), DeliveryNote.id.desc())


def list_notes(
    status: str | None = None,
    customer_name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    customer_id=None,
) -> list[DeliveryNote]:
    query = _filtered_query(status, customer_name, date_from, date_to, customer_id)
    return _newest_first(query).options(joinedload(DeliveryNote.creator)).all()


def page_notes(filters: dict, page=None, per_page=None) -> dict:
    """Una página del listado más los totales de todas las notas que cumplen los filtros."""
    query = _filtered_query(**filters)
    page = 1 if page in (None, "") else _positive_param(page, "page")
    per_page = _DEFAULT_PER_PAGE if per_page in (None, "") else _positive_param(per_page, "per_page")
    if per_page > _MAX_PER_PAGE:
        raise ValidationError(f"El parámetro 'per_page' debe estar entre 1 y {_MAX_PER_PAGE}.")
    by_status = {status: (count, amount) for status, count, amount in query.with_entities(
        DeliveryNote.status, func.count(DeliveryNote.id), func.coalesce(func.sum(DeliveryNote.total_amount), 0),
    ).group_by(DeliveryNote.status)}
    total = sum(count for count, _ in by_status.values())
    issued_count, issued_amount = by_status.get(STATUS_ISSUED, (0, 0))
    items = (_newest_first(query).options(joinedload(DeliveryNote.creator))
             .offset((page - 1) * per_page).limit(per_page).all())
    return {
        "items": items, "total": total, "page": page, "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
        "issued": issued_count, "cancelled": by_status.get(STATUS_CANCELLED, (0, 0))[0],
        "issued_amount": Decimal(issued_amount),
    }


def _cancellation_reason(value) -> str:
    if not isinstance(value, str) or not 5 <= len(value.strip()) <= 500:
        raise ValidationError("Indique el motivo de la cancelación (entre 5 y 500 caracteres).")
    return value.strip()


@_atomic
def cancel_note(note_id: int, reason) -> DeliveryNote:
    reason = _cancellation_reason(reason)
    # Lock the document before checking status; refresh stale identity-map state.
    note = (db.session.query(DeliveryNote).filter(DeliveryNote.id == note_id)
            .with_for_update().populate_existing().first())
    if note is None:
        raise NotFoundError(f"La nota de entrega con id {note_id} no existe.")

    if note.status != STATUS_ISSUED:
        raise ConflictError(f"La nota {note.note_number} ya está cancelada.")

    # Devuelve el stock de cada item con un movimiento de entrada (transaccional).
    locked = inventory_service.lock_products(item.product_id for item in note.items)
    for item in note.items:
        if item.reversal_stock_movement_id is not None:
            raise ConflictError("La nota contiene una reversión previa inconsistente.")
        if item.product_id not in locked:
            raise ConflictError(
                f"No se puede cancelar: el producto id {item.product_id} ya no existe."
            )
        # quantity se guarda como DECIMAL(12,2); el stock es entero y no se trunca en silencio.
        if item.quantity != int(item.quantity):
            raise ConflictError(f"La cantidad del producto {item.product_code} no es entera; revise la nota.")
        # La mercancía vuelve aunque el producto se haya desactivado después de emitir la nota.
        item.reversal_stock_movement = inventory_service.register_entry(
            {"product_id": item.product_id, "quantity": int(item.quantity),
             "reason": f"Cancelación de nota de entrega #{note.note_number}"},
            commit=False, allow_inactive=True,
        )

    note.status = STATUS_CANCELLED
    note.cancelled_by_user_id = current_user.id
    note.cancelled_at = datetime.utcnow()
    note.cancellation_reason = reason
    db.session.flush()
    db.session.commit()
    _notify_stock(locked.values())
    return note
