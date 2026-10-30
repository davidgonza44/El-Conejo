"""Lógica de negocio de categorías.

Convención de agregados (igual que products_summary): solo cuentan los productos
activos; el valor de inventario es SUM(current_stock * purchase_price).
"""
from datetime import datetime
import unicodedata

from flask_login import current_user

from sqlalchemy import case, func, or_
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Category, Product
from app.models.category import CATEGORY_COLORS, DEFAULT_CATEGORY_COLOR, CATEGORY_ICONS, DEFAULT_CATEGORY_ICON
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.rbac_service import require_operational_permission
from app.services import category_change_service
from app.utils.category_names import category_name_key
from app.services import catalog_valuation_service as valuation

STATUS_VALUES = ("all", "active", "inactive", "pending", "approved", "rejected")
SORT_KEYS = ("name-asc", "name-desc", "products-desc", "products-asc", "value-desc", "value-asc")
_NAME_MAX = 100
_DESCRIPTION_MAX = 255
_DEFAULT_PER_PAGE = 10
_MAX_PER_PAGE = 100


def _validate_name(data: dict, exclude_id: int | None = None) -> str:
    name = " ".join(unicodedata.normalize("NFKC", str(data.get("name") or "")).split())
    if not name:
        raise ValidationError("El nombre de la categoría es obligatorio.")
    if len(name) > _NAME_MAX:
        raise ValidationError(f"El nombre de la categoría no puede superar {_NAME_MAX} caracteres.")

    key = category_name_key(name)
    if not key:
        raise ValidationError("El nombre debe contener letras o números.")
    query = Category.query.filter(Category.normalized_name == key)
    if exclude_id is not None:
        query = query.filter(Category.id != exclude_id)
    if query.first() is not None:
        raise ConflictError(f"Ya existe una categoría con el nombre '{name}'.")
    return name


def _validate_description(data: dict) -> str | None:
    description = str(data.get("description") or "").strip()
    if len(description) > _DESCRIPTION_MAX:
        raise ValidationError(f"La descripción no puede superar {_DESCRIPTION_MAX} caracteres.")
    return description or None


def _validate_color(value) -> str:
    color = str(value or DEFAULT_CATEGORY_COLOR).strip().lower()
    if color not in CATEGORY_COLORS:
        raise ValidationError(f"Color inválido. Valores permitidos: {', '.join(CATEGORY_COLORS)}.")
    return color


def _validate_icon(value) -> str:
    if not isinstance(value, str) or value not in CATEGORY_ICONS:
        raise ValidationError(f"Icono inválido. Valores permitidos: {', '.join(CATEGORY_ICONS)}.")
    return value


def _validate_active(value) -> bool:
    if not isinstance(value, bool):
        raise ValidationError("El campo 'is_active' debe ser booleano.")
    return value


def get_category_or_404(category_id: int) -> Category:
    category = db.session.get(Category, category_id)
    if category is None:
        raise NotFoundError(f"La categoría con id {category_id} no existe.")
    return category


# --- Consultas con agregados (un solo JOIN, sin N+1) ------------------------

def _annotated_query():
    """(Category, productos activos, valor de inventario) por categoría; las vacías aparecen con 0."""
    currency = valuation.common_inventory_currency()
    stats = (
        db.session.query(
            Product.category_id.label("category_id"),
            func.count(Product.id).label("products"),
            func.coalesce(func.sum(case((Product.purchase_price_currency.in_([currency] if currency else []),
                Product.current_stock * Product.purchase_price), else_=None)), 0).label("value"),
        )
        .filter(Product.is_active.is_(True))
        .group_by(Product.category_id)
        .subquery()
    )
    products = func.coalesce(stats.c.products, 0)
    value = func.coalesce(stats.c.value, 0)
    query = db.session.query(Category, products, value).outerjoin(stats, stats.c.category_id == Category.id)
    return query, products, value


def _cost_visible() -> bool:
    from app.utils.permissions import response_includes_cost

    return response_includes_cost()


def effective_sort(sort: str | None) -> str:
    """Orden aplicado. Sin products:view_cost, value-asc y value-desc caen en name-asc.

    Ese orden usa el costo de inventario. Sustituirlo por el nombre evita que la
    secuencia de categorías revele cuál vale más.
    """
    key = (sort or SORT_KEYS[0]).strip()
    if key not in SORT_KEYS:
        raise ValidationError(f"Orden inválido: '{sort}'. Valores permitidos: {', '.join(SORT_KEYS)}.")
    if key in ("value-desc", "value-asc") and (not _cost_visible() or valuation.common_inventory_currency() is None):
        return "name-asc"
    return key


def _sort_clauses(sort: str | None, products, value):
    key = effective_sort(sort)
    options = {
        "name-asc": (Category.name.asc(),),
        "name-desc": (Category.name.desc(),),
        "products-desc": (products.desc(), Category.name.asc()),
        "products-asc": (products.asc(), Category.name.asc()),
        "value-desc": (value.desc(), Category.name.asc()),
        "value-asc": (value.asc(), Category.name.asc()),
    }
    return options[key]


def _payload(category: Category, products, value, monetary, include_value: bool) -> dict:
    data = category.to_dict()
    data["products_count"] = int(products or 0)
    if include_value:
        overall, categories = monetary
        current = categories.get(category.id, valuation.empty_valuation())
        data.update(current)
        amount, currency = valuation.scalar_valuation(current)
        data["inventory_value"] = float(amount) if amount is not None else None
        data["inventory_value_currency"] = currency
        data["inventory_share"] = current["totals_by_currency"][0]["share"] if currency else None
        data["value_rank_available"] = len(overall["totals_by_currency"]) == 1
    return data


def list_categories(
    search: str | None = None,
    status: str | None = None,
    sort: str | None = None,
    page: int | None = None,
    per_page: int | None = None,
    approval_status: str | None = None,
) -> dict:
    query, products, value = _annotated_query()
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(or_(Category.name.ilike(term), Category.description.ilike(term)))
    status = status or "all"
    if status not in STATUS_VALUES:
        raise ValidationError(f"Estado inválido: '{status}'. Valores permitidos: {', '.join(STATUS_VALUES)}.")
    if status in ("pending", "approved", "rejected"):
        query = approval_filter(query, status)
    elif status == "active":
        query = query.filter(Category.is_active.is_(True), Category.approval_status == "approved")
    elif status == "inactive":
        query = query.filter(Category.is_active.is_(status == "active"))
    query = approval_filter(query, approval_status)
    include_value = _cost_visible()
    query = query.order_by(*_sort_clauses(sort, products, value))
    total = query.order_by(None).count()

    pagination = None
    if page is not None or per_page is not None:
        page = 1 if page is None else page
        per_page = _DEFAULT_PER_PAGE if per_page is None else per_page
        if page < 1:
            raise ValidationError("El parámetro 'page' debe ser mayor o igual a 1.")
        if per_page < 1 or per_page > _MAX_PER_PAGE:
            raise ValidationError(f"El parámetro 'per_page' debe estar entre 1 y {_MAX_PER_PAGE}.")
        pages = (total + per_page - 1) // per_page if total else 0
        page = min(page, max(1, pages))
        query = query.offset((page - 1) * per_page).limit(per_page)
        pagination = {
            "total": total, "page": page, "per_page": per_page, "pages": pages,
            "has_next": page < pages, "has_prev": page > 1,
        }

    monetary = valuation.valuations() if include_value else None
    items = [_payload(category, count, amount, monetary, include_value) for category, count, amount in query.all()]
    proposals = category_change_service.latest_changes([item["id"] for item in items])
    for item in items:
        item.update(category_change_service.review_payload(proposals.get(item["id"])))
    return {"items": items, "total": total, "pagination": pagination}


def approval_filter(query, status):
    if status not in (None, "", "all", "pending", "approved", "rejected"):
        raise ValidationError("Estado de aprobación inválido.")
    pending = category_change_service.pending_condition()
    if status == "pending":
        return query.filter(or_(Category.approval_status == "pending", pending))
    if status == "approved":
        return query.filter(Category.approval_status == "approved", ~pending)
    if status == "rejected":
        return query.filter(Category.approval_status == "rejected")
    return query


def category_detail(category_id: int) -> dict:
    query, _, _ = _annotated_query()
    row = query.filter(Category.id == category_id).first()
    if row is None:
        raise NotFoundError(f"La categoría con id {category_id} no existe.")
    include_value = _cost_visible()
    payload = _payload(*row, valuation.valuations() if include_value else None, include_value)
    payload.update(category_change_service.review_payload(category_change_service.latest_changes([category_id]).get(category_id)))
    return payload


def categories_summary() -> dict:
    """KPIs y distribución por conteo de productos activos; incluye categorías con cero productos."""
    query, products, _ = _annotated_query()
    rows = query.order_by(products.desc(), Category.name.asc()).all()
    total_products = sum(int(count or 0) for _, count, _ in rows)
    active = sum(1 for category, _, _ in rows if category.is_active)
    payload = {
        "total": len(rows),
        "active": active,
        "inactive": len(rows) - active,
        "products": total_products,
        "distribution": [
            {
                "id": category.id,
                "name": category.name,
                "color": category.to_dict()["color"],
                "count": int(count or 0),
                "percent": round(int(count or 0) * 100 / total_products, 1) if total_products else 0,
            }
            for category, count, _ in rows
        ],
    }
    if _cost_visible():
        monetary, _ = valuation.valuations()
        payload.update(monetary)
        amount, currency = valuation.scalar_valuation(monetary)
        payload["inventory_value"] = float(amount) if amount is not None else None
        payload["inventory_value_currency"] = currency
    return payload


# --- Escrituras --------------------------------------------------------------

def _commit():
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ConflictError("Ya existe una categoría con un nombre equivalente.") from None


def create_category(data: dict) -> Category:
    require_operational_permission("categories:propose")
    if "is_active" in data:
        _validate_active(data["is_active"])
    category = Category(
        name=_validate_name(data),
        description=_validate_description(data),
        color=_validate_color(data.get("color")),
        icon=_validate_icon(data.get("icon", DEFAULT_CATEGORY_ICON)),
        is_active=False,
        approval_status="pending",
        proposed_by_user_id=current_user.id,
    )
    db.session.add(category)
    _commit()
    return category


def _editable_category(category_id: int, data: dict):
    require_operational_permission("categories:propose")
    category = Category.query.filter_by(id=category_id).populate_existing().with_for_update().first()
    if category is None:
        raise NotFoundError("La categoría no existe.")
    if category.approval_status == "pending" and category.proposed_by_user_id != current_user.id:
        raise ValidationError("Solo puede editar sus propias propuestas pendientes.", status_code=403)
    if category.approval_status != "approved" and data.get("is_active") is True:
        raise ValidationError("Una propuesta no puede activarse sin aprobación.")
    return category


def _category_changes(category_id: int, data: dict) -> dict:
    validators = {"name": lambda value: _validate_name({"name": value}, exclude_id=category_id),
                  "description": lambda value: _validate_description({"description": value}),
                  "color": _validate_color, "icon": _validate_icon, "is_active": _validate_active}
    if not data or set(data) - set(validators):
        raise ValidationError("La propuesta contiene campos no permitidos o está vacía.")
    return {key: validators[key](value) for key, value in data.items()}


def update_category(category_id: int, data: dict) -> Category:
    category = _editable_category(category_id, data)
    if category.approval_status == "approved":
        return category_change_service.propose_change(category_id, data)
    changes = _category_changes(category_id, data)
    for key, value in changes.items():
        setattr(category, key, value)
    category.approval_status = "pending"
    category.is_active = False
    category.proposed_by_user_id = current_user.id
    category.approved_by_user_id = None
    category.approved_at = None
    category.rejection_reason = None

    _commit()
    return category


def decide_category(category_id: int, *, approve: bool, reason: str | None = None, proposal_id=None) -> Category:
    """UPDATE condicional: una única decisión gana, aun con administradores simultáneos."""
    require_operational_permission("categories:approve")
    if proposal_id is not None:
        return category_change_service.decide_change(category_id, proposal_id, approve=approve, reason=reason)
    category = get_category_or_404(category_id)
    if category.proposed_by_user_id == current_user.id:
        raise ValidationError("Quien propone la categoría no puede aprobarla ni rechazarla.", status_code=403)
    if category.approval_status != "pending":
        raise ConflictError("La categoría ya no está pendiente de aprobación.")
    reason = str(reason or "").strip()
    if not approve and (not reason or len(reason) > 1000):
        raise ValidationError("Indique un motivo de rechazo de hasta 1000 caracteres.")
    values = dict(approval_status="approved" if approve else "rejected", is_active=approve,
                  approved_by_user_id=current_user.id, approved_at=datetime.utcnow(),
                  rejection_reason=None if approve else reason)
    changed = Category.query.filter(Category.id == category_id, Category.approval_status == "pending",
        or_(Category.proposed_by_user_id.is_(None), Category.proposed_by_user_id != current_user.id)).update(
        values, synchronize_session=False)
    if changed != 1:
        db.session.rollback()
        raise ConflictError("Otro administrador ya decidió esta categoría. Actualice el listado.")
    db.session.commit()
    db.session.refresh(category)
    return category


def duplicate_category(category_id: int) -> Category:
    """Duplicar también propone; no copia aprobación ni mueve productos."""
    require_operational_permission("categories:propose")
    source = get_category_or_404(category_id)
    name = f"{source.name} (Copia)"
    attempt = 2
    # ponytail: sondeo lineal de nombres; basta para copias manuales, no para lotes.
    while Category.query.filter(Category.normalized_name == category_name_key(name)).first() is not None:
        name = f"{source.name} (Copia {attempt})"
        attempt += 1
    if len(name) > _NAME_MAX:
        raise ValidationError("El nombre de la copia supera los 100 caracteres; acorte el nombre original primero.")

    return create_category(dict(name=name, description=source.description, color=source.color, icon=source.icon))


def delete_category(category_id: int) -> None:
    require_operational_permission("categories:propose")
    category = get_category_or_404(category_id)
    if category.approval_status == "approved" or category.proposed_by_user_id != current_user.id:
        raise ValidationError("Una categoría operativa se desactiva mediante una propuesta; no se elimina directamente.", status_code=403)

    products_count = Product.query.filter_by(category_id=category.id).count()
    if products_count > 0:
        raise ConflictError(
            f"No se puede eliminar la categoría '{category.name}' porque tiene "
            f"{products_count} producto(s) asociado(s). Reasigne los productos primero."
        )

    db.session.delete(category)
    db.session.commit()
