"""Bandeja paginada de decisiones de Compras, sin importes ni datos fuera de los permisos del rol."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask_login import current_user
from sqlalchemy import literal, select, union_all, func
from app.extensions import db
from app.models import User
from app.models.purchase import PurchaseOrder, PurchaseReception, Supplier
from app.models.supplier_change import SupplierChange
from app.models.purchase_closure import PurchaseClosureRequest
from app.models.purchase_correction import PurchaseReceptionCorrection
from app.services.purchase_query_service import page_values
from app.services.exceptions import ValidationError
from app.utils.permissions import role_has_permission
from app.utils.schema import table_ready


def listing(params):
    role = current_user.role
    specs = [
        ("order", PurchaseOrder, "purchases:approve", PurchaseOrder.created_by_user_id, PurchaseOrder.created_at,
         PurchaseOrder.order_number, PurchaseOrder.status == "pending_approval"),
        ("supplier", Supplier, "suppliers:approve", Supplier.proposed_by_user_id, Supplier.created_at,
         Supplier.name, Supplier.approval_status == "pending"),
        ("supplier_change", SupplierChange, "suppliers:approve", SupplierChange.proposed_by_user_id, SupplierChange.created_at,
         SupplierChange.supplier_id, SupplierChange.status == "pending"),
        ("closure", PurchaseClosureRequest, "purchases:close_approve", PurchaseClosureRequest.requested_by_user_id,
         PurchaseClosureRequest.requested_at, PurchaseClosureRequest.purchase_order_id, PurchaseClosureRequest.status == "pending"),
        ("correction", PurchaseReceptionCorrection, "purchases:approve", PurchaseReceptionCorrection.requested_by_user_id,
         PurchaseReceptionCorrection.created_at, PurchaseReceptionCorrection.reception_id, PurchaseReceptionCorrection.status == "pending"),
    ]
    queries = [select(literal(kind).label("kind"), model.id.label("id"), actor.label("actor_id"),
                      created.label("created_at"), reference.label("reference")).where(condition)
               for kind, model, permission, actor, created, reference, condition in specs
               if role_has_permission(role, permission) and table_ready(model.__tablename__)]
    if not queries:
        raise ValidationError("Su rol no puede consultar decisiones de compras.", status_code=403)
    pending = union_all(*queries).subquery()
    total = db.session.scalar(select(func.count()).select_from(pending))
    page = page_values(params, total)
    rows = db.session.execute(select(pending, User.name.label("responsible")).outerjoin(User, User.id == pending.c.actor_id)
        .order_by(pending.c.created_at, pending.c.kind, pending.c.id)
        .offset((page["page"]-1)*page["per_page"]).limit(page["per_page"])).mappings().all()
    counts = dict(db.session.execute(select(pending.c.kind, func.count()).group_by(pending.c.kind)).all())
    names = _reference_names(rows)
    return {"items": [_present(row, names) for row in rows], "pagination": page, "counts": counts,
            "kinds": [kind for kind, model, permission, *_ in specs
                      if role_has_permission(role, permission) and table_ready(model.__tablename__)]}


LABELS = {"order": "Orden de compra", "supplier": "Alta de proveedor", "supplier_change": "Cambio de proveedor",
          "closure": "Cierre comercial", "correction": "Corrección de recepción"}
CARACAS = ZoneInfo("America/Caracas")


def _reference_names(rows):
    """Referencias legibles en una consulta por tipo: proveedor, número de OC o de recepción, no ids internos."""
    ids = {kind: {int(row["reference"]) for row in rows if row["kind"] == kind}
           for kind in ("supplier_change", "closure", "correction")}
    names = {}
    if ids["supplier_change"]:
        names["supplier_change"] = dict(db.session.execute(
            select(Supplier.id, Supplier.name).where(Supplier.id.in_(ids["supplier_change"]))).all())
    if ids["closure"]:
        names["closure"] = dict(db.session.execute(
            select(PurchaseOrder.id, PurchaseOrder.order_number).where(PurchaseOrder.id.in_(ids["closure"]))).all())
    if ids["correction"]:
        names["correction"] = {row.id: f"REC-{row.id:05d} · {row.order_number}" for row in db.session.execute(
            select(PurchaseReception.id, PurchaseOrder.order_number).join(
                PurchaseOrder, PurchaseOrder.id == PurchaseReception.purchase_order_id)
            .where(PurchaseReception.id.in_(ids["correction"])))}
    return names


def _present(row, names):
    kind, reference = row["kind"], row["reference"]
    targets = {"order": f"/purchases?detail={row['id']}", "supplier": f"/suppliers?detail={row['id']}",
               "supplier_change": f"/suppliers?detail={reference}", "closure": f"/purchases?detail={reference}",
               "correction": f"/purchases/receptions?detail={reference}"}
    readable = names.get(kind, {}).get(int(reference)) if kind in names else None
    created = row["created_at"]
    age = max(0, (datetime.utcnow() - created).days)
    own = row["actor_id"] is not None and row["actor_id"] == current_user.id
    return {"id": row["id"], "kind": kind, "label": LABELS[kind], "reference": str(readable or reference),
            "responsible": row["responsible"] or "No registrado", "created_at": created.isoformat() + "Z",
            "created_label": created.replace(tzinfo=timezone.utc).astimezone(CARACAS).strftime("%d/%m/%Y %H:%M"),
            "age_days": age, "age_label": "Hoy" if age == 0 else ("1 día" if age == 1 else f"{age} días"),
            "status_label": "Pendiente de decisión", "can_decide": not own, "href": targets[kind]}
