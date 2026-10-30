"""Propuestas de modificación de proveedores aprobados (asistente propone, administrador decide).

Los datos oficiales del proveedor no cambian hasta la aprobación; un rechazo los deja intactos.
Sin la tabla supplier_changes (scripts/migrate_supplier_changes.py) la edición de un proveedor
aprobado se rechaza con un mensaje claro en lugar de sobrescribir sus datos.
"""
from datetime import datetime

from flask_login import current_user

from app.extensions import db
from app.models import SupplierChange
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.utils.schema import table_ready

_TABLE = "supplier_changes"


def available() -> bool:
    return table_ready(_TABLE)


def pending_for(supplier_id):
    if not available():
        return None
    return SupplierChange.query.filter_by(supplier_id=supplier_id, status="pending").first()


def propose(supplier, fields: dict):
    """Registra la propuesta sobre ``supplier`` (bloqueado por el llamador) sin modificarlo."""
    if not available():
        raise ConflictError("Las propuestas de modificación de proveedores requieren la migración "
                            "scripts/migrate_supplier_changes.py. El proveedor no se modificó.")
    if pending_for(supplier.id):
        raise ConflictError("El proveedor ya tiene una propuesta de modificación pendiente.")
    changes = {key: value for key, value in fields.items() if key != "is_active" and getattr(supplier, key) != value}
    if not changes:
        raise ValidationError("La propuesta no contiene cambios respecto a los datos vigentes del proveedor.")
    db.session.add(SupplierChange(supplier_id=supplier.id, proposed_by_user_id=current_user.id, changes=changes,
                                  previous_values={key: getattr(supplier, key) for key in changes}))


def decide(supplier, *, approve, reason):
    """Aplica o descarta la propuesta pendiente de ``supplier`` (bloqueado por el llamador)."""
    proposal = SupplierChange.query.filter_by(supplier_id=supplier.id, status="pending").with_for_update().first()
    if proposal is None:
        raise NotFoundError("El proveedor no tiene una propuesta pendiente.")
    if proposal.proposed_by_user_id == current_user.id:
        raise ValidationError("Quien propone el cambio no puede aprobarlo ni rechazarlo.", status_code=403)
    if approve:
        if any(getattr(supplier, key) != value for key, value in proposal.previous_values.items()):
            raise ConflictError("Los datos vigentes cambiaron. Rechace la propuesta y solicite una nueva.")
        for key, value in proposal.changes.items():
            setattr(supplier, key, value)
        supplier.updated_at = datetime.utcnow()
    proposal.status = "approved" if approve else "rejected"
    proposal.reviewed_by_user_id = current_user.id
    proposal.reviewed_at = datetime.utcnow()
    proposal.rejection_reason = None if approve else reason
