"""Piezas compartidas por cotizaciones y pedidos (documentos comerciales sin efecto en stock).

Reutiliza las reglas ya probadas de ``sale_service`` (cliente, dinero, rechazo de campos
autoritativos) y añade lo común a los listados: búsqueda por número/cliente/producto,
rangos de fecha comercial (America/Caracas), orden y paginación. Ninguna función de este
módulo bloquea filas de productos ni crea StockMovement: aquí el stock es informativo.
"""
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from flask_login import current_user
from sqlalchemy import exists, or_, select
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import Customer, Product, User
from app.models.user import ROLE_VENDEDOR
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.inventory_service import COMMERCIAL_ZONE
from app.services.inventory_snapshot_service import commercial_today
from app.services.sale_service import _customer as resolve_customer  # noqa: F401  (reexport)
from app.services.sale_service import _discount as parse_discount  # noqa: F401  (reexport)
from app.services.sale_service import _positive_int as positive_int
from app.services.sale_service import _reject as reject  # noqa: F401  (reexport)
from app.services.sale_service import quantize

DEFAULT_PER_PAGE = 10
MAX_PER_PAGE = 100
FORBIDDEN_ITEM_FIELDS = {"unit_price", "line_subtotal", "product_code", "product_name"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_boundary(day: date) -> datetime:
    """Inicio del día comercial (Caracas) expresado en UTC naive, como guarda la BD."""
    return datetime.combine(day, datetime.min.time(), COMMERCIAL_ZONE).astimezone(timezone.utc).replace(tzinfo=None)


def parse_date(value, field) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value.strip()):
        raise ValidationError(f"'{field}' debe ser una fecha válida YYYY-MM-DD.")
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError(f"'{field}' debe ser una fecha válida YYYY-MM-DD.")


def like_term(search: str) -> str:
    escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def choice(data: dict, field: str, allowed, default=None) -> str:
    value = data.get(field, default)
    if value is None and default is None:
        raise ValidationError(f"El campo '{field}' es obligatorio. Valores permitidos: {', '.join(allowed)}.")
    if not isinstance(value, str) or value.strip().lower() not in allowed:
        raise ValidationError(f"El campo '{field}' es inválido. Valores permitidos: {', '.join(allowed)}.")
    return value.strip().lower()


def optional_text(data: dict, field: str, max_length: int) -> str | None:
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"El campo '{field}' debe ser texto.")
    value = value.strip()
    if len(value) > max_length:
        raise ValidationError(f"El campo '{field}' no puede superar {max_length} caracteres.")
    return value or None


def _quantity(value, index) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError(f"El item #{index} requiere una 'quantity' entera mayor que cero.")
    return value


def parse_lines(data: dict) -> list[tuple[Product, int]]:
    """Items ``{product_id, quantity}`` sin repetidos; producto activo. NO consulta ni bloquea stock."""
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ValidationError("El campo 'items' es obligatorio y debe tener al menos un producto.")
    parsed, seen = [], set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValidationError(f"El item #{index} debe ser un objeto JSON.")
        reject(item, FORBIDDEN_ITEM_FIELDS, f"el item #{index}")
        try:
            product_id = int(item.get("product_id"))
        except (TypeError, ValueError):
            raise ValidationError(f"El item #{index} requiere un 'product_id' entero.")
        if product_id in seen:
            raise ValidationError(f"El producto {product_id} aparece repetido en los items; consolide las cantidades.")
        seen.add(product_id)
        quantity = _quantity(item.get("quantity"), index)
        product = db.session.get(Product, product_id)
        if product is None:
            raise NotFoundError(f"El producto con id {product_id} no existe.")
        if not product.is_active:
            raise ConflictError(f"El producto '{product.name}' está inactivo; no puede incluirse.")
        parsed.append((product, quantity))
    return parsed


def snapshot_line(line_model, product: Product, quantity: int, **keys):
    """Renglón con copia de código, nombre y ``sale_price`` actuales (precio autoritativo)."""
    unit_price = quantize(Decimal(product.sale_price))
    return line_model(product_id=product.id, product_code=product.code, product_name=product.name,
                      quantity=quantity, unit_price=unit_price, line_subtotal=quantize(unit_price * quantity), **keys)


def assign_number(document, attr: str, prefix: str) -> None:
    """``flush`` obtiene el id autoincremental (la secuencia) y fija ``PREFIX-{id:06d}``; sin COUNT+1."""
    db.session.add(document)
    db.session.flush()
    setattr(document, attr, f"{prefix}-{document.id:06d}")


def seller_id() -> int:
    return current_user.id


def current_vendor_id() -> int | None:
    """Id del vendedor autenticado cuando solo ve sus ventas. None para admin e inventario."""
    if current_user.is_authenticated and current_user.role == ROLE_VENDEDOR:
        return current_user.id
    return None


def restrict_own_sales(query, seller_column):
    """Acota la query a Sale.seller_id del vendedor antes de buscar, filtrar, ordenar o paginar."""
    vendor_id = current_vendor_id()
    if vendor_id is None:
        return query
    return query.filter(seller_column == vendor_id)


def money_param(value, field) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValidationError(f"El parámetro '{field}' debe ser un monto válido.")
    if not amount.is_finite() or amount < 0:
        raise ValidationError(f"El parámetro '{field}' debe ser un monto mayor o igual a cero.")
    return quantize(amount)


def int_param(params: dict, key: str, minimum: int = 1) -> int | None:
    value = params.get(key)
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not (isinstance(value, int) or isinstance(value, str) and value.strip().lstrip("-").isdigit()):
        raise ValidationError(f"El parámetro '{key}' debe ser un entero.")
    if int(value) < minimum:
        raise ValidationError(f"El parámetro '{key}' debe ser mayor o igual a {minimum}.")
    return int(value)


def apply_search(query, document, line, owner, number_column, search: str):
    """Número, nombre de cliente o nombre/código de producto cotizado/pedido (una sola consulta).

    ``owner`` es la FK del renglón hacia el documento (``QuoteLine.quote_id`` / ``OrderLine.order_id``).
    """
    term = like_term(search)
    line_match = exists().where(
        owner == document.id,
        or_(line.product_name.ilike(term, escape="\\"), line.product_code.ilike(term, escape="\\")),
    )
    return query.filter(or_(number_column.ilike(term, escape="\\"), Customer.name.ilike(term, escape="\\"), line_match))


def apply_datetime_range(query, column, params: dict, from_key: str, to_key: str):
    """Fechas comerciales (Caracas) inclusivas sobre una columna DATETIME UTC."""
    if params.get(from_key):
        query = query.filter(column >= utc_boundary(parse_date(params[from_key], from_key)))
    if params.get(to_key):
        query = query.filter(column < utc_boundary(parse_date(params[to_key], to_key) + timedelta(days=1)))
    return query


def apply_date_range(query, column, params: dict, from_key: str, to_key: str):
    if params.get(from_key):
        query = query.filter(column >= parse_date(params[from_key], from_key))
    if params.get(to_key):
        query = query.filter(column <= parse_date(params[to_key], to_key))
    return query


def apply_id_filters(query, document, line, owner, params: dict):
    if params.get("customer_id"):
        query = query.filter(document.customer_id == positive_int(params["customer_id"], "customer_id"))
    if params.get("seller_id"):
        query = query.filter(document.seller_id == positive_int(params["seller_id"], "seller_id"))
    if params.get("product_id"):
        query = query.filter(document.id.in_(
            select(owner).where(line.product_id == positive_int(params["product_id"], "product_id"))))
    return query


def apply_money_range(query, column, params: dict, min_key: str, max_key: str):
    low, high = money_param(params.get(min_key), min_key), money_param(params.get(max_key), max_key)
    if low is not None and high is not None and low > high:
        raise ValidationError(f"'{min_key}' no puede superar a '{max_key}'.")
    if low is not None:
        query = query.filter(column >= low)
    if high is not None:
        query = query.filter(column <= high)
    return query


def order_clause(params: dict, columns: dict, default: str):
    sort = (params.get("sort") or default).strip()
    column = columns.get(sort.lstrip("-"))
    if column is None or sort.startswith("--"):
        raise ValidationError(f"Orden no permitido. Claves: {', '.join(columns)}.")
    return column.desc() if sort.startswith("-") else column.asc()


def paginate(query, params: dict, document, line, serializer):
    total = query.order_by(None).count()
    page = positive_int(params.get("page", 1), "page")
    per_page = positive_int(params.get("per_page", DEFAULT_PER_PAGE), "per_page")
    if per_page > MAX_PER_PAGE:
        raise ValidationError(f"per_page debe estar entre 1 y {MAX_PER_PAGE}.")
    items = (query.options(joinedload(document.customer), joinedload(document.seller),
                           selectinload(document.lines).joinedload(line.product))
             .offset((page - 1) * per_page).limit(per_page).all())
    return {"count": len(items), "items": [serializer(doc) for doc in items], "total": total,
            "page": page, "per_page": per_page, "pages": (total + per_page - 1) // per_page}


def sellers_of(document) -> list[dict]:
    """Vendedores con documentos registrados (valores reales para el filtro 'Vendedor')."""
    rows = (db.session.query(User.id, User.name).join(document, document.seller_id == User.id)
            .distinct().order_by(User.name.asc()).all())
    return [{"id": row.id, "name": row.name} for row in rows]


__all__ = ["commercial_today", "quantize", "resolve_customer", "parse_discount", "reject", "positive_int"]
