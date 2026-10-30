"""Solicitud de reposición de inventario. No es un pedido de cliente ni una orden de compra."""
from datetime import datetime, timezone

from app.extensions import db

STATUSES = ("open", "processing", "fulfilled", "cancelled")
STATUS_LABELS = {
    "open": "Abierta",
    "processing": "En proceso",
    "fulfilled": "Convertida en OC",
    "cancelled": "Anulada",
    "converted": "Convertida en orden",
}
ORIGIN_LABELS = {"manual": "Manual", "predictive": "Predictivo", "shortage": "Faltante de orden de compra"}
# Estados terminales de la OC generada que dejan el faltante otra vez sin tramitar.
DEAD_ORDER_STATUSES = ("rejected", "cancelled")


def shortage_still_covered(status, order_status):
    """¿Esta solicitud sigue cubriendo el faltante? Anulada, o con OC rechazada/anulada, ya no."""
    if status == "cancelled":
        return False
    return order_status not in DEAD_ORDER_STATUSES


def _local_date(value):
    if value is None:
        return None
    from app.services.inventory_service import COMMERCIAL_ZONE

    return value.replace(tzinfo=timezone.utc).astimezone(COMMERCIAL_ZONE).date().isoformat()


def _order_ref(order_id):
    if not order_id:
        return None
    from app.models.purchase import PurchaseOrder

    order = db.session.get(PurchaseOrder, order_id)
    if order is None:
        return {"id": order_id, "order_number": None, "status": None}
    from app.models.supplier_quote import SupplierQuote
    quote = db.session.get(SupplierQuote, order.supplier_quote_id) if order.supplier_quote_id else None
    return {"id": order.id, "order_number": order.order_number, "status": order.status,
            "prepared_by": order.creator.name if order.creator else None,
            "prepared_at": order.created_at.isoformat() if order.created_at else None,
            "quote_number": quote.quote_number if quote else None,
            "supplier_name": order.supplier.name if order.supplier else None}


def _source_ref(order_id):
    if not order_id:
        return None
    from app.models.purchase import STATUS_LABELS as ORDER_LABELS, PurchaseOrder

    order = db.session.get(PurchaseOrder, order_id)
    if order is None:
        return {"id": order_id, "order_number": None, "status_label": None}
    return {"id": order.id, "order_number": order.order_number,
            "status_label": ORDER_LABELS.get(order.status, order.status)}


class ReplenishmentRequest(db.Model):
    __tablename__ = "replenishment_requests"
    __table_args__ = (
        db.CheckConstraint("status IN ('open','processing','fulfilled','cancelled')", name="ck_sr_status"),
        db.UniqueConstraint("created_by_user_id", "idempotency_key", name="uq_sr_creator_retry"),
    )
    id = db.Column(db.Integer, primary_key=True)
    request_number = db.Column(db.String(20), unique=True)
    status = db.Column(db.String(20), nullable=False, default="open", index=True)
    origin = db.Column(db.String(20))
    notes = db.Column(db.String(1000))
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    processed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    processed_at = db.Column(db.DateTime)
    cancelled_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    cancelled_at = db.Column(db.DateTime)
    idempotency_key = db.Column(db.String(80))
    purchase_order_id = db.Column(db.Integer, db.ForeignKey("purchase_orders.id"), unique=True)
    # OC cerrada cuyo faltante origina esta solicitud. Solo se fija al crearla; nunca se infiere.
    source_purchase_order_id = db.Column(db.Integer, db.ForeignKey("purchase_orders.id"), index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    creator = db.relationship("User", foreign_keys=[created_by_user_id])
    processor = db.relationship("User", foreign_keys=[processed_by_user_id])
    canceller = db.relationship("User", foreign_keys=[cancelled_by_user_id])
    lines = db.relationship(
        "ReplenishmentRequestLine", back_populates="request",
        order_by="ReplenishmentRequestLine.id", cascade="all, delete-orphan",
    )

    def to_dict(self):
        return {
            "id": self.id, "request_number": self.request_number, "status": self.status,
            "status_label": STATUS_LABELS.get(self.status, self.status),
            "origin": self.origin, "origin_label": ORIGIN_LABELS.get(self.origin, "No registrado"),
            "notes": self.notes or "",
            "creator": {"id": self.created_by_user_id, "name": self.creator.name if self.creator else None},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_on": _local_date(self.created_at),
            "processed_by": (
                {"id": self.processed_by_user_id, "name": self.processor.name if self.processor else None}
                if self.processed_by_user_id else None
            ),
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
            "processed_on": _local_date(self.processed_at),
            "cancelled_by": ({"id": self.cancelled_by_user_id, "name": self.canceller.name if self.canceller else None}
                             if self.cancelled_by_user_id else None),
            "cancelled_at": self.cancelled_at.isoformat() if self.cancelled_at else None,
            "cancelled_on": _local_date(self.cancelled_at),
            "purchase_order_id": self.purchase_order_id,
            "purchase_order": _order_ref(self.purchase_order_id),
            "source_purchase_order": _source_ref(self.source_purchase_order_id),
            "items": [line.to_dict() for line in self.lines],
        }


class ReplenishmentRequestLine(db.Model):
    __tablename__ = "replenishment_request_lines"
    __table_args__ = (
        db.UniqueConstraint("request_id", "product_id", name="uq_sr_product"),
        db.CheckConstraint("quantity > 0", name="ck_sr_quantity"),
    )
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey("replenishment_requests.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    product_code = db.Column(db.String(50), nullable=False)
    product_name = db.Column(db.String(150), nullable=False)
    unit = db.Column(db.String(30), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    stock_on_hand = db.Column(db.Integer)
    request = db.relationship("ReplenishmentRequest", back_populates="lines")

    def to_dict(self):
        return {"id": self.id, "product_id": self.product_id, "product_code": self.product_code,
                "product_name": self.product_name, "unit": self.unit, "quantity": self.quantity,
                "stock_on_hand": self.stock_on_hand}
