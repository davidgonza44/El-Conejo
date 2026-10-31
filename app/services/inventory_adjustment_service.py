"""Aprobación de ajustes: manuales y los que propone una conciliación.

Un ajuste manual fija un stock objetivo y exige que el stock no haya cambiado.
Uno de conciliación aplica la diferencia medida en el corte sobre el stock vigente,
así las ventas posteriores al corte se conservan. Ambos los aprueba otra persona.
"""
from datetime import datetime, timedelta

from flask_login import current_user

from app.extensions import db
from app.models import InventoryAdjustmentRequest, StockMovement
from app.models.stock_movement import MOVEMENT_AJUSTE
from app.services import inventory_service, rbac_service
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.movement_traceability_service import creation_fields

CREATE = "inventory_adjustments:create"
APPROVE = "inventory_adjustments:approve"
# Holgura ante relojes y transacciones que confirman justo después de la foto del corte.
SNAPSHOT_MARGIN = timedelta(minutes=5)


def _only(data, allowed):
    if set(data) - set(allowed):
        raise ValidationError("La petición contiene campos no admitidos.")


def _last_movement(product_id):
    # Locking read sees the latest committed row even under REPEATABLE READ.
    row = (StockMovement.query.filter_by(product_id=product_id)
           .order_by(StockMovement.id.desc()).with_for_update().first())
    return row.id if row else None


def create_request(data):
    rbac_service.require_operational_permission(CREATE)
    _only(data, ("product_id", "new_stock", "reason", "external_reference"))
    trace = creation_fields(data, "manual")
    reason = inventory_service._reason(data, required=True)
    new_stock = inventory_service._integer(data.get("new_stock"), "new_stock")
    if not 0 <= new_stock <= 2147483647:
        raise ValidationError("El stock objetivo debe estar entre 0 y 2147483647.")
    try:
        product = inventory_service._get_active_product_locked(data.get("product_id"), allow_inactive=True)
        if product.current_stock == new_stock:
            raise ValidationError("El ajuste no tiene efecto sobre el stock actual.")
        if not product.is_active and new_stock != 0:
            raise ConflictError(f"El producto '{product.name}' está inactivo; solo admite un ajuste a 0.")
        row = InventoryAdjustmentRequest(
            product_id=product.id, requester_user_id=current_user.id,
            previous_stock=product.current_stock, previous_movement_id=_last_movement(product.id),
            new_stock=new_stock, reason=reason, external_reference=trace["external_reference"], status="pending")
        db.session.add(row)
        db.session.commit()
        return row
    except Exception:
        db.session.rollback()
        raise


def _read_query():
    if rbac_service.can_operate(current_user.role, APPROVE):
        return InventoryAdjustmentRequest.query
    rbac_service.require_operational_permission(CREATE)
    return InventoryAdjustmentRequest.query.filter_by(requester_user_id=current_user.id)


def list_requests(params):
    query = _read_query()
    state = params.get("status")
    if state:
        if state not in ("pending", "approved", "rejected"):
            raise ValidationError("Estado no válido.")
        query = query.filter_by(status=state)
    page = inventory_service._positive_parameter(params.get("page", 1), "page")
    per_page = inventory_service._positive_parameter(params.get("per_page", 20), "per_page")
    if per_page > 100:
        raise ValidationError("per_page no puede superar 100.")
    total = query.count()
    rows = query.order_by(InventoryAdjustmentRequest.id.desc()).offset((page - 1) * per_page).limit(per_page).all()
    return {"items": [row.to_dict() for row in rows], "total": total, "page": page, "per_page": per_page}


def get_request(request_id):
    row = _read_query().filter_by(id=request_id).first()
    if row is None:
        raise NotFoundError("Solicitud no encontrada.")
    return row


def _pending_locked(request_id):
    row = (InventoryAdjustmentRequest.query.filter_by(id=request_id)
           .populate_existing().with_for_update().first())
    if row is None:
        raise NotFoundError("Solicitud no encontrada.")
    if row.requester_user_id == current_user.id:
        raise ValidationError("El solicitante no puede revisar su propia solicitud.", status_code=403)
    if row.status != "pending":
        raise ConflictError("La solicitud ya fue revisada.")
    return row


def review_request(request_id, data, *, approve):
    rbac_service.require_operational_permission(APPROVE)
    _only(data, ("review_comment",))
    comment = inventory_service._reason({"reason": data.get("review_comment", "")})
    try:
        row = _pending_locked(request_id)
        if approve:
            _apply(row)
        row.status = "approved" if approve else "rejected"
        row.reviewer_user_id = current_user.id
        row.reviewed_at = datetime.utcnow()
        row.review_comment = comment
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    if approve:
        from app.services import notification_service
        notification_service.product_stock_changed(row.product)
    return row


def _reconciliation_target(line, product):
    """Stock vigente + diferencia del corte; rechaza si otro ajuste ya pudo corregirla."""
    run = line.run
    if run.source_type == "automatic" and product.code.upper() != line.product_code.upper():
        raise ConflictError("El código del producto cambió desde el corte; revise el catálogo antes de ajustar.")
    reference = run.cut.local_snapshot_at if run.cut and run.cut.local_snapshot_at else run.imported_at
    later = (StockMovement.query.filter(
        StockMovement.product_id == product.id, StockMovement.movement_type == MOVEMENT_AJUSTE,
        StockMovement.created_at >= reference - SNAPSHOT_MARGIN).first())
    if later is not None:
        raise ConflictError(
            f"El producto tuvo un ajuste (MOV-{later.id:06d}) después del corte; esta diferencia pudo "
            "corregirse ya. Use un corte más reciente.")
    target = product.current_stock + line.difference
    if target < 0:
        raise ConflictError(
            f"Aplicar la diferencia ({line.difference:+d}) sobre el stock vigente ({product.current_stock}) "
            "lo dejaría negativo. Revise el conteo con un corte nuevo.")
    if not product.is_active and target != 0:
        raise ConflictError(f"El producto '{product.name}' está inactivo; solo admite un ajuste a 0.")
    return target


def create_from_reconciliation(line, reason):
    """Solicitud desde una discrepancia conciliada. La conciliación ya verificó su permiso
    (reconciliation:resolve) y bloqueó la línea; la transacción la confirma el llamador."""
    if InventoryAdjustmentRequest.query.filter_by(reconciliation_line_id=line.id, status="pending").first():
        raise ConflictError("La discrepancia ya tiene una solicitud de ajuste pendiente.")
    product = inventory_service._get_active_product_locked(line.product_id, allow_inactive=True)
    target = _reconciliation_target(line, product)
    row = InventoryAdjustmentRequest(
        product_id=product.id, requester_user_id=current_user.id, previous_stock=product.current_stock,
        previous_movement_id=_last_movement(product.id), new_stock=target, reason=reason,
        status="pending", reconciliation_line_id=line.id)
    db.session.add(row)
    db.session.flush()
    return row


def _apply(row):
    line = None
    if row.reconciliation_line_id is not None:
        from app.models.reconciliation import ReconciliationLine

        # Línea antes que producto: el mismo orden que usa la resolución de la conciliación.
        line = (ReconciliationLine.query.filter_by(id=row.reconciliation_line_id)
                .populate_existing().with_for_update().one())
    product = inventory_service._get_active_product_locked(row.product_id, allow_inactive=True)
    if line is not None:
        if line.review_status == "resolved":
            raise ConflictError("La discrepancia ya fue resuelta; rechace esta solicitud.")
        # Las ventas posteriores al corte no invalidan la solicitud: se recalcula sobre el stock vigente.
        row.new_stock = _reconciliation_target(line, product)
        row.previous_stock, row.previous_movement_id = product.current_stock, _last_movement(product.id)
        data = {"product_id": row.product_id, "new_stock": row.new_stock, "reason": row.reason}
        origin = None  # el origen visible es la línea de conciliación que enlaza el movimiento
    else:
        if product.current_stock != row.previous_stock or _last_movement(product.id) != row.previous_movement_id:
            raise ConflictError("El stock cambió desde la solicitud. Rechácela y solicite un ajuste actualizado.")
        data = {"product_id": row.product_id, "new_stock": row.new_stock, "reason": row.reason,
                "external_reference": row.external_reference}
        origin = "manual"
    movement = inventory_service.register_adjustment(data, commit=False, creation_origin=origin)
    row.stock_movement_id = movement.id
    if line is not None:
        line.review_status, line.resolution_action = "resolved", "adjustment"
        line.resolved_by_user_id, line.resolved_at = row.requester_user_id, datetime.utcnow()
        line.resolution_note, line.stock_movement_id = row.reason, movement.id
