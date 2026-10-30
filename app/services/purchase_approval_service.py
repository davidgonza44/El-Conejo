"""Bandeja paginada de decisiones de Compras, sin importes ni datos fuera de los permisos del rol."""
from datetime import datetime
from flask_login import current_user
from sqlalchemy import literal, select, union_all, func
from app.extensions import db
from app.models import User
from app.models.purchase import PurchaseOrder, Supplier
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
    return {"items": [_present(row) for row in rows], "pagination": page, "counts": counts}


def _present(row):
    kind, reference = row["kind"], row["reference"]
    labels = {"order": "Orden de compra", "supplier": "Alta de proveedor", "supplier_change": "Cambio de proveedor",
              "closure": "Cierre comercial", "correction": "Corrección de recepción"}
    targets = {"order": f"/purchases?detail={row['id']}", "supplier": f"/suppliers?detail={row['id']}",
               "supplier_change": f"/suppliers?detail={reference}", "closure": f"/purchases?detail={reference}",
               "correction": f"/purchases/receptions?detail={reference}"}
    return {"id": row["id"], "kind": kind, "label": labels[kind], "reference": str(reference),
            "responsible": row["responsible"] or "No registrado", "created_at": row["created_at"].isoformat()+"Z",
            "age_days": max(0, (datetime.utcnow()-row["created_at"]).days), "href": targets[kind]}
