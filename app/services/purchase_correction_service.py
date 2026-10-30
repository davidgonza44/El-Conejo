"""Inventario propone; Administración decide. Cada diferencia crea un movimiento compensatorio."""
from datetime import datetime
from flask_login import current_user
from app.extensions import db
from app.models import StockMovement
from app.models.purchase import PurchaseReception
from app.models.purchase_correction import PurchaseReceptionCorrection as Correction, PurchaseReceptionCorrectionLine as Line
from app.services import inventory_service
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.purchase_service import get_order, integer, lock_products, only, text_value
from app.services.rbac_service import require_operational_permission


def _receipt(receipt_id):
    # La OC siempre se bloquea primero, igual que recepción y reposición de faltantes.
    row = db.session.get(PurchaseReception, receipt_id)
    if row is None:
        raise NotFoundError("Recepción no encontrada.")
    order = get_order(row.purchase_order_id, lock=True)
    if order.status not in ("closed_complete", "closed_partial") or len(order.receptions) != 1:
        raise ConflictError("Solo se corrige una recepción del flujo actual, sin reabrir la orden.")
    from app.services.replenishment_request_service import _covering_shortage_request
    if _covering_shortage_request(order.id) is not None:
        raise ConflictError("El faltante ya está en trámite. Resuelva o anule ese trámite antes de corregir la recepción.")
    return row, order


def _last_movement(product_id):
    row = (StockMovement.query.filter_by(product_id=product_id).order_by(StockMovement.id.desc())
           .with_for_update().first())
    return row.id if row else None


def propose(receipt_id, data):
    require_operational_permission("purchases:receive")
    only(data, ("items", "reason"))
    reason = text_value(data.get("reason"), "Motivo de corrección", 1000, required=True)
    try:
        receipt, order = _receipt(receipt_id)
        pending = Correction.query.filter_by(reception_id=receipt_id, status="pending").with_for_update().first()
        if pending:
            raise ConflictError("La recepción ya tiene una corrección pendiente.")
        sent = data.get("items")
        if not isinstance(sent, list) or not 1 <= len(sent) <= 100:
            raise ValidationError("Indique entre 1 y 100 cantidades corregidas.")
        lines = {line.id: line for line in order.lines}
        changed, seen = [], set()
        for item in sent:
            only(item, ("line_id", "quantity"))
            key = integer(item.get("line_id"), "Renglón")
            qty = integer(item.get("quantity"), "Cantidad corregida", minimum=0)
            line = lines.get(key)
            if line is None or key in seen or qty > line.quantity_ordered:
                raise ValidationError("Renglón duplicado, ajeno o cantidad superior a la pedida.")
            seen.add(key)
            if qty != line.quantity_received:
                changed.append((line, qty))
        if not changed:
            raise ValidationError("La corrección no contiene cambios.")
        products = lock_products(line.product_id for line, _ in changed)
        proposal = Correction(reception=receipt, requested_by_user_id=current_user.id, reason=reason)
        for line, qty in changed:
            product = products[line.product_id]
            proposal.lines.append(Line(purchase_order_line_id=line.id, product_id=line.product_id,
                previous_quantity=line.quantity_received, target_quantity=qty, expected_stock=product.current_stock,
                previous_movement_id=_last_movement(product.id)))
        db.session.add(proposal)
        db.session.commit()
        _announce(proposal)
        return proposal
    except Exception:
        db.session.rollback()
        raise


def decide(receipt_id, correction_id, data, *, approve):
    require_operational_permission("purchases:approve")
    only(data, () if approve else ("reason",))
    try:
        receipt, order = _receipt(receipt_id)
        proposal = (Correction.query.filter_by(id=correction_id, reception_id=receipt.id)
                    .populate_existing().with_for_update().first())
        if proposal is None:
            raise NotFoundError("Corrección no encontrada.")
        if proposal.status != "pending":
            raise ConflictError("La corrección ya fue decidida.")
        if proposal.requested_by_user_id == current_user.id:
            raise ValidationError("Quien solicita la corrección no puede decidirla.", status_code=403)
        if approve:
            _apply(order, proposal)
        else:
            proposal.decision_note = text_value(data.get("reason"), "Motivo de rechazo", 1000, required=True)
        proposal.status = "approved" if approve else "rejected"
        proposal.decided_by_user_id = current_user.id
        proposal.decided_at = datetime.utcnow()
        db.session.commit()
        _announce(proposal)
        return proposal
    except Exception:
        db.session.rollback()
        raise


def _apply(order, proposal):
    products = lock_products(line.product_id for line in proposal.lines)
    order_lines = {line.id: line for line in order.lines}
    for line in proposal.lines:
        product = products[line.product_id]
        original = order_lines[line.purchase_order_line_id]
        if (product.current_stock != line.expected_stock or _last_movement(product.id) != line.previous_movement_id
                or original.quantity_received != line.previous_quantity):
            raise ConflictError("El stock o la recepción cambiaron después de solicitar la corrección. Rechace y solicite otra.")
        delta = line.target_quantity-line.previous_quantity
        register = inventory_service.register_entry if delta > 0 else inventory_service.register_exit
        movement = register({"product_id": line.product_id, "quantity": abs(delta),
                             "reason": f"Corrección COR-{proposal.id} de REC-{proposal.reception_id}"}, commit=False)
        line.stock_movement_id = movement.id
        original.quantity_received = line.target_quantity
        original.quantity_not_supplied = original.quantity_ordered-original.quantity_received
    order.status = "closed_partial" if any(line.quantity_not_supplied for line in order.lines) else "closed_complete"


def history(receipt_id):
    return [row.to_dict() for row in Correction.query.filter_by(reception_id=receipt_id)
            .order_by(Correction.id.desc()).limit(100).all()]


def _announce(proposal):
    from app.services import notification_service
    notification_service._safe(notification_service.emit, "replenishment", "Corrección de recepción actualizada",
        f"COR-{proposal.id}: {proposal.status}", permission="purchases:read",
        entity_type="purchase_reception_correction", entity_id=proposal.id,
        href=f"/purchases/receptions?detail={proposal.reception_id}")
    if proposal.status == "approved":
        from app.models import Product
        for line in proposal.lines:
            notification_service._safe(notification_service.product_stock_changed, db.session.get(Product, line.product_id))
