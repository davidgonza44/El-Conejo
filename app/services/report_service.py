"""Consultas agregadas de SOLO LECTURA para los reportes del MVP.

Este módulo nunca modifica datos: no hay add/commit, únicamente SELECT con
agrupaciones y sumas. Los montos Numeric se convierten a float para JSON.

Conceptos alineados con el inventario de la ferretería: salidas de mercancía,
movimientos de inventario, notas de entrega, stock actual y stock mínimo.
"""
import math
from datetime import datetime, timedelta

from sqlalchemy import and_, case, func, or_

from app.extensions import db
from app.models import (
    Category,
    DeliveryNote,
    DeliveryNoteItem,
    Product,
    StockMovement,
    User,
)
from app.models.delivery_note import STATUS_CANCELLED, STATUS_ISSUED
from app.models.stock_movement import (
    MOVEMENT_AJUSTE,
    MOVEMENT_ENTRADA,
    MOVEMENT_SALIDA,
)
from app.services.exceptions import ValidationError
from app.utils.permissions import response_includes_sale_price

# ---------------------------------------------------------------------------
# Validación de parámetros (errores -> 400 vía ValidationError)
# ---------------------------------------------------------------------------


def _parse_date(value: str, field: str) -> datetime:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d")
    except (ValueError, AttributeError):
        raise ValidationError(f"El parámetro '{field}' debe tener formato YYYY-MM-DD.")


def _parse_date_range(
    date_from: str | None, date_to: str | None
) -> tuple[datetime | None, datetime | None]:
    """Devuelve (inicio, fin_exclusivo). date_to es inclusivo hasta fin de día."""
    start = _parse_date(date_from, "date_from") if date_from else None
    end = None
    if date_to:
        end = _parse_date(date_to, "date_to") + timedelta(days=1)
    if start is not None and end is not None and start >= end:
        raise ValidationError("'date_from' no puede ser mayor que 'date_to'.")
    return start, end


def _parse_positive_int(value, field: str, default: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValidationError(f"El parámetro '{field}' debe ser un entero mayor o igual a 1.")
    if parsed < 1:
        raise ValidationError(f"El parámetro '{field}' debe ser un entero mayor o igual a 1.")
    return parsed


def _parse_multiplier(value) -> float:
    if value is None or str(value).strip() == "":
        return 3.0
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        raise ValidationError("El parámetro 'multiplier' debe ser numérico y mayor o igual a 1.")
    if not math.isfinite(parsed) or parsed < 1:
        raise ValidationError("El parámetro 'multiplier' debe ser numérico y mayor o igual a 1.")
    return parsed


def _apply_date_range(query, column, start, end):
    if start is not None:
        query = query.filter(column >= start)
    if end is not None:
        query = query.filter(column < end)
    return query


def _money(value) -> float | None:
    return round(float(value or 0), 2) if response_includes_sale_price() else None


# ---------------------------------------------------------------------------
# 1. Resumen general del dashboard
# ---------------------------------------------------------------------------


def dashboard_summary() -> dict:
    total_products = db.session.query(func.count(Product.id)).scalar() or 0
    active_products = (
        db.session.query(func.count(Product.id))
        .filter(Product.is_active.is_(True))
        .scalar()
        or 0
    )
    low_stock_products = (
        db.session.query(func.count(Product.id))
        .filter(Product.is_active.is_(True), Product.current_stock <= Product.minimum_stock)
        .scalar()
        or 0
    )
    total_categories = db.session.query(func.count(Category.id)).scalar() or 0
    total_movements = db.session.query(func.count(StockMovement.id)).scalar() or 0

    notes_by_status = dict(
        db.session.query(DeliveryNote.status, func.count(DeliveryNote.id))
        .group_by(DeliveryNote.status)
        .all()
    )
    total_issued, avg_issued = (
        db.session.query(
            func.coalesce(func.sum(DeliveryNote.total_amount), 0),
            func.coalesce(func.avg(DeliveryNote.total_amount), 0),
        )
        .filter(DeliveryNote.status == STATUS_ISSUED)
        .one()
    )

    return {
        "total_products": total_products,
        "active_products": active_products,
        "inactive_products": total_products - active_products,
        "low_stock_products": low_stock_products,
        "total_categories": total_categories,
        "total_inventory_movements": total_movements,
        "issued_delivery_notes": notes_by_status.get(STATUS_ISSUED, 0),
        "cancelled_delivery_notes": notes_by_status.get(STATUS_CANCELLED, 0),
        "total_amount_issued_delivery_notes": _money(total_issued),
        "average_amount_issued_delivery_notes": _money(avg_issued),
    }


# ---------------------------------------------------------------------------
# 2 y 3. Stock actual vs stock mínimo
# ---------------------------------------------------------------------------


def _products_with_category(extra_filter=None):
    query = (
        db.session.query(Product, Category.name)
        .join(Category, Product.category_id == Category.id)
        .filter(Product.is_active.is_(True))
    )
    if extra_filter is not None:
        query = query.filter(extra_filter)
    return query.order_by(
        (Product.current_stock - Product.minimum_stock).asc(), Product.name.asc()
    ).all()


def stock_vs_minimum() -> dict:
    rows = _products_with_category()
    items = [
        {
            "product_id": product.id,
            "code": product.code,
            "name": product.name,
            "category_name": category_name,
            "current_stock": product.current_stock,
            "minimum_stock": product.minimum_stock,
            "difference": product.current_stock - product.minimum_stock,
        }
        for product, category_name in rows
    ]
    return {"count": len(items), "items": items}


def low_stock_products() -> dict:
    rows = _products_with_category(Product.current_stock <= Product.minimum_stock)
    items = [
        {
            "product_id": product.id,
            "code": product.code,
            "name": product.name,
            "category_name": category_name,
            "current_stock": product.current_stock,
            "minimum_stock": product.minimum_stock,
        }
        for product, category_name in rows
    ]
    return {"count": len(items), "items": items}


# ---------------------------------------------------------------------------
# 4. Productos sin movimiento en los últimos X días
# ---------------------------------------------------------------------------


def products_without_movement(days=None) -> dict:
    days = _parse_positive_int(days, "days", default=30)
    now = datetime.utcnow()
    cutoff = now - timedelta(days=days)

    last_movement = (
        db.session.query(
            StockMovement.product_id.label("product_id"),
            func.max(StockMovement.created_at).label("last_movement_at"),
        )
        .group_by(StockMovement.product_id)
        .subquery()
    )

    rows = (
        db.session.query(Product, Category.name, last_movement.c.last_movement_at)
        .join(Category, Product.category_id == Category.id)
        .outerjoin(last_movement, last_movement.c.product_id == Product.id)
        .filter(Product.is_active.is_(True))
        .filter(
            or_(
                last_movement.c.last_movement_at.is_(None),
                last_movement.c.last_movement_at < cutoff,
            )
        )
        .all()
    )

    items = []
    for product, category_name, last_at in rows:
        # Si nunca tuvo movimientos, se cuenta desde la creación del producto.
        reference = last_at or product.created_at
        items.append(
            {
                "product_id": product.id,
                "code": product.code,
                "name": product.name,
                "category_name": category_name,
                "current_stock": product.current_stock,
                "last_movement_at": last_at.isoformat() if last_at else None,
                "days_without_movement": max((now - reference).days, 0),
            }
        )

    items.sort(key=lambda item: item["days_without_movement"], reverse=True)
    return {"days": days, "count": len(items), "items": items}


# ---------------------------------------------------------------------------
# 5. Productos con exceso de stock
# ---------------------------------------------------------------------------


def excess_stock_products(multiplier=None) -> dict:
    multiplier = _parse_multiplier(multiplier)

    rows = (
        db.session.query(Product, Category.name)
        .join(Category, Product.category_id == Category.id)
        .filter(Product.is_active.is_(True))
        .filter(Product.current_stock >= Product.minimum_stock * multiplier)
        # Con minimum_stock = 0 cualquier stock cumple; se exige stock > 0
        # para no listar productos vacíos sin mínimo definido.
        .filter(or_(Product.minimum_stock > 0, Product.current_stock > 0))
        .all()
    )

    items = [
        {
            "product_id": product.id,
            "code": product.code,
            "name": product.name,
            "category_name": category_name,
            "current_stock": product.current_stock,
            "minimum_stock": product.minimum_stock,
            "excess_quantity": round(
                product.current_stock - product.minimum_stock * multiplier, 2
            ),
        }
        for product, category_name in rows
    ]
    items.sort(key=lambda item: item["excess_quantity"], reverse=True)
    return {"multiplier": multiplier, "count": len(items), "items": items}


# ---------------------------------------------------------------------------
# 6. Entradas vs salidas por día
# ---------------------------------------------------------------------------


def entries_vs_exits(date_from=None, date_to=None) -> dict:
    start, end = _parse_date_range(date_from, date_to)
    day = func.date(StockMovement.created_at).label("day")

    query = db.session.query(
        day,
        func.coalesce(
            func.sum(
                case(
                    (StockMovement.movement_type == MOVEMENT_ENTRADA, StockMovement.quantity),
                    else_=0,
                )
            ),
            0,
        ).label("entries_quantity"),
        func.coalesce(
            func.sum(
                case(
                    (StockMovement.movement_type == MOVEMENT_SALIDA, StockMovement.quantity),
                    else_=0,
                )
            ),
            0,
        ).label("exits_quantity"),
        func.sum(
            case((StockMovement.movement_type == MOVEMENT_ENTRADA, 1), else_=0)
        ).label("entries_count"),
        func.sum(
            case((StockMovement.movement_type == MOVEMENT_SALIDA, 1), else_=0)
        ).label("exits_count"),
    ).filter(StockMovement.movement_type.in_((MOVEMENT_ENTRADA, MOVEMENT_SALIDA)))

    query = _apply_date_range(query, StockMovement.created_at, start, end)
    rows = query.group_by(day).order_by(day.asc()).all()

    items = [
        {
            "date": row.day.isoformat() if hasattr(row.day, "isoformat") else str(row.day),
            "total_entries_quantity": int(row.entries_quantity),
            "total_exits_quantity": int(row.exits_quantity),
            "entries_count": int(row.entries_count),
            "exits_count": int(row.exits_count),
        }
        for row in rows
    ]
    return {"count": len(items), "items": items}


# ---------------------------------------------------------------------------
# 7. Movimientos agrupados por categoría
# ---------------------------------------------------------------------------


def movements_by_category(date_from=None, date_to=None) -> dict:
    start, end = _parse_date_range(date_from, date_to)

    query = (
        db.session.query(
            Category.id.label("category_id"),
            Category.name.label("category_name"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            StockMovement.movement_type == MOVEMENT_ENTRADA,
                            StockMovement.quantity,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("entries_quantity"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            StockMovement.movement_type == MOVEMENT_SALIDA,
                            StockMovement.quantity,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("exits_quantity"),
            func.sum(
                case((StockMovement.movement_type == MOVEMENT_AJUSTE, 1), else_=0)
            ).label("adjustments_count"),
            func.count(StockMovement.id).label("movements_count"),
        )
        .select_from(StockMovement)
        .join(Product, StockMovement.product_id == Product.id)
        .join(Category, Product.category_id == Category.id)
    )

    query = _apply_date_range(query, StockMovement.created_at, start, end)
    rows = (
        query.group_by(Category.id, Category.name)
        .order_by(func.count(StockMovement.id).desc())
        .all()
    )

    items = [
        {
            "category_id": row.category_id,
            "category_name": row.category_name,
            "total_entries_quantity": int(row.entries_quantity),
            "total_exits_quantity": int(row.exits_quantity),
            "total_adjustments_count": int(row.adjustments_count),
            "total_movements_count": int(row.movements_count),
        }
        for row in rows
    ]
    return {"count": len(items), "items": items}


# ---------------------------------------------------------------------------
# 8 y 9. Productos con más / menos salidas de inventario
# ---------------------------------------------------------------------------


def top_products_by_exits(date_from=None, date_to=None, limit=None) -> dict:
    start, end = _parse_date_range(date_from, date_to)
    limit = _parse_positive_int(limit, "limit", default=10)

    query = (
        db.session.query(
            Product.id.label("product_id"),
            Product.code.label("product_code"),
            Product.name.label("product_name"),
            Category.name.label("category_name"),
            func.coalesce(func.sum(StockMovement.quantity), 0).label("total_quantity"),
            func.count(StockMovement.id).label("total_movements"),
        )
        .select_from(StockMovement)
        .join(Product, StockMovement.product_id == Product.id)
        .join(Category, Product.category_id == Category.id)
        .filter(StockMovement.movement_type == MOVEMENT_SALIDA)
    )

    query = _apply_date_range(query, StockMovement.created_at, start, end)
    rows = (
        query.group_by(Product.id, Product.code, Product.name, Category.name)
        .order_by(func.sum(StockMovement.quantity).desc(), Product.name.asc())
        .limit(limit)
        .all()
    )

    items = [
        {
            "product_id": row.product_id,
            "product_code": row.product_code,
            "product_name": row.product_name,
            "category_name": row.category_name,
            "total_quantity": int(row.total_quantity),
            "total_movements": int(row.total_movements),
        }
        for row in rows
    ]
    return {"limit": limit, "count": len(items), "items": items}


def least_products_by_exits(date_from=None, date_to=None, limit=None) -> dict:
    start, end = _parse_date_range(date_from, date_to)
    limit = _parse_positive_int(limit, "limit", default=10)

    # LEFT JOIN con las condiciones dentro del ON para incluir productos
    # activos con 0 salidas en el período.
    join_condition = and_(
        StockMovement.product_id == Product.id,
        StockMovement.movement_type == MOVEMENT_SALIDA,
    )
    if start is not None:
        join_condition = and_(join_condition, StockMovement.created_at >= start)
    if end is not None:
        join_condition = and_(join_condition, StockMovement.created_at < end)

    total_quantity = func.coalesce(func.sum(StockMovement.quantity), 0)
    rows = (
        db.session.query(
            Product.id.label("product_id"),
            Product.code.label("product_code"),
            Product.name.label("product_name"),
            Category.name.label("category_name"),
            total_quantity.label("total_quantity"),
            func.count(StockMovement.id).label("total_movements"),
        )
        .select_from(Product)
        .join(Category, Product.category_id == Category.id)
        .outerjoin(StockMovement, join_condition)
        .filter(Product.is_active.is_(True))
        .group_by(Product.id, Product.code, Product.name, Category.name)
        .order_by(total_quantity.asc(), Product.name.asc())
        .limit(limit)
        .all()
    )

    items = [
        {
            "product_id": row.product_id,
            "product_code": row.product_code,
            "product_name": row.product_name,
            "category_name": row.category_name,
            "total_quantity": int(row.total_quantity),
            "total_movements": int(row.total_movements),
        }
        for row in rows
    ]
    return {"limit": limit, "count": len(items), "items": items}


# ---------------------------------------------------------------------------
# 10. Ajustes de inventario
# ---------------------------------------------------------------------------


def inventory_adjustments(date_from=None, date_to=None) -> dict:
    start, end = _parse_date_range(date_from, date_to)

    query = (
        db.session.query(StockMovement, Product, User)
        .join(Product, StockMovement.product_id == Product.id)
        .outerjoin(User, StockMovement.user_id == User.id)
        .filter(StockMovement.movement_type == MOVEMENT_AJUSTE)
    )
    query = _apply_date_range(query, StockMovement.created_at, start, end)
    rows = query.order_by(StockMovement.created_at.desc(), StockMovement.id.desc()).all()

    items = [
        {
            "movement_id": movement.id,
            "product_id": product.id,
            "product_code": product.code,
            "product_name": product.name,
            "previous_stock": movement.previous_stock,
            "new_stock": movement.new_stock,
            "quantity": movement.quantity,
            "reason": movement.reason,
            "user_name": user.name if user else None,
            "created_at": movement.created_at.isoformat() if movement.created_at else None,
        }
        for movement, product, user in rows
    ]
    summary = {
        "total_adjustments": len(items),
        "adjusted_products_count": len({item["product_id"] for item in items}),
    }
    return {"summary": summary, "count": len(items), "items": items}


# ---------------------------------------------------------------------------
# 11. Notas de entrega agrupadas por día
# ---------------------------------------------------------------------------


def delivery_notes_by_period(date_from=None, date_to=None) -> dict:
    start, end = _parse_date_range(date_from, date_to)
    day = func.date(DeliveryNote.created_at).label("day")

    query = db.session.query(
        day,
        func.sum(case((DeliveryNote.status == STATUS_ISSUED, 1), else_=0)).label(
            "issued_count"
        ),
        func.sum(case((DeliveryNote.status == STATUS_CANCELLED, 1), else_=0)).label(
            "cancelled_count"
        ),
        func.coalesce(
            func.sum(
                case(
                    (DeliveryNote.status == STATUS_ISSUED, DeliveryNote.total_amount),
                    else_=0,
                )
            ),
            0,
        ).label("issued_amount"),
        func.coalesce(
            func.sum(
                case(
                    (DeliveryNote.status == STATUS_CANCELLED, DeliveryNote.total_amount),
                    else_=0,
                )
            ),
            0,
        ).label("cancelled_amount"),
    )

    query = _apply_date_range(query, DeliveryNote.created_at, start, end)
    rows = query.group_by(day).order_by(day.asc()).all()

    items = [
        {
            "date": row.day.isoformat() if hasattr(row.day, "isoformat") else str(row.day),
            "issued_count": int(row.issued_count),
            "cancelled_count": int(row.cancelled_count),
            "issued_amount": _money(row.issued_amount),
            "cancelled_amount": _money(row.cancelled_amount),
        }
        for row in rows
    ]
    return {"count": len(items), "items": items}


# ---------------------------------------------------------------------------
# 12. Productos más entregados (solo notas emitidas)
# ---------------------------------------------------------------------------


def top_delivered_products(date_from=None, date_to=None, limit=None) -> dict:
    start, end = _parse_date_range(date_from, date_to)
    limit = _parse_positive_int(limit, "limit", default=10)

    total_quantity = func.coalesce(func.sum(DeliveryNoteItem.quantity), 0)
    query = (
        db.session.query(
            DeliveryNoteItem.product_id.label("product_id"),
            func.max(DeliveryNoteItem.product_code).label("product_code"),
            func.max(DeliveryNoteItem.product_name).label("product_name"),
            total_quantity.label("total_quantity"),
            func.coalesce(func.sum(DeliveryNoteItem.line_total), 0).label("total_amount"),
            func.count(func.distinct(DeliveryNoteItem.delivery_note_id)).label(
                "notes_count"
            ),
        )
        .select_from(DeliveryNoteItem)
        .join(DeliveryNote, DeliveryNoteItem.delivery_note_id == DeliveryNote.id)
        .filter(DeliveryNote.status == STATUS_ISSUED)
    )

    query = _apply_date_range(query, DeliveryNote.created_at, start, end)
    rows = (
        query.group_by(DeliveryNoteItem.product_id)
        .order_by(total_quantity.desc())
        .limit(limit)
        .all()
    )

    items = [
        {
            "product_id": row.product_id,
            "product_code": row.product_code,
            "product_name": row.product_name,
            "total_quantity": round(float(row.total_quantity), 2),
            "total_amount": _money(row.total_amount),
            "notes_count": int(row.notes_count),
        }
        for row in rows
    ]
    return {"limit": limit, "count": len(items), "items": items}


# ---------------------------------------------------------------------------
# 13 y 14. Notas de entrega por usuario y por cliente (solo emitidas)
# ---------------------------------------------------------------------------


def delivery_notes_by_user(date_from=None, date_to=None) -> dict:
    start, end = _parse_date_range(date_from, date_to)

    total_amount = func.coalesce(func.sum(DeliveryNote.total_amount), 0)
    query = (
        db.session.query(
            User.id.label("user_id"),
            User.name.label("user_name"),
            func.count(DeliveryNote.id).label("notes_count"),
            total_amount.label("total_amount"),
        )
        .select_from(DeliveryNote)
        .join(User, DeliveryNote.created_by_user_id == User.id)
        .filter(DeliveryNote.status == STATUS_ISSUED)
    )

    query = _apply_date_range(query, DeliveryNote.created_at, start, end)
    # Sin precios tampoco se revela una clasificación derivada de importes.
    order = total_amount.desc() if response_includes_sale_price() else func.count(DeliveryNote.id).desc()
    rows = query.group_by(User.id, User.name).order_by(order, User.id.asc()).all()

    items = [
        {
            "user_id": row.user_id,
            "user_name": row.user_name,
            "notes_count": int(row.notes_count),
            "total_amount": _money(row.total_amount),
        }
        for row in rows
    ]
    return {"count": len(items), "items": items}


def delivery_notes_by_customer(date_from=None, date_to=None, limit=None) -> dict:
    start, end = _parse_date_range(date_from, date_to)
    limit = _parse_positive_int(limit, "limit", default=10)

    total_amount = func.coalesce(func.sum(DeliveryNote.total_amount), 0)
    query = db.session.query(
        DeliveryNote.customer_name.label("customer_name"),
        func.count(DeliveryNote.id).label("notes_count"),
        total_amount.label("total_amount"),
    ).filter(DeliveryNote.status == STATUS_ISSUED)

    query = _apply_date_range(query, DeliveryNote.created_at, start, end)
    order = total_amount.desc() if response_includes_sale_price() else func.count(DeliveryNote.id).desc()
    rows = (
        query.group_by(DeliveryNote.customer_name)
        .order_by(order, DeliveryNote.customer_name.asc())
        .limit(limit)
        .all()
    )

    items = [
        {
            "customer_name": row.customer_name,
            "notes_count": int(row.notes_count),
            "total_amount": _money(row.total_amount),
        }
        for row in rows
    ]
    return {"limit": limit, "count": len(items), "items": items}
