"""Lógica de negocio de productos.

El stock inicial se propone con el alta y se registra vía InventoryService
(entrada) cuando el Administrador la aprueba. Después, solo los movimientos
de inventario pueden modificar current_stock.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask_login import current_user

from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import contains_eager

from app.extensions import db
from app.models import Category, Product
from app.models.purchase import ProductSupplier, Supplier
from app.models.product_change import ProductChange
from app.utils.permissions import SUPPLIERS_READ, role_has_permission
from app.services import inventory_service, product_state_history_service
from app.services.exceptions import ApiError, ConflictError, NotFoundError, ValidationError
from app.utils.catalog_currency import CURRENCIES, require_currency, validate_price_pairs

# Campos que el CRUD acepta al editar. current_stock queda excluido a propósito.
_UPDATABLE_FIELDS = {
    "code",
    "name",
    "description",
    "category_id",
    "unit",
    "minimum_stock",
    "purchase_price",
    "sale_price",
    "sale_price_currency",
    "purchase_price_currency",
    "image_url",
    "is_active",
    "is_service",
    "brand",
}

STATUS_DISPONIBLE = "disponible"
STATUS_BAJO_STOCK = "bajo_stock"
STATUS_AGOTADO = "agotado"
STATUS_INACTIVO = "inactivo"
STATUS_VALUES = {
    STATUS_DISPONIBLE,
    STATUS_BAJO_STOCK,
    STATUS_AGOTADO,
    STATUS_INACTIVO,
}

_SORT_WHITELIST = {
    "name-asc": (Product.name.asc(),),
    "name-desc": (Product.name.desc(),),
    "price-asc": (Product.sale_price_currency.asc(), Product.sale_price.asc(), Product.name.asc()),
    "price-desc": (Product.sale_price_currency.asc(), Product.sale_price.desc(), Product.name.asc()),
    "stock-asc": (Product.current_stock.asc(), Product.name.asc()),
    "stock-desc": (Product.current_stock.desc(), Product.name.asc()),
    "created-desc": (Product.created_at.desc(), Product.id.desc()),
    "created-asc": (Product.created_at.asc(), Product.id.asc()),
    "updated-desc": (Product.updated_at.desc(), Product.id.desc()),
}

_DEFAULT_SORT = "name-asc"
_DEFAULT_PER_PAGE = 8
_MAX_PER_PAGE = 100
_ALERT_LIMIT = 5
_RECENT_LIMIT = 1
_INITIAL_STOCK_REASON = "Stock inicial"


def _non_negative_int(data: dict, field: str, default: int | None = None) -> int:
    value = data.get(field, default)
    if value is None:
        value = default
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"El campo '{field}' debe ser un número entero.")
    if value < 0:
        raise ValidationError(f"El campo '{field}' no puede ser negativo.")
    return value


def _non_negative_price(data: dict, field: str, default: str = "0") -> Decimal:
    value = data.get(field, default)
    if value is None:
        value = default
    try:
        value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationError(f"El campo '{field}' debe ser un número.")
    if not value.is_finite():
        raise ValidationError(f"El campo '{field}' debe ser un número finito.")
    if value < 0:
        raise ValidationError(f"El campo '{field}' no puede ser negativo.")
    return value


def _validate_code(code: str | None, exclude_id: int | None = None) -> str:
    code = (code or "").strip()
    if not code:
        raise ValidationError("El código del producto es obligatorio.")

    query = Product.query.filter(Product.code == code)
    if exclude_id is not None:
        query = query.filter(Product.id != exclude_id)
    if query.first() is not None:
        raise ConflictError(f"Ya existe un producto con el código '{code}'.")
    return code


def _validate_category(category_id) -> int:
    """Solo categorías aprobadas y activas se pueden asignar, también durante la edición."""
    try:
        category_id = int(category_id)
    except (TypeError, ValueError):
        raise ValidationError("El campo 'category_id' es obligatorio y debe ser un entero.")
    category = Category.query.filter_by(id=category_id).populate_existing().with_for_update().first()
    if category is None:
        raise ValidationError(f"La categoría con id {category_id} no existe.")
    if not category.is_active or category.approval_status != "approved":
        raise ValidationError(f"La categoría '{category.name}' debe estar aprobada y activa.")
    return category_id


def _validate_unit(data: dict) -> str:
    if "unit" not in data or data.get("unit") is None:
        return "unidad"
    unit = str(data.get("unit")).strip()
    if not unit:
        raise ValidationError("El campo 'unit' no puede estar vacío.")
    return unit


def _validate_range(min_value, max_value, field: str):
    if min_value is not None and max_value is not None and min_value > max_value:
        raise ValidationError(f"El rango de '{field}' es inválido: el mínimo no puede ser mayor que el máximo.")


def get_product_or_404(product_id: int) -> Product:
    product = db.session.get(Product, product_id)
    if product is None:
        raise NotFoundError(f"El producto con id {product_id} no existe.")
    return product


def _product_query():
    return Product.query.join(Category).options(contains_eager(Product.category))


def _apply_status_filters(query, status: str | None, include_inactive: bool, low_stock: bool):
    if status:
        if status not in STATUS_VALUES:
            raise ValidationError(
                f"Estado inválido: '{status}'. "
                f"Valores permitidos: {', '.join(sorted(STATUS_VALUES))}."
            )
        if status == STATUS_INACTIVO:
            return query.filter(Product.is_active.is_(False))
        query = query.filter(Product.is_active.is_(True))
        if status == STATUS_AGOTADO:
            return query.filter(Product.current_stock == 0)
        if status == STATUS_BAJO_STOCK:
            return query.filter(
                Product.current_stock > 0,
                Product.current_stock <= Product.minimum_stock,
            )
        return query.filter(Product.current_stock > Product.minimum_stock)

    if not include_inactive:
        query = query.filter(Product.is_active.is_(True))
    if low_stock:
        query = query.filter(Product.current_stock <= Product.minimum_stock)
    return query


def _apply_list_filters(
    query,
    search: str | None,
    category_id: int | None,
    price_min: Decimal | None,
    price_max: Decimal | None,
    stock_min: int | None,
    stock_max: int | None,
    updated_from: datetime | None,
    updated_to: datetime | None,
):
    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Product.name.ilike(term),
                Product.code.ilike(term),
                Category.name.ilike(term),
            )
        )
    if category_id is not None:
        query = query.filter(Product.category_id == category_id)
    if price_min is not None:
        query = query.filter(Product.sale_price >= price_min)
    if price_max is not None:
        query = query.filter(Product.sale_price <= price_max)
    if stock_min is not None:
        query = query.filter(Product.current_stock >= stock_min)
    if stock_max is not None:
        query = query.filter(Product.current_stock <= stock_max)
    if updated_from is not None:
        query = query.filter(Product.updated_at >= updated_from)
    if updated_to is not None:
        query = query.filter(Product.updated_at <= updated_to)
    return query


def _apply_supplier_filter(query, supplier_id):
    if supplier_id is None:
        return query
    if not current_user or not current_user.is_authenticated or not role_has_permission(current_user.role, SUPPLIERS_READ):
        raise ApiError("No tiene permiso para filtrar por proveedor.", status_code=403)
    if type(supplier_id) is not int or not 1 <= supplier_id <= 2147483647:
        raise ValidationError("El parámetro 'supplier_id' debe ser un entero positivo válido.")
    if db.session.get(Supplier, supplier_id) is None:
        raise NotFoundError("El proveedor no existe.")
    # Todas las vinculaciones del catálogo, no solo el proveedor preferido.
    linked = db.session.query(ProductSupplier.id).filter(
        ProductSupplier.product_id == Product.id,
        ProductSupplier.supplier_id == supplier_id,
    ).exists()
    return query.filter(linked)


def _sort_clauses(sort: str | None):
    key = (sort or _DEFAULT_SORT).strip()
    clauses = _SORT_WHITELIST.get(key)
    if clauses is None:
        raise ValidationError(
            f"Orden inválido: '{sort}'. "
            f"Valores permitidos: {', '.join(_SORT_WHITELIST)}."
        )
    return clauses


def _apply_approval_filter(query, approval_status):
    if not approval_status or approval_status == "all":
        return query
    if approval_status not in ("pending", "approved", "rejected"):
        raise ValidationError("Estado de aprobación inválido.")
    pending_change = db.session.query(ProductChange.id).filter(
        ProductChange.product_id == Product.id, ProductChange.status == "pending").exists()
    if approval_status == "pending":
        return query.filter(or_(Product.approval_status == "pending", pending_change))
    query = query.filter(Product.approval_status == approval_status)
    return query.filter(~pending_change) if approval_status == "approved" else query


def filtered_products_query(
    search: str | None = None,
    category_id: int | None = None,
    low_stock: bool = False,
    include_inactive: bool = False,
    status: str | None = None,
    price_min: Decimal | None = None,
    price_max: Decimal | None = None,
    stock_min: int | None = None,
    stock_max: int | None = None,
    updated_from: datetime | None = None,
    updated_to: datetime | None = None,
    sort: str | None = None,
    supplier_id: int | None = None,
    approval_status: str | None = None,
    sale_price_currency: str | None = None,
):
    _validate_range(price_min, price_max, "precio")
    _validate_range(stock_min, stock_max, "stock")
    _validate_range(updated_from, updated_to, "última actualización")

    query = _apply_list_filters(
        _apply_status_filters(_product_query(), status,
                              include_inactive or approval_status in ("all", "pending", "approved", "rejected"), low_stock),
        search,
        category_id,
        price_min,
        price_max,
        stock_min,
        stock_max,
        updated_from,
        updated_to,
    )
    query = _apply_supplier_filter(query, supplier_id)
    query = _apply_approval_filter(query, approval_status)
    if sale_price_currency is not None:
        query = query.filter(Product.sale_price_currency == require_currency(sale_price_currency, "sale_price_currency"))
    if price_min is not None or price_max is not None:
        currencies = db.session.query(Product.sale_price_currency).filter(
            Product.sale_price_currency.in_(CURRENCIES)).distinct().limit(2).all()
        if sale_price_currency is None and len(currencies) > 1:
            raise ValidationError("Seleccione una moneda para comparar precios USD o VES.")
        query = query.filter(Product.sale_price_currency.in_(CURRENCIES))
    return query.order_by(*_sort_clauses(sort))


def list_products(
    search: str | None = None,
    category_id: int | None = None,
    low_stock: bool = False,
    include_inactive: bool = False,
    status: str | None = None,
    price_min: Decimal | None = None,
    price_max: Decimal | None = None,
    stock_min: int | None = None,
    stock_max: int | None = None,
    updated_from: datetime | None = None,
    updated_to: datetime | None = None,
    sort: str | None = None,
    page: int | None = None,
    per_page: int | None = None,
    supplier_id: int | None = None,
    approval_status: str | None = None,
    sale_price_currency: str | None = None,
) -> dict:
    query = filtered_products_query(
        search, category_id, low_stock, include_inactive, status,
        price_min, price_max, stock_min, stock_max, updated_from, updated_to, sort,
        supplier_id=supplier_id,
        approval_status=approval_status,
        sale_price_currency=sale_price_currency,
    )
    total = query.order_by(None).count()

    paginated = page is not None or per_page is not None
    if not paginated:
        items = query.all()
        return {"items": items, "total": total, "pagination": None}

    page = 1 if page is None else page
    per_page = _DEFAULT_PER_PAGE if per_page is None else per_page
    if page < 1:
        raise ValidationError("El parámetro 'page' debe ser mayor o igual a 1.")
    if per_page < 1 or per_page > _MAX_PER_PAGE:
        raise ValidationError(
            f"El parámetro 'per_page' debe estar entre 1 y {_MAX_PER_PAGE}."
        )

    pages = (total + per_page - 1) // per_page if total else 0
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    return {
        "items": items,
        "total": total,
        "pagination": {
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": pages,
            "has_next": page < pages,
            "has_prev": page > 1,
        },
    }


def _active_count_query():
    return db.session.query(func.count(Product.id)).filter(Product.is_active.is_(True))


def kpi_counts() -> dict:
    """Los cuatro KPI vivos de Gestión de Productos; misma regla que usa el histórico por cierre diario."""
    return {
        "total_products": _active_count_query().scalar() or 0,
        "low_stock": (
            _active_count_query()
            .filter(Product.current_stock > 0, Product.current_stock <= Product.minimum_stock)
            .scalar()
            or 0
        ),
        "out_of_stock": _active_count_query().filter(Product.current_stock == 0).scalar() or 0,
        "active_categories": (
            db.session.query(func.count(func.distinct(Product.category_id)))
            .filter(Product.is_active.is_(True))
            .scalar()
            or 0
        ),
    }


def products_summary() -> dict:
    """KPIs y paneles laterales. 'Categorías activas' = distinct category_id de productos activos."""
    counts = kpi_counts()
    total_active = counts["total_products"]

    rows = (
        db.session.query(Category.id, Category.name, func.count(Product.id))
        .outerjoin(
            Product,
            and_(Product.category_id == Category.id, Product.is_active.is_(True)),
        )
        .group_by(Category.id, Category.name)
        .order_by(func.count(Product.id).desc(), Category.name.asc())
        .all()
    )
    distribution = []
    for category_id, name, count in rows:
        percent = round((count * 100 / total_active), 1) if total_active else 0
        distribution.append(
            {
                "id": category_id,
                "name": name,
                "count": count,
                "percent": percent,
            }
        )

    alerts = (
        _product_query()
        .filter(
            Product.is_active.is_(True),
            or_(
                Product.current_stock == 0,
                and_(
                    Product.current_stock > 0,
                    Product.current_stock <= Product.minimum_stock,
                ),
            ),
        )
        .order_by(
            case((Product.current_stock == 0, 0), else_=1),
            Product.name.asc(),
        )
        .limit(_ALERT_LIMIT)
        .all()
    )
    recent = (
        _product_query()
        .filter(Product.is_active.is_(True))
        .order_by(Product.created_at.desc(), Product.id.desc())
        .limit(_RECENT_LIMIT)
        .all()
    )
    return {
        "total_active": total_active,
        "low_stock": counts["low_stock"],
        "out_of_stock": counts["out_of_stock"],
        "active_categories": counts["active_categories"],
        "category_distribution": distribution,
        "inventory_alerts": [p.to_dict() for p in alerts],
        "recent_products": [p.to_dict() for p in recent],
    }


def _optional_brand(data: dict):
    """Texto local opcional. None significa «la fuente no trae marca», no «borrarla»."""
    if "brand" not in data or data.get("brand") is None:
        return None
    if not isinstance(data.get("brand"), str):
        raise ValidationError("La marca debe ser texto.")
    value = data.get("brand").strip()
    if len(value) > 100:
        raise ValidationError("La marca admite hasta 100 caracteres.")
    return value or None


def create_product(data: dict) -> Product:
    from app.services.movement_traceability_service import creation_fields

    from app.services.rbac_service import require_operational_permission

    require_operational_permission("products:write")
    validate_price_pairs(data, creation=True)
    creation_fields(data, "initial_stock")
    code = _validate_code(data.get("code"))

    name = (data.get("name") or "").strip()
    if not name:
        raise ValidationError("El nombre del producto es obligatorio.")

    category_id = _validate_category(data.get("category_id"))
    initial_stock = _non_negative_int(data, "current_stock", default=0)
    minimum_stock = _non_negative_int(data, "minimum_stock", default=0)
    is_service = data.get("is_service", False)
    if type(is_service) is not bool:
        raise ValidationError("El campo 'is_service' debe ser booleano.")
    if is_service and initial_stock:
        raise ValidationError("Un servicio no lleva existencias: el stock inicial debe ser 0.")
    purchase_price = _non_negative_price(data, "purchase_price")
    sale_price = _non_negative_price(data, "sale_price")

    product = Product(
        code=code,
        name=name,
        description=(data.get("description") or "").strip() or None,
        brand=_optional_brand(data),
        category_id=category_id,
        unit=_validate_unit(data),
        current_stock=0,
        # El stock inicial espera la aprobación: un alta rechazada no deja existencias ni movimientos.
        proposed_initial_stock=initial_stock or None,
        is_service=is_service,
        minimum_stock=minimum_stock,
        purchase_price=purchase_price,
        sale_price=sale_price,
        sale_price_currency=data["sale_price_currency"],
        purchase_price_currency=data["purchase_price_currency"],
        image_url=(data.get("image_url") or "").strip() or None,
        is_active=False,
        approval_status="pending",
        proposed_by_user_id=current_user.id,
    )
    try:
        db.session.add(product)
        db.session.flush()
        product_state_history_service.record_baseline(product, datetime.utcnow())
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return product


def product_state_at(product_id: int, ts: datetime) -> dict:
    """Estado histórico de minimum_stock e is_active (delegado, solo lectura)."""
    return product_state_history_service.state_at(product_id, ts)


def update_product(product_id: int, data: dict) -> Product:
    """PUT/PATCH presentan una propuesta; nunca editan el producto vigente."""
    from app.services.product_change_service import propose_change

    return propose_change(product_id, data)


def decide_product(product_id: int, *, approve: bool, reason: str | None = None, proposal_id=None) -> Product:
    from app.services.rbac_service import require_operational_permission

    from app.services.product_change_service import decide_change, locked_product

    require_operational_permission("products:approve")
    if proposal_id is not None:
        return decide_change(product_id, proposal_id, approve=approve, reason=reason)
    product = locked_product(product_id)
    if product.proposed_by_user_id == current_user.id:
        raise ValidationError("Quien propone el producto no puede aprobarlo ni rechazarlo.", status_code=403)
    if product.approval_status != "pending":
        raise ConflictError("El producto ya no está pendiente de aprobación.")
    previous_active = bool(product.is_active)
    try:
        if approve:
            # La categoría pudo desactivarse mientras el alta esperaba revisión.
            _validate_category(product.category_id)
            product.approval_status = "approved"
            product.is_active = True
            product.approved_by_user_id = current_user.id
            product.approved_at = datetime.utcnow()
            product.rejection_reason = None
            if product.proposed_initial_stock:
                inventory_service.register_entry(
                    {"product_id": product.id, "quantity": product.proposed_initial_stock,
                     "reason": _INITIAL_STOCK_REASON},
                    commit=False, creation_origin="initial_stock",
                )
                product.proposed_initial_stock = None
        else:
            reason = (reason or "").strip()
            if not reason or len(reason) > 1000:
                raise ValidationError("Indique un motivo de rechazo de hasta 1000 caracteres.")
            product.approval_status = "rejected"
            product.is_active = False
            product.rejection_reason = reason
            product.approved_by_user_id = current_user.id
            product.approved_at = datetime.utcnow()
        if previous_active != bool(product.is_active):
            product_state_history_service.record_change(
                product.id, product.minimum_stock, product.minimum_stock,
                previous_active, bool(product.is_active), datetime.utcnow())
        db.session.commit()
        return product
    except Exception:
        db.session.rollback()
        raise


def deactivate_product(product_id: int) -> Product:
    """La baja lógica también requiere propuesta y aprobación."""
    return update_product(product_id, {"is_active": False})
