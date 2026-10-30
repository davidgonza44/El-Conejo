"""Registro de ventas (Sales Core v1): una venta = una transacción.

Secuencia de ``create_sale``:
1. rechazar campos autoritativos del cliente (seller_id, precios, totales, stock);
2. idempotencia: si ``idempotency_key`` ya existe se devuelve esa venta (sin efectos),
   salvo que pertenezca a otro vendedor: 409 genérico, sin datos de esa venta;
3. validar cliente, método/condición de pago y renglones (sin product_id repetidos);
4. insertar la venta con ``sale_number`` NULL, ``flush`` para obtener el id y fijar
   ``VTA-{id:06d}`` (el autoincremento de InnoDB es la secuencia; sin COUNT+1 ni
   literal temporal compartido);
5. por cada producto en orden ascendente de ``product_id`` (orden de bloqueo estable):
   ``inventory_service.register_exit(..., commit=False)`` bloquea la fila, valida
   activo/stock, crea la salida y actualiza el stock; el renglón copia código, nombre y
   ``sale_price`` reales y enlaza ``stock_movement_id``;
6. totales autoritativos con Decimal ROUND_HALF_UP a 2 decimales;
7. un único ``commit``. Cualquier error -> ``rollback`` de todo.

Concurrencia con la misma clave: la UNIQUE de ``idempotency_key`` hace que la segunda
transacción espere y reciba IntegrityError al confirmar la primera; se revierte y se
devuelve la venta ya confirmada (200). Ningún stock se descuenta dos veces.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask_login import current_user
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Category, Customer, Product, Sale, SaleLine
from app.models.sale import NUMBER_PREFIX, PAYMENT_CONDITIONS, PAYMENT_METHODS, SALE_STATUSES, STATUS_COMPLETED
from app.services import inventory_service
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.movement_traceability_service import SERVER_FIELDS, reject_server_fields

CENT = Decimal("0.01")
HUNDRED = Decimal("100")
MAX_MONEY = Decimal("9999999999.99")
_CREATE_FIELDS = {"customer_id", "payment_method", "payment_condition", "idempotency_key", "items", "cash_received"}
_ITEM_FIELDS = {"product_id", "quantity", "discount_percent"}
_FORBIDDEN_FIELDS = {
    "seller_id", "user_id", "created_by", "responsible_id",
    "sale_number", "sold_at", "status", "subtotal", "discount_total", "total",
}
_FOREIGN_REPLAY = "No se pudo registrar la venta."
_FORBIDDEN_ITEM_FIELDS = {"unit_price", "line_subtotal", "line_total", "discount_amount",
                          "previous_stock", "new_stock", "product_code", "product_name"}
_DEFAULT_PER_PAGE = 10
_MAX_PER_PAGE = 100
_PRODUCT_SEARCH_LIMIT = 50


def quantize(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _reject(data: dict, forbidden: set, where: str) -> None:
    present = forbidden & set(data)
    if present:
        raise ValidationError(
            f"Campos no permitidos en {where}: {', '.join(sorted(present))}. "
            "El servidor calcula precios, totales, vendedor y stock."
        )


def _choice(data: dict, field: str, allowed: dict) -> str:
    value = data.get(field)
    if not isinstance(value, str) or value.strip().lower() not in allowed:
        raise ValidationError(
            f"El campo '{field}' es obligatorio. Valores permitidos: {', '.join(allowed)}."
        )
    return value.strip().lower()


def _only(data: dict, allowed: set, where: str) -> None:
    if not isinstance(data, dict):
        raise ValidationError(f"Se requiere un objeto JSON en {where}.")
    if set(data) - allowed:
        raise ValidationError(f"Campos inesperados en {where}: {', '.join(sorted(set(data) - allowed))}.")


def _cash_received(data: dict, method: str) -> Decimal | None:
    value = data.get("cash_received")
    if method != "cash":
        if value is not None:
            raise ValidationError("cash_received solo aplica al pago en efectivo.")
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValidationError("cash_received es obligatorio en efectivo; use un importe decimal exacto.")
    try:
        amount = Decimal(str(value).strip())
        if (not amount.is_finite() or not 0 <= amount <= MAX_MONEY
                or amount != quantize(amount)):
            raise InvalidOperation
        return quantize(amount)
    except InvalidOperation:
        raise ValidationError("cash_received debe ser no negativo, con máximo dos decimales y dentro del rango permitido.")


def _settle_cash(sale: Sale, received: Decimal | None) -> None:
    if sale.total > MAX_MONEY or sale.subtotal > MAX_MONEY:
        raise ValidationError("El importe de la venta supera el máximo permitido.")
    if received is not None:
        if received < sale.total:
            raise ValidationError("El efectivo recibido no cubre el total de la venta.")
        sale.cash_received = received
        sale.change_due = quantize(received - sale.total)


def _discount(value) -> Decimal:
    if value is None:
        return Decimal("0.00")
    if isinstance(value, bool) or not isinstance(value, (int, str, Decimal)):
        raise ValidationError("El campo 'discount_percent' debe ser un número entre 0 y 100.")
    try:
        percent = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValidationError("El campo 'discount_percent' debe ser un número entre 0 y 100.")
    if not percent.is_finite() or percent < 0 or percent > HUNDRED:
        raise ValidationError("El campo 'discount_percent' debe estar entre 0 y 100.")
    return quantize(percent)


def _items(data: dict) -> list[dict]:
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ValidationError("El campo 'items' es obligatorio y debe tener al menos un producto.")
    parsed, seen = [], set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValidationError(f"El item #{index} debe ser un objeto JSON.")
        _only(item, _ITEM_FIELDS, f"el item #{index}")
        _reject(item, _FORBIDDEN_ITEM_FIELDS | SERVER_FIELDS | {"external_reference"}, f"el item #{index}")
        try:
            product_id = int(item.get("product_id"))
        except (TypeError, ValueError):
            raise ValidationError(f"El item #{index} requiere un 'product_id' entero.")
        if product_id in seen:
            raise ValidationError(
                f"El producto {product_id} aparece repetido en los items; "
                "consolide las cantidades en un solo item."
            )
        seen.add(product_id)
        parsed.append({"product_id": product_id, "quantity": item.get("quantity"),
                       "discount_percent": _discount(item.get("discount_percent"))})
    parsed.sort(key=lambda entry: entry["product_id"])  # orden de bloqueo determinista
    return parsed


def _customer(data: dict) -> Customer:
    try:
        customer_id = int(data.get("customer_id"))
    except (TypeError, ValueError):
        raise ValidationError("El campo 'customer_id' es obligatorio y debe ser un entero.")
    customer = db.session.get(Customer, customer_id)
    if customer is None:
        raise NotFoundError(f"El cliente con id {customer_id} no existe.")
    if not customer.is_active:
        raise ConflictError(f"El cliente '{customer.name}' está inactivo; no admite ventas.")
    return customer


def _idempotency_key(data: dict) -> str:
    key = data.get("idempotency_key")
    if not isinstance(key, str) or not (8 <= len(key.strip()) <= 64):
        raise ValidationError("El campo 'idempotency_key' es obligatorio (8 a 64 caracteres).")
    return key.strip()


def _by_key(key: str) -> Sale | None:
    return Sale.query.filter(Sale.idempotency_key == key).first()


def _owned_replay(existing: Sale) -> tuple[Sale, bool]:
    """La clave repetida devuelve la venta propia. La de otro vendedor no revela número ni datos."""
    from app.services.sales_common import current_vendor_id

    vendor_id = current_vendor_id()
    if vendor_id is not None and existing.seller_id != vendor_id:
        raise ConflictError(_FOREIGN_REPLAY)
    return existing, False


def _add_line(sale: Sale, entry: dict) -> SaleLine:
    movement = inventory_service.register_exit(
        {"product_id": entry["product_id"], "quantity": entry["quantity"],
         "reason": f"Venta {sale.sale_number}"},
        commit=False,
    )
    product = movement.product
    unit_price = Decimal(product.sale_price)
    line_subtotal = quantize(unit_price * movement.quantity)
    if line_subtotal > MAX_MONEY:
        raise ValidationError("El importe del renglón supera el máximo permitido.")
    discount_amount = quantize(line_subtotal * entry["discount_percent"] / HUNDRED)
    line = SaleLine(
        sale_id=sale.id, product_id=product.id, stock_movement_id=movement.id,
        product_code=product.code, product_name=product.name, quantity=movement.quantity,
        unit_price=quantize(unit_price), discount_percent=entry["discount_percent"],
        discount_amount=discount_amount, line_subtotal=line_subtotal,
        line_total=quantize(line_subtotal - discount_amount),
    )
    db.session.add(line)
    return line


def create_sale(data: dict) -> tuple[Sale, bool]:
    """Devuelve ``(venta, creada)``; ``creada=False`` es una repetición idempotente."""
    from app.services.rbac_service import require_operational_permission

    require_operational_permission("sales:create")
    _only(data, _CREATE_FIELDS, "la venta")
    reject_server_fields(data)
    _reject(data, _FORBIDDEN_FIELDS | {"external_reference"}, "la venta")
    key = _idempotency_key(data)
    existing = _by_key(key)
    if existing is not None:
        return _owned_replay(existing)
    customer = _customer(data)
    method = _choice(data, "payment_method", PAYMENT_METHODS)
    condition = _choice(data, "payment_condition", PAYMENT_CONDITIONS)
    received = _cash_received(data, method)
    entries = _items(data)
    try:
        sale = Sale(
            customer_id=customer.id, seller_id=current_user.id,
            sold_at=datetime.now(timezone.utc).replace(tzinfo=None),
            payment_method=method, payment_condition=condition,
            status=STATUS_COMPLETED, idempotency_key=key,
        )
        db.session.add(sale)
        db.session.flush()  # AUTO_INCREMENT asigna el id: es la secuencia del número.
        sale.sale_number = f"{NUMBER_PREFIX}-{sale.id:06d}"
        lines = [_add_line(sale, entry) for entry in entries]
        sale.subtotal = quantize(sum((l.line_subtotal for l in lines), Decimal(0)))
        sale.discount_total = quantize(sum((l.discount_amount for l in lines), Decimal(0)))
        sale.total = quantize(sum((l.line_total for l in lines), Decimal(0)))
        _settle_cash(sale, received)
        db.session.commit()
        from app.services import notification_service

        notification_service.sale_created(sale)
    except IntegrityError:
        db.session.rollback()
        winner = _by_key(key)  # la otra petición con la misma clave ya confirmó
        if winner is None:
            raise ConflictError("No se pudo registrar la venta; intente nuevamente.")
        return _owned_replay(winner)
    except Exception:
        db.session.rollback()
        raise
    return sale, True


def _with_relations(query):
    return query.options(joinedload(Sale.customer), joinedload(Sale.seller),
                         joinedload(Sale.lines).joinedload(SaleLine.product))


def get_sale(sale_number: str) -> Sale:
    from app.services.sales_common import restrict_own_sales

    # Mismo 404 si no existe o si es de otro vendedor: la respuesta no confirma el número.
    sale = restrict_own_sales(
        _with_relations(Sale.query).filter(Sale.sale_number == sale_number), Sale.seller_id,
    ).first()
    if sale is None:
        raise NotFoundError(f"La venta {sale_number} no existe.")
    return sale


def _positive_int(value, field) -> int:
    if isinstance(value, bool) or not (isinstance(value, int) or isinstance(value, str) and value.strip().isdigit()):
        raise ValidationError(f"El parámetro '{field}' debe ser un entero positivo.")
    if int(value) < 1:
        raise ValidationError(f"El parámetro '{field}' debe ser un entero positivo.")
    return int(value)


def list_sales(params: dict) -> dict:
    """Listado: búsqueda por número/cliente/producto, estado, método de pago, fechas comerciales
    (date_from/date_to inclusivas) y paginación (más reciente primero)."""
    from app.services import sales_common as common  # sales_common reexporta helpers de este módulo

    query = common.restrict_own_sales(Sale.query, Sale.seller_id).join(Customer)
    search = (params.get("search") or "").strip()
    if search:
        term = common.like_term(search)
        in_lines = select(SaleLine.sale_id).where(or_(SaleLine.product_name.ilike(term, escape="\\"),
                                                      SaleLine.product_code.ilike(term, escape="\\")))
        query = query.filter(or_(Sale.sale_number.ilike(term, escape="\\"),
                                 Customer.name.ilike(term, escape="\\"), Sale.id.in_(in_lines)))
    status = (params.get("status") or "").strip().lower()
    if status:
        if status not in SALE_STATUSES:
            raise ValidationError(f"Estado inválido. Valores permitidos: {', '.join(SALE_STATUSES)}.")
        query = query.filter(Sale.status == status)
    if params.get("payment_method"):
        query = query.filter(Sale.payment_method == common.choice(params, "payment_method", PAYMENT_METHODS))
    query = common.apply_datetime_range(query, Sale.sold_at, params, "date_from", "date_to")
    customer_id = params.get("customer_id")
    if customer_id not in (None, ""):
        query = query.filter(Sale.customer_id == _positive_int(customer_id, "customer_id"))
    total = query.order_by(None).count()
    page = _positive_int(params.get("page", 1), "page")
    per_page = _positive_int(params.get("per_page", _DEFAULT_PER_PAGE), "per_page")
    if per_page > _MAX_PER_PAGE:
        raise ValidationError(f"per_page debe estar entre 1 y {_MAX_PER_PAGE}.")
    items = (_with_relations(query).order_by(Sale.sold_at.desc(), Sale.id.desc())
             .offset((page - 1) * per_page).limit(per_page).all())
    return {"count": len(items), "items": [s.to_summary_dict() for s in items], "total": total,
            "page": page, "per_page": per_page, "pages": (total + per_page - 1) // per_page}


# Mismos grupos excluyentes que Product.stock_status (solo activos): el filtro se resuelve en SQL.
_STOCK_FILTERS = {
    "disponible": Product.current_stock > Product.minimum_stock,
    "bajo_stock": and_(Product.current_stock > 0, Product.current_stock <= Product.minimum_stock),
    "agotado": Product.current_stock == 0,
}


def sale_products(params: dict) -> dict:
    """Catálogo apto para vender/cotizar/pedir: solo activos y campos públicos (nunca purchase_price).

    Compartido por el selector de productos de Nueva venta, Nueva cotización y Nuevo pedido;
    el stock aquí es informativo: solo la venta lo revalida y bloquea al guardar.
    """
    limit = _positive_int(params.get("limit", 10), "limit")
    if limit > _PRODUCT_SEARCH_LIMIT:
        raise ValidationError(f"limit debe estar entre 1 y {_PRODUCT_SEARCH_LIMIT}.")
    query = Product.query.options(joinedload(Product.category)).filter(Product.is_active.is_(True))
    search = (params.get("search") or "").strip()
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        term = f"%{escaped}%"
        query = query.filter(or_(Product.name.ilike(term, escape="\\"), Product.code.ilike(term, escape="\\")))
    if params.get("category_id"):
        query = query.filter(Product.category_id == _positive_int(params["category_id"], "category_id"))
    stock_status = (params.get("stock_status") or "").strip().lower()
    if stock_status:
        if stock_status not in _STOCK_FILTERS:
            raise ValidationError(f"stock_status inválido. Valores permitidos: {', '.join(_STOCK_FILTERS)}.")
        query = query.filter(_STOCK_FILTERS[stock_status])
    products = query.order_by(Product.name.asc(), Product.id.asc()).limit(limit).all()
    return {"count": len(products), "items": [{
        "id": p.id, "code": p.code, "name": p.name, "image_url": p.image_url,
        "sale_price": f"{Decimal(p.sale_price):.2f}", "current_stock": p.current_stock,
        "minimum_stock": p.minimum_stock, "stock_status": p.stock_status,
        "category_id": p.category_id, "category": p.category.name if p.category else None,
    } for p in products]}


def sale_options() -> dict:
    """Etiquetas en español de los valores canónicos (para la UI live) y categorías activas reales."""
    categories = Category.query.filter(Category.is_active.is_(True)).order_by(Category.name.asc()).all()
    return {"payment_methods": [{"value": k, "label": v} for k, v in PAYMENT_METHODS.items()],
            "payment_conditions": [{"value": k, "label": v} for k, v in PAYMENT_CONDITIONS.items()],
            "categories": [{"id": c.id, "name": c.name} for c in categories],
            "stock_statuses": [{"value": "disponible", "label": "Disponible"}, {"value": "bajo_stock", "label": "Bajo stock"},
                               {"value": "agotado", "label": "Agotado"}]}
