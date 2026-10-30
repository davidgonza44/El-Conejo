"""Canonical supplier validation and atomic mutations. No inventory operations."""
import re
from datetime import datetime

from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.purchase import Supplier
from app.services.exceptions import ValidationError, ConflictError, NotFoundError
from app.services.purchase_service import only, text_value

FIELDS = {"name": 150, "tax_id": 30, "contact_name": 150, "contact_title": 150,
          "phone": 30, "email": 150, "address": 300, "city": 100,
          "state_province": 100, "category": 100, "notes": 300}
REQUIRED = {"name", "tax_id", "contact_name", "phone", "email", "category"}


def authorize():
    from app.services.rbac_service import require_operational_permission

    require_operational_permission("suppliers:propose")


def get_supplier(supplier_id, lock=False):
    query = Supplier.query.filter_by(id=supplier_id)
    if lock:
        query = query.populate_existing().with_for_update()
    supplier = query.first()
    if supplier is None:
        raise NotFoundError("Proveedor no encontrado.")
    return supplier


def validated(data):
    only(data, (*FIELDS, "is_active"))
    result = {key: text_value(data.get(key, ""), key, size, key in REQUIRED) for key, size in FIELDS.items()}
    result["tax_id"] = result["tax_id"].upper()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", result["email"]):
        raise ValidationError("Correo electrónico inválido.")
    if not isinstance(data.get("is_active"), bool):
        raise ValidationError("Seleccione un estado válido.")
    result["is_active"] = data["is_active"]
    return result


def save(data, supplier_id=None):
    authorize()
    fields = validated(data)
    try:
        supplier = get_supplier(supplier_id, True) if supplier_id else Supplier()
        if supplier_id and supplier.approval_status == "pending" and supplier.proposed_by_user_id != current_user.id:
            raise ValidationError("Solo puede editar sus propias propuestas pendientes.", status_code=403)
        if supplier_id and supplier.approval_status == "approved":
            # Proveedor aprobado: el cambio queda como propuesta; los datos oficiales no se tocan.
            from app.services import supplier_change_service

            supplier_change_service.propose(supplier, fields)
            db.session.commit()
            return supplier
        for key, value in fields.items():
            setattr(supplier, key, value)
        supplier.approval_status = "pending"
        supplier.is_active = False
        supplier.proposed_by_user_id = current_user.id
        supplier.approved_by_user_id = None
        supplier.approved_at = None
        supplier.rejection_reason = None
        supplier.updated_at = datetime.utcnow()
        db.session.add(supplier)
        db.session.commit()
        return supplier
    except IntegrityError:
        db.session.rollback()
        raise ConflictError("Ya existe un proveedor con ese RIF.")
    except Exception:
        db.session.rollback()
        raise


def change(supplier_id, action):
    """Conserva las rutas anteriores, sin permitir cambios directos al catálogo."""
    raise ValidationError(
        "Los cambios del proveedor requieren una propuesta del Asistente y aprobación del Administrador.",
        status_code=403,
    )


def decide(supplier_id, *, approve, reason=None):
    from app.services.rbac_service import require_operational_permission

    require_operational_permission("suppliers:approve")
    try:
        supplier = get_supplier(supplier_id, True)
        from app.services import supplier_change_service

        if supplier.approval_status == "approved" and supplier_change_service.pending_for(supplier.id):
            note = None if approve else text_value(reason or "", "Motivo", 1000, required=True)
            supplier_change_service.decide(supplier, approve=approve, reason=note)
            db.session.commit()
            return supplier
        if supplier.proposed_by_user_id == current_user.id:
            raise ValidationError("Quien propone el proveedor no puede aprobarlo ni rechazarlo.", status_code=403)
        if supplier.approval_status != "pending":
            raise ConflictError("El proveedor ya no está pendiente de aprobación.")
        if approve:
            supplier.approval_status = "approved"
            supplier.is_active = True
            supplier.approved_by_user_id = current_user.id
            supplier.approved_at = datetime.utcnow()
            supplier.rejection_reason = None
        else:
            note = text_value(reason or "", "Motivo", 1000, required=True)
            supplier.approval_status = "rejected"
            supplier.is_active = False
            supplier.rejection_reason = note
            supplier.approved_by_user_id = current_user.id
            supplier.approved_at = datetime.utcnow()
        db.session.commit()
        return supplier
    except Exception:
        db.session.rollback()
        raise
