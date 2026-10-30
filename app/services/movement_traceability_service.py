"""Read-only origins from canonical document lines; never infer origins from text."""
from collections import defaultdict
from urllib.parse import quote

from flask import has_request_context
from flask_login import current_user

from app.extensions import db
from app.models import Sale, SaleLine
from app.models import DeliveryNote, DeliveryNoteItem, StockMovement
from app.models.purchase import PurchaseOrder, PurchaseReception, PurchaseReceptionLine
from app.models.reconciliation import ReconciliationLine, ReconciliationRun
from app.services.exceptions import ValidationError
from app.utils.permissions import role_has_permission
from app.utils.schema import table_ready

SERVER_FIELDS = frozenset({"source_type", "source_id", "creation_origin", "movement_number",
                           "display_reference", "source_url", "reference", "reference_number"})


def reject_server_fields(data):
    if SERVER_FIELDS.intersection(data):
        raise ValidationError("Los campos de origen y referencia automática son asignados por el servidor.")


def creation_fields(data, origin):
    """origin is an explicit internal keyword, never copied from HTTP data."""
    reject_server_fields(data)
    if origin not in (None, "manual", "initial_stock"):
        raise ValidationError("Origen de creación no permitido.")
    if "external_reference" in data and origin != "manual":
        raise ValidationError("La referencia externa solo se admite en movimientos manuales.")
    external = data.get("external_reference")
    if external is not None:
        if not isinstance(external, str):
            raise ValidationError("La referencia externa debe ser texto.")
        external = external.strip() or None
        if external and len(external) > 120:
            raise ValidationError("La referencia externa no puede superar 120 caracteres.")
    return {"creation_origin": origin, "external_reference": external}


def _access():
    if not has_request_context() or not current_user.is_authenticated:
        return set(), None
    permissions = {code for code in ("sales:read", "purchases:read", "reconciliation:read", "inventory:read", "delivery_notes:read")
                   if role_has_permission(current_user.role, code)}
    from app.services.sales_common import current_vendor_id

    return permissions, current_vendor_id()


def _sales(ids, access):
    permissions, vendor_id = access
    rows = (db.session.query(SaleLine.stock_movement_id, Sale.id, Sale.sale_number, Sale.seller_id)
            .join(Sale, Sale.id == SaleLine.sale_id).filter(SaleLine.stock_movement_id.in_(ids)).all())
    for row in rows:
        allowed = "sales:read" in permissions and (vendor_id is None or vendor_id == row.seller_id)
        url = "/sales/" + quote(row.sale_number, safe="") if allowed and row.sale_number else None
        yield row.stock_movement_id, ("sale", row.id, row.sale_number, url, "Venta")


def _receptions(ids, access):
    rows = (db.session.query(PurchaseReceptionLine.stock_movement_id, PurchaseReception.id, PurchaseOrder.order_number)
            .join(PurchaseReception, PurchaseReception.id == PurchaseReceptionLine.reception_id)
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseReception.purchase_order_id)
            .filter(PurchaseReceptionLine.stock_movement_id.in_(ids)).all())
    for row in rows:
        reference = " · ".join(value for value in (row.order_number, f"REC-{row.id:05d}") if value)
        url = f"/purchases/receptions?detail={row.id}" if "purchases:read" in access[0] else None
        yield row.stock_movement_id, ("purchase_reception", row.id, reference, url, "Recepción de compra")


def _receipt_corrections(ids, access):
    from app.models.purchase_correction import PurchaseReceptionCorrection as Correction, PurchaseReceptionCorrectionLine as Line
    rows = (db.session.query(Line.stock_movement_id, Correction.id, Correction.reception_id)
            .join(Correction, Correction.id == Line.correction_id)
            .filter(Line.stock_movement_id.in_(ids), Correction.status == "approved").all())
    for movement_id, correction_id, receipt_id in rows:
        url = f"/purchases/receptions?detail={receipt_id}" if "purchases:read" in access[0] else None
        yield movement_id, ("purchase_reception_correction", correction_id, f"COR-{correction_id} · REC-{receipt_id:05d}", url, "Corrección de recepción")


def _reconciliations(ids, access):
    rows = (db.session.query(ReconciliationLine.stock_movement_id, ReconciliationRun.id, ReconciliationRun.number)
            .join(ReconciliationRun, ReconciliationRun.id == ReconciliationLine.run_id)
            .filter(ReconciliationLine.stock_movement_id.in_(ids)).all())
    for row in rows:
        url = f"/reconciliation?detail={row.id}" if "reconciliation:read" in access[0] else None
        yield row.stock_movement_id, ("reconciliation", row.id, row.number, url, "Conciliación")


def _delivery_notes(ids, access):
    for field, movement_type, direction in (
        (DeliveryNoteItem.issue_stock_movement_id, "salida", -1),
        (DeliveryNoteItem.reversal_stock_movement_id, "entrada", 1),
    ):
        rows = (db.session.query(field, DeliveryNote, DeliveryNoteItem, StockMovement)
                .select_from(DeliveryNoteItem)
                .join(DeliveryNote, DeliveryNote.id == DeliveryNoteItem.delivery_note_id)
                .join(StockMovement, StockMovement.id == field).filter(field.in_(ids)).all())
        for movement_id, note, item, movement in rows:
            valid = (movement.product_id == item.product_id and movement.quantity == item.quantity
                     and movement.movement_type == movement_type
                     and movement.new_stock - movement.previous_stock == direction * item.quantity)
            if not valid:
                yield movement_id, ("inconsistent", None, None, None, "Origen inconsistente")
                continue
            url = f"/delivery-notes?detail={note.id}" if "delivery_notes:read" in access[0] else None
            yield movement_id, ("delivery_note", note.id, note.note_number, url, "Nota de entrega")


def _presentation(movement, origins, access):
    number = f"MOV-{movement.id:06d}"
    kind = getattr(movement, "creation_origin", None)
    source_id, reference, url, label = None, number, None, "Origen no identificado"
    if len(origins) > 1 or (origins and kind is not None):
        kind, label = "inconsistent", "Origen inconsistente"
    elif origins:
        kind, source_id, reference, url, label = origins[0]
    elif kind == "manual":
        source_id, label = movement.id, "Movimiento manual"
        if "inventory:read" in access[0]:
            url = f"/inventory?detail={movement.id}"
    elif kind == "initial_stock":
        source_id, label = movement.product_id, "Stock inicial"
    return {"movement_number": number, "source_type": kind, "source_id": source_id,
            "display_reference": reference or number, "source_url": url, "source_label": label,
            "display_status": "Aplicado",
            "presentation": {"reference": reference or number, "status_label": "Aplicado"}}


def movement_presentations(movements):
    """Bounded SELECTs per batch, independent of the number of documents."""
    ids = [movement.id for movement in movements]
    if not ids:
        return {}
    origins = defaultdict(list)
    with db.session.no_autoflush:
        access = _access()
        loaders = [(tables, loader) for tables, loader in (
            (("sale_lines", "sales"), _sales),
            (("purchase_reception_lines", "purchase_receptions", "purchase_orders"), _receptions),
            (("purchase_reception_corrections", "purchase_reception_correction_lines"), _receipt_corrections),
            (("reconciliation_lines", "reconciliation_runs"), _reconciliations),
            (("delivery_note_items", "delivery_notes"), _delivery_notes),
        ) if all(table_ready(table) for table in tables)]
        for start in range(0, len(ids), 500):
            for _, loader in loaders:
                for movement_id, source in loader(ids[start:start + 500], access):
                    origins[movement_id].append(source)
    return {movement.id: _presentation(movement, origins[movement.id], access) for movement in movements}
