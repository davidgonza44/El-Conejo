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
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask_login import current_user

from app.extensions import db
from app.models import DeliveryNote, DeliveryNoteItem, Product
from app.models.delivery_note import (
    DELIVERY_NOTE_STATUSES,
    STATUS_CANCELLED,
    STATUS_ISSUED,
)
from app.services import inventory_service
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


# Límites de las columnas de delivery_notes y formatos aceptados para identificar al cliente.
_FIELD_LIMITS = {"customer_name": ("nombre del cliente", 150), "customer_document": ("documento", 30),
                 "customer_phone": ("teléfono", 30), "customer_address": ("dirección", 255)}
_DOCUMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\- ]{2,29}")
_PHONE_RE = re.compile(r"\+?[0-9][0-9 ()\-.]{6,29}")


def _customer_field(data: dict, field: str) -> str | None:
    label, limit = _FIELD_LIMITS[field]
    value = (data.get(field) or "")
    if not isinstance(value, str):
        raise ValidationError(f"El campo {label} debe ser texto.")
    value = value.strip()
    if len(value) > limit:
        raise ValidationError(f"El {label} admite como máximo {limit} caracteres.")
    if value and field == "customer_document" and not _DOCUMENT_RE.fullmatch(value):
        raise ValidationError("El documento solo admite letras, números, puntos y guiones (p. ej. V-12345678 o J-12345678-9).")
    if value and field == "customer_phone" and not _PHONE_RE.fullmatch(value):
        raise ValidationError("El teléfono solo admite dígitos, espacios, paréntesis, guiones y un + inicial (p. ej. 0414-1234567).")
    return value or None


def _item_integer(value, message: str) -> int:
    try:
        return inventory_service._integer(value, "item")
    except ValidationError:
        raise ValidationError(message) from None


def _shown_prices(items) -> dict[int, Decimal]:
    """Precio que el usuario vio por producto (opcional). Sirve para confirmar cambios de precio."""
    shown = {}
    for item in items:
        if item.get("unit_price") is None:
            continue
        try:
            shown[int(item["product_id"])] = Decimal(str(item["unit_price"])).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError("El precio mostrado de un item no es válido.") from None
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


@_atomic
def create_note(data: dict) -> DeliveryNote:
    _reject_movement_ids(data)
    if "created_by_user_id" in data:
        raise ValidationError(
            "El campo 'created_by_user_id' no se acepta en el body: la nota se "
            "registra a nombre del usuario autenticado."
        )

    customer_name = _customer_field(data, "customer_name")
    if not customer_name:
        raise ValidationError("El campo 'customer_name' es obligatorio.")
    customer = {field: _customer_field(data, field) for field in ("customer_document", "customer_phone", "customer_address")}

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
                            payload={"price_changes": changes})

    note = DeliveryNote(
        note_number="PENDIENTE",  # se reemplaza tras obtener el id
        customer_name=customer_name,
        customer_document=customer["customer_document"],
        customer_phone=customer["customer_phone"],
        customer_address=customer["customer_address"],
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
    db.session.flush()
    db.session.commit()
    _notify_stock(locked.values())
    return note
