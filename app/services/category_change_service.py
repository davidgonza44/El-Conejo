"""Propuestas separadas y decisiones atómicas sobre categorías aprobadas."""
from datetime import datetime

from flask_login import current_user
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import Category, CategoryChange
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.rbac_service import require_operational_permission


def pending_condition():
    return CategoryChange.query.filter(
        CategoryChange.category_id == Category.id, CategoryChange.status == "pending").exists()


def latest_changes(category_ids):
    if not category_ids:
        return {}
    latest = db.session.query(func.max(CategoryChange.id).label("id")).filter(
        CategoryChange.category_id.in_(category_ids)).group_by(CategoryChange.category_id).subquery()
    query = CategoryChange.query.join(latest, CategoryChange.id == latest.c.id).options(
        selectinload(CategoryChange.proposed_by_user), selectinload(CategoryChange.reviewed_by_user))
    return {row.category_id: row for row in query}


def review_payload(proposal):
    pending = proposal is not None and proposal.status == "pending"
    return {
        "has_pending_change": pending,
        "pending_change": proposal.to_dict() if pending else None,
        "last_change": proposal.to_dict() if proposal else None,
    }


def _locked_category(category_id):
    category = Category.query.filter_by(id=category_id).populate_existing().with_for_update().first()
    if category is None:
        raise NotFoundError("La categoría no existe.")
    return category


def _require_without_active_products(category):
    """Desactivar una categoría con productos activos los dejaría en una categoría inactiva."""
    from app.models import Product

    active = Product.query.filter_by(category_id=category.id, is_active=True).count()
    if active:
        raise ConflictError(
            f"La categoría tiene {active} producto(s) activo(s). Reasígnelos o desactívelos antes de desactivarla.")


def propose_change(category_id, data):
    from app.services.category_service import _category_changes

    require_operational_permission("categories:propose")
    try:
        category = _locked_category(category_id)
        if category.approval_status != "approved":
            raise ConflictError("Solo se proponen modificaciones de categorías aprobadas.")
        if CategoryChange.query.filter_by(category_id=category.id, status="pending").first():
            raise ConflictError("La categoría ya tiene una propuesta pendiente de revisión.")
        changes = {key: value for key, value in _category_changes(category.id, data).items()
                   if getattr(category, key) != value}
        if not changes:
            raise ValidationError("La propuesta no contiene cambios respecto a la categoría vigente.")
        if changes.get("is_active") is False:
            _require_without_active_products(category)
        db.session.add(CategoryChange(category_id=category.id, proposed_by_user_id=current_user.id,
            changes=changes, previous_values={key: getattr(category, key) for key in changes}))
        db.session.commit()
        return category
    except Exception:
        db.session.rollback()
        raise


def _validated_changes(category, proposal):
    from app.services.category_service import _category_changes

    if category.approval_status != "approved":
        raise ConflictError("La categoría ya no está aprobada.")
    if any(getattr(category, key) != value for key, value in proposal.previous_values.items()):
        raise ConflictError("Los valores vigentes cambiaron. Rechace la propuesta y solicite una nueva.")
    changes = _category_changes(category.id, proposal.changes)
    if changes.get("is_active") is False:
        _require_without_active_products(category)  # pudo aprobarse un producto mientras tanto
    return changes


def decide_change(category_id, proposal_id, *, approve, reason=None):
    require_operational_permission("categories:approve")
    if type(proposal_id) is not int:
        raise ValidationError("Indique el ID de la propuesta que está revisando.")
    reason = str(reason or "").strip()
    if not approve and (not reason or len(reason) > 1000):
        raise ValidationError("Indique un motivo de rechazo de hasta 1000 caracteres.")
    try:
        category = _locked_category(category_id)
        proposal = CategoryChange.query.filter_by(id=proposal_id, category_id=category.id).populate_existing().with_for_update().first()
        if proposal is None:
            raise NotFoundError("La propuesta no pertenece a la categoría.")
        if proposal.proposed_by_user_id == current_user.id:
            raise ValidationError("Quien propone no puede aprobar ni rechazar su propio cambio.", status_code=403)
        if proposal.status != "pending":
            raise ConflictError("La propuesta ya fue revisada.")
        changes = _validated_changes(category, proposal) if approve else {}
        values = dict(status="approved" if approve else "rejected", reviewed_by_user_id=current_user.id,
                      reviewed_at=datetime.utcnow(), rejection_reason=None if approve else reason)
        changed = CategoryChange.query.filter(CategoryChange.id == proposal.id, CategoryChange.status == "pending",
            or_(CategoryChange.proposed_by_user_id.is_(None), CategoryChange.proposed_by_user_id != current_user.id)).update(
                values, synchronize_session=False)
        if changed != 1:
            raise ConflictError("Otro administrador ya decidió esta propuesta.")
        for key, value in changes.items():
            setattr(category, key, value)
        db.session.commit()
        return category
    except IntegrityError:
        db.session.rollback()
        raise ConflictError("El nombre propuesto ya existe; no se aplicaron cambios.") from None
    except Exception:
        db.session.rollback()
        raise
