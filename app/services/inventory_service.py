"""Lógica de negocio de los movimientos de inventario.

Este módulo es el ÚNICO camino permitido para modificar el stock de un
producto (regla 1): cada cambio crea un registro en stock_movements y
actualiza current_stock dentro de la misma transacción.
"""
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from flask_login import current_user
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Product, StockMovement, User
from app.models.stock_movement import (
    MOVEMENT_AJUSTE,
    MOVEMENT_ENTRADA,
    MOVEMENT_SALIDA,
    MOVEMENT_TYPES,
)
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.movement_traceability_service import creation_fields, movement_presentations


# Límite de las columnas enteras de stock (INT con signo de MySQL/MariaDB).
MAX_STOCK = 2_147_483_647


def _get_active_product_locked(product_id, *, allow_inactive: bool = False) -> Product:
    """Obtiene el producto con bloqueo de fila para actualizar stock sin carreras."""
    # Solo enteros positivos: 15.5, true o "1e3" no identifican un producto.
    product_id = _integer(product_id, "product_id") if product_id is not None else None
    if product_id is None or not 1 <= product_id <= MAX_STOCK:
        raise ValidationError("El campo 'product_id' es obligatorio y debe ser un entero positivo.")

    product = (
        db.session.query(Product)
        .populate_existing()
        .filter(Product.id == product_id)
        .with_for_update()
        .first()
    )
    if product is None:
        raise NotFoundError(f"El producto con id {product_id} no existe.")
    if not product.is_active and not allow_inactive:
        raise ConflictError(
            f"El producto '{product.name}' está inactivo; no admite movimientos de inventario."
        )
    return product


def lock_products(product_ids) -> dict[int, Product]:
    """Bloquea varias filas en orden de id: dos documentos simultáneos no se bloquean entre sí."""
    ids = sorted(set(product_ids))
    if not ids:
        return {}
    rows = (db.session.query(Product).populate_existing().filter(Product.id.in_(ids))
            .order_by(Product.id).with_for_update().all())
    return {product.id: product for product in rows}


def _current_user_id(data: dict) -> int:
    """El user_id se toma SIEMPRE del usuario autenticado, nunca del body."""
    if "user_id" in data:
        raise ValidationError(
            "El campo 'user_id' no se acepta en el body: el movimiento se "
            "registra a nombre del usuario autenticado."
        )
    return current_user.id


def _positive_quantity(data: dict) -> int:
    """Regla 6: cantidades de entradas y salidas deben ser enteros mayores a cero."""
    quantity = _integer(data.get("quantity"), "quantity")
    if quantity <= 0:
        raise ValidationError("El campo 'quantity' debe ser mayor a cero.")
    return quantity


def _integer(value, field: str) -> int:
    if isinstance(value, bool) or not (
        isinstance(value, int)
        or isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]{1,12}", value.strip())
    ):
        raise ValidationError(f"El campo '{field}' debe ser un entero.")
    number = int(value)
    if abs(number) > MAX_STOCK:
        raise ValidationError(f"El campo '{field}' supera el máximo permitido ({MAX_STOCK:,}).".replace(",", "."))
    return number


def _within_stock_limit(new_stock: int) -> int:
    """El stock resultante debe caber en la columna; si no, error controlado y sin cambios."""
    if new_stock > MAX_STOCK:
        raise ValidationError("La operación dejaría un stock mayor al máximo permitido.")
    return new_stock


def _reason(data: dict, required: bool = False) -> str | None:
    value = data.get("reason", "")
    if not isinstance(value, str):
        raise ValidationError("El campo 'reason' debe ser texto.")
    if len(value) > 255:
        raise ValidationError("El motivo no puede superar 255 caracteres.")
    value = value.strip()
    if required and not value:
        raise ValidationError("El campo 'reason' (motivo) es obligatorio en los ajustes.")
    return value or None


def _create_movement(
    product: Product,
    movement_type: str,
    quantity: int,
    new_stock: int,
    reason: str | None,
    user_id: int | None,
    trace: dict,
) -> StockMovement:
    movement = StockMovement(
        product_id=product.id,
        movement_type=movement_type,
        quantity=quantity,
        previous_stock=product.current_stock,
        new_stock=new_stock,
        reason=reason,
        user_id=user_id,
        **trace,
    )
    product.current_stock = new_stock
    db.session.add(movement)
    db.session.flush()
    return movement


def _commit_owned(product: Product | None = None) -> None:
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    if product is not None:  # tras confirmar: evento real de stock bajo/agotado/crítico (nunca rompe el movimiento)
        from app.services import notification_service

        notification_service.product_stock_changed(product)


def register_entry(data: dict, commit: bool = True, *, creation_origin=None,
                   allow_inactive: bool = False) -> StockMovement:
    """Regla 2: una entrada aumenta el stock.

    ``commit=False`` deja la transacción al llamador (creación de producto).
    ``allow_inactive`` solo para reversiones de documentos: la mercancía vuelve aunque
    el producto se haya desactivado después.
    """
    try:
        trace = creation_fields(data, creation_origin)
        product = _get_active_product_locked(
            data.get("product_id"), allow_inactive=allow_inactive or creation_origin == "initial_stock")
        quantity = _positive_quantity(data)
        user_id = _current_user_id(data)
        reason = _reason(data)
        if product.is_service:
            raise ConflictError(f"'{product.name}' es un servicio: no lleva existencias.")

        new_stock = _within_stock_limit(product.current_stock + quantity)
        movement = _create_movement(
            product, MOVEMENT_ENTRADA, quantity, new_stock, reason, user_id, trace
        )
        if commit:
            _commit_owned(product)
        return movement
    except Exception:
        if commit:
            db.session.rollback()
        raise


def register_exit(data: dict, commit: bool = True, *, creation_origin=None) -> StockMovement:
    """Regla 3: una salida disminuye el stock. Regla 5: nunca por debajo de cero.

    ``commit=False`` deja la transacción al llamador (registro de una venta):
    bloquea la fila, valida y crea el movimiento, pero no confirma ni revierte.
    """
    try:
        trace = creation_fields(data, creation_origin)
        product = _get_active_product_locked(data.get("product_id"))
        quantity = _positive_quantity(data)
        user_id = _current_user_id(data)
        reason = _reason(data)

        if quantity > product.current_stock:
            raise ConflictError(
                f"Stock insuficiente para '{product.name}': disponible "
                f"{product.current_stock}, solicitado {quantity}."
            )

        new_stock = product.current_stock - quantity
        movement = _create_movement(
            product, MOVEMENT_SALIDA, quantity, new_stock, reason, user_id, trace
        )
        if commit:
            _commit_owned(product)
        return movement
    except Exception:
        if commit:
            db.session.rollback()
        raise


def register_adjustment(data: dict, commit: bool = True, *, creation_origin=None) -> StockMovement:
    """Primitiva interna: solo la aprobación de una solicitud de ajuste; nunca HTTP manual.

    El workflow valida solicitud, revisor y stock esperado antes de llamar.
    Un producto inactivo solo admite el ajuste a 0: así su stock nunca queda congelado.
    """
    try:
        trace = creation_fields(data, creation_origin)
        product = _get_active_product_locked(data.get("product_id"), allow_inactive=True)
        user_id = _current_user_id(data)

        reason = _reason(data, required=True)
        new_stock = _integer(data.get("new_stock"), "new_stock")
        if new_stock < 0:
            raise ValidationError("El campo 'new_stock' no puede ser negativo.")
        if not product.is_active and new_stock != 0:
            raise ConflictError(
                f"El producto '{product.name}' está inactivo; solo admite un ajuste a 0. "
                "Reactívelo para registrar otras existencias.")
        if new_stock == product.current_stock:
            raise ValidationError(
                f"El ajuste no tiene efecto: el stock actual ya es {new_stock}."
            )

        quantity = abs(new_stock - product.current_stock)
        movement = _create_movement(
            product, MOVEMENT_AJUSTE, quantity, new_stock, reason, user_id, trace
        )
        if commit:
            _commit_owned(product)
        return movement
    except Exception:
        if commit:
            db.session.rollback()
        raise


_KEY_RE = re.compile(r"[A-Za-z0-9_-]{8,64}")


def _idempotency_key(data: dict) -> str | None:
    key = data.pop("idempotency_key", None)
    if key is None:
        return None
    if not isinstance(key, str) or not _KEY_RE.fullmatch(key.strip()):
        raise ValidationError("El campo 'idempotency_key' debe tener de 8 a 64 letras, números, '-' o '_'.")
    return key.strip()


def _replay(movement: StockMovement, movement_type: str, data: dict) -> StockMovement:
    """La misma clave devuelve el movimiento ya registrado; con otros datos es un conflicto."""
    same = (movement.user_id == current_user.id and movement.movement_type == movement_type
            and movement.product_id == _integer(data.get("product_id"), "product_id")
            and movement.quantity == _integer(data.get("quantity"), "quantity"))
    if not same:
        raise ConflictError("La clave de reintento ya se usó para otro movimiento. Recargue el formulario.")
    return movement


def register_manual(movement_type: str, data: dict) -> tuple[StockMovement, bool]:
    """Entrada o salida manual. Devuelve ``(movimiento, creado)``; un reintento no duplica."""
    data = dict(data)
    key = _idempotency_key(data)
    if key:
        existing = StockMovement.query.filter_by(idempotency_key=key).first()
        if existing is not None:
            return _replay(existing, movement_type, data), False
    register = register_entry if movement_type == MOVEMENT_ENTRADA else register_exit
    try:
        movement = register(data, commit=False, creation_origin="manual")
        movement.idempotency_key = key
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        winner = StockMovement.query.filter_by(idempotency_key=key).first() if key else None
        if winner is None:
            raise
        return _replay(winner, movement_type, data), False  # la petición paralela ya confirmó
    except Exception:
        db.session.rollback()
        raise
    from app.services import notification_service

    notification_service.product_stock_changed(movement.product)
    return movement, True


def list_movements(
    movement_type: str | None = None,
    product_id: int | None = None,
    limit: int | None = None,
) -> list[StockMovement]:
    query = StockMovement.query.options(joinedload(StockMovement.product), joinedload(StockMovement.user))

    if movement_type:
        movement_type = movement_type.strip().lower()
        if movement_type not in MOVEMENT_TYPES:
            raise ValidationError(
                f"Tipo de movimiento inválido: '{movement_type}'. "
                f"Valores permitidos: {', '.join(MOVEMENT_TYPES)}."
            )
        query = query.filter(StockMovement.movement_type == movement_type)

    if product_id is not None:
        if db.session.get(Product, product_id) is None:
            raise NotFoundError(f"El producto con id {product_id} no existe.")
        query = query.filter(StockMovement.product_id == product_id)

    query = query.order_by(StockMovement.created_at.desc(), StockMovement.id.desc())

    if limit is not None:
        if limit <= 0:
            raise ValidationError("El parámetro 'limit' debe ser mayor a cero.")
        query = query.limit(limit)

    return query.all()


COMMERCIAL_ZONE = ZoneInfo("America/Caracas")
SORT_COLUMNS = {
    "created_at": StockMovement.created_at, "movement_type": StockMovement.movement_type,
    "product": Product.name, "quantity": StockMovement.quantity,
    "previous_stock": StockMovement.previous_stock, "new_stock": StockMovement.new_stock,
    "user": User.name,
}


def _utc_boundary(day):
    return datetime.combine(day, time.min, COMMERCIAL_ZONE).astimezone(timezone.utc).replace(tzinfo=None)


def _date_filter(value, field):
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError
        day = datetime.strptime(value, "%Y-%m-%d").date()
        _utc_boundary(day + timedelta(days=1))
        return day
    except (ValueError, OverflowError):
        raise ValidationError(f"'{field}' debe ser una fecha válida YYYY-MM-DD.")


def _positive_parameter(value, field):
    value = _integer(value, field)
    if value < 1:
        raise ValidationError(f"'{field}' debe ser positivo.")
    return value


def movement_filters(params: dict) -> dict:
    """Validate HTTP-independent query values; dates are commercial days, storage UTC."""
    unsupported = {"branch", "branch_id", "status", "reference", "reference_number"} & params.keys()
    if unsupported:
        raise ValidationError("Filtros no disponibles: " + ", ".join(sorted(unsupported)))
    result = {"search": (params.get("search") or "").strip()}
    kind = (params.get("movement_type") or "").strip().lower()
    if kind and kind not in MOVEMENT_TYPES:
        raise ValidationError("Tipo de movimiento no disponible.")
    result["movement_type"] = kind
    for field in ("user_id", "product_id"):
        if params.get(field) not in (None, ""):
            result[field] = _positive_parameter(params[field], field)
    for field in ("date_from", "date_to"):
        if not params.get(field):
            continue
        result[field] = _date_filter(params[field], field)
    if result.get("date_from") and result.get("date_to") and result["date_from"] > result["date_to"]:
        raise ValidationError("La fecha inicial no puede superar la final.")
    return result


def _filtered_query(filters):
    query = StockMovement.query.join(Product).outerjoin(User, User.id == StockMovement.user_id)
    search = filters.get("search")
    if search:
        search = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(or_(*[
            column.ilike(f"%{search}%", escape="\\")
            for column in (Product.name, Product.code, StockMovement.reason)
        ]))
    for key in ("movement_type", "product_id", "user_id"):
        if filters.get(key):
            query = query.filter(getattr(StockMovement, key) == filters[key])
    if filters.get("date_from"):
        query = query.filter(StockMovement.created_at >= _utc_boundary(filters["date_from"]))
    if filters.get("date_to"):
        query = query.filter(StockMovement.created_at < _utc_boundary(filters["date_to"] + timedelta(days=1)))
    return query


def movement_data(movement, presentation=None):
    """Explicit public fields, preserving the legacy movement representation."""
    result = movement.to_dict()
    product = movement.product
    result.update({
        "product_code": product.code if product else None,
        "product_image": product.image_url if product else None,
        "product_unit": product.unit if product else None,
        "product_category": product.category.name if product and product.category else None,
        "delta": movement.new_stock - movement.previous_stock,
    })
    result.update(presentation if presentation is not None else movement_presentations([movement])[movement.id])
    return result


def _with_relations(query):
    return query.options(
        joinedload(StockMovement.product).joinedload(Product.category),
        joinedload(StockMovement.user),
    )


def _pagination(params, query):
    if "page" not in params and "per_page" not in params:
        return None
    if "limit" in params:
        raise ValidationError("No combine limit con paginación.")
    page = _positive_parameter(params.get("page", 1), "page")
    per_page = _positive_parameter(params.get("per_page", 8), "per_page")
    if per_page > 100:
        raise ValidationError("per_page debe estar entre 1 y 100.")
    total = query.count()
    return {"total": total, "page": page, "per_page": per_page,
            "pages": (total + per_page - 1) // per_page}


def sorted_movements_query(params: dict):
    """Validated filters, whitelisted sort and unpaginated query shared by list and export."""
    filters = movement_filters(params)
    sort = params.get("sort") or "-created_at"
    column = SORT_COLUMNS.get(sort.lstrip("-"))
    if column is None or sort.startswith("--"):
        raise ValidationError("Orden de movimientos no permitido.")
    query = _with_relations(_filtered_query(filters))
    if filters.get("product_id") and db.session.get(Product, filters["product_id"]) is None:
        raise NotFoundError("Producto no encontrado.")
    order = ((column.desc(), StockMovement.id.desc()) if sort.startswith("-")
             else (column.asc(), StockMovement.id.asc()))
    return filters, query, order


def query_movements(params: dict) -> dict:
    filters, query, order = sorted_movements_query(params)
    metadata = _pagination(params, query)
    query = query.order_by(*order)
    if metadata:
        query = query.offset((metadata["page"] - 1) * metadata["per_page"]).limit(metadata["per_page"])
    elif "limit" in params:
        query = query.limit(_positive_parameter(params["limit"], "limit"))
    movements = query.all()
    presentations = movement_presentations(movements)
    items = [movement_data(m, presentations[m.id]) if metadata
             else {**m.to_dict(), **presentations[m.id]} for m in movements]
    return {"count": len(items), "items": items, **(metadata or {})}


def get_movement(movement_id):
    movement = _with_relations(StockMovement.query).filter_by(id=movement_id).first()
    if movement is None:
        raise NotFoundError("Movimiento no encontrado.")
    return movement_data(movement)


def movement_users():
    rows = db.session.query(User.id, User.name).join(StockMovement).distinct().order_by(User.name, User.id).all()
    return [{"id": row.id, "name": row.name} for row in rows]


def movement_summary(params: dict, now=None):
    filters = movement_filters(params)
    today = (now or datetime.now(timezone.utc)).astimezone(COMMERCIAL_ZONE).date()
    daily = dict(db.session.query(StockMovement.movement_type, func.count(StockMovement.id))
                 .filter(StockMovement.created_at >= _utc_boundary(today),
                         StockMovement.created_at < _utc_boundary(today + timedelta(days=1)))
                 .group_by(StockMovement.movement_type).all())
    query = _filtered_query(filters)
    grouped = dict(query.with_entities(StockMovement.movement_type, func.count(StockMovement.id))
                   .group_by(StockMovement.movement_type).all())
    recent = _with_relations(query).order_by(StockMovement.created_at.desc(), StockMovement.id.desc()).limit(5).all()
    presentations = movement_presentations(recent)
    return {"daily": {kind: daily.get(kind, 0) for kind in MOVEMENT_TYPES},
            "commercial_day": today.isoformat(), "timezone": "America/Caracas",
            "distribution": grouped, "total": sum(grouped.values()),
            "recent": [movement_data(m, presentations[m.id]) for m in recent]}
