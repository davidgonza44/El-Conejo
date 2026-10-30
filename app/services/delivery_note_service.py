"""Lógica de negocio de notas de entrega.

Reglas clave:
- Emitir una nota descuenta stock y crea movimientos de salida, todo en una
  sola transacción (si un producto falla, no se crea nada).
- Cancelar una nota devuelve el stock con movimientos de entrada, también de
  forma transaccional.
- Los items guardan copia de código, nombre y precio del producto para
  preservar el histórico de la nota.
"""
from datetime import datetime, timedelta
from functools import wraps

from flask_login import current_user

from app.extensions import db
from app.models import DeliveryNote, DeliveryNoteItem, Product, StockMovement
from app.models.delivery_note import (
    DELIVERY_NOTE_STATUSES,
    STATUS_CANCELLED,
    STATUS_ISSUED,
)
from app.models.stock_movement import MOVEMENT_ENTRADA, MOVEMENT_SALIDA
from app.services.exceptions import ConflictError, NotFoundError, ValidationError


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
        try:
            product_id = int(item.get("product_id"))
        except (TypeError, ValueError):
            raise ValidationError(f"El item #{index} requiere un 'product_id' entero.")

        try:
            quantity = int(item.get("quantity"))
        except (TypeError, ValueError):
            raise ValidationError(f"El item #{index} requiere una 'quantity' entera.")
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


def _get_locked_product(product_id: int) -> Product:
    """Bloquea la fila del producto para descontar stock sin carreras."""
    return (
        db.session.query(Product)
        .filter(Product.id == product_id)
        .with_for_update()
        .populate_existing()
        .first()
    )


@_atomic
def create_note(data: dict) -> DeliveryNote:
    _reject_movement_ids(data)
    if "created_by_user_id" in data:
        raise ValidationError(
            "El campo 'created_by_user_id' no se acepta en el body: la nota se "
            "registra a nombre del usuario autenticado."
        )

    customer_name = (data.get("customer_name") or "").strip()
    if not customer_name:
        raise ValidationError("El campo 'customer_name' es obligatorio.")

    parsed_items = _validate_items_payload(data.get("items"))

    # Valida y bloquea todos los productos ANTES de crear nada (transaccional).
    products: list[tuple[Product, int]] = []
    for product_id, quantity in parsed_items:
        product = _get_locked_product(product_id)
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

    note = DeliveryNote(
        note_number="PENDIENTE",  # se reemplaza tras obtener el id
        customer_name=customer_name,
        customer_document=(data.get("customer_document") or "").strip() or None,
        customer_phone=(data.get("customer_phone") or "").strip() or None,
        customer_address=(data.get("customer_address") or "").strip() or None,
        status=STATUS_ISSUED,
        total_amount=0,
        created_by_user_id=current_user.id,
    )
    db.session.add(note)
    db.session.flush()  # asigna note.id; el número deriva de él (único garantizado)
    note.note_number = f"NE-{note.id:06d}"

    total = 0
    for product, quantity in products:
        unit_price = product.sale_price
        line_total = unit_price * quantity
        total += line_total

        movement = StockMovement(
            product_id=product.id,
            movement_type=MOVEMENT_SALIDA,
            quantity=quantity,
            previous_stock=product.current_stock,
            new_stock=product.current_stock - quantity,
            reason=f"Nota de entrega #{note.note_number}",
            user_id=current_user.id,
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
        product.current_stock -= quantity

    note.total_amount = total
    db.session.commit()
    return note


def get_note_or_404(note_id: int) -> DeliveryNote:
    note = db.session.get(DeliveryNote, note_id)
    if note is None:
        raise NotFoundError(f"La nota de entrega con id {note_id} no existe.")
    return note


def list_notes(
    status: str | None = None,
    customer_name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[DeliveryNote]:
    query = DeliveryNote.query

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

    def _parse_date(value: str, field: str) -> datetime:
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValidationError(f"El parámetro '{field}' debe tener formato YYYY-MM-DD.")

    if date_from:
        query = query.filter(DeliveryNote.created_at >= _parse_date(date_from, "date_from"))
    if date_to:
        # Inclusivo: hasta el final del día indicado.
        query = query.filter(
            DeliveryNote.created_at < _parse_date(date_to, "date_to") + timedelta(days=1)
        )

    return query.order_by(DeliveryNote.created_at.desc(), DeliveryNote.id.desc()).all()


@_atomic
def cancel_note(note_id: int) -> DeliveryNote:
    # Lock the document before checking status; refresh stale identity-map state.
    note = (db.session.query(DeliveryNote).filter(DeliveryNote.id == note_id)
            .with_for_update().populate_existing().first())
    if note is None:
        raise NotFoundError(f"La nota de entrega con id {note_id} no existe.")

    if note.status != STATUS_ISSUED:
        raise ConflictError(f"La nota {note.note_number} ya está cancelada.")

    # Devuelve el stock de cada item con un movimiento de entrada (transaccional).
    for item in note.items:
        if item.reversal_stock_movement_id is not None:
            raise ConflictError("La nota contiene una reversión previa inconsistente.")
        product = _get_locked_product(item.product_id)
        if product is None:
            raise ConflictError(
                f"No se puede cancelar: el producto id {item.product_id} ya no existe."
            )
        # quantity se guarda como DECIMAL(12,2); el stock actual es entero.
        quantity = int(item.quantity)
        item.reversal_stock_movement = StockMovement(
                product_id=product.id,
                movement_type=MOVEMENT_ENTRADA,
                quantity=quantity,
                previous_stock=product.current_stock,
                new_stock=product.current_stock + quantity,
                reason=f"Cancelación de nota de entrega #{note.note_number}",
                user_id=current_user.id,
        )
        product.current_stock += quantity

    note.status = STATUS_CANCELLED
    note.cancelled_by_user_id = current_user.id
    note.cancelled_at = datetime.utcnow()
    db.session.flush()
    db.session.commit()
    return note
