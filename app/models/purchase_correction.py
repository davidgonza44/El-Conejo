"""Correcciones aprobadas de recepción; el comprobante original conserva sus renglones."""
from datetime import datetime
from app.extensions import db


class PurchaseReceptionCorrection(db.Model):
    __tablename__ = "purchase_reception_corrections"
    __table_args__ = (db.CheckConstraint("status IN ('pending','approved','rejected')", name="ck_receipt_correction_status"),)
    id = db.Column(db.Integer, primary_key=True)
    reception_id = db.Column(db.Integer, db.ForeignKey("purchase_receptions.id"), nullable=False, index=True)
    requested_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    reason = db.Column(db.String(1000), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    decided_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    decided_at = db.Column(db.DateTime)
    decision_note = db.Column(db.String(1000))
    reception = db.relationship("PurchaseReception")
    requester = db.relationship("User", foreign_keys=[requested_by_user_id])
    decider = db.relationship("User", foreign_keys=[decided_by_user_id])
    lines = db.relationship("PurchaseReceptionCorrectionLine", cascade="all, delete-orphan", order_by="PurchaseReceptionCorrectionLine.product_id")

    def to_dict(self):
        return {"id": self.id, "reception_id": self.reception_id, "status": self.status, "reason": self.reason,
                "requested_by_user_id": self.requested_by_user_id, "requested_by": self.requester.name,
                "created_at": self.created_at.isoformat(), "decided_by": self.decider.name if self.decider else None,
                "decided_at": self.decided_at.isoformat() if self.decided_at else None,
                "decision_note": self.decision_note, "items": [line.to_dict() for line in self.lines]}


class PurchaseReceptionCorrectionLine(db.Model):
    __tablename__ = "purchase_reception_correction_lines"
    __table_args__ = (
        db.UniqueConstraint("correction_id", "purchase_order_line_id", name="uq_receipt_correction_line"),
        db.CheckConstraint("previous_quantity >= 0 AND target_quantity >= 0 AND previous_quantity <> target_quantity", name="ck_receipt_correction_quantities"),
    )
    id = db.Column(db.Integer, primary_key=True)
    correction_id = db.Column(db.Integer, db.ForeignKey("purchase_reception_corrections.id"), nullable=False, index=True)
    purchase_order_line_id = db.Column(db.Integer, db.ForeignKey("purchase_order_lines.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    previous_quantity = db.Column(db.Integer, nullable=False)
    target_quantity = db.Column(db.Integer, nullable=False)
    expected_stock = db.Column(db.Integer, nullable=False)
    previous_movement_id = db.Column(db.Integer, db.ForeignKey("stock_movements.id"))
    stock_movement_id = db.Column(db.Integer, db.ForeignKey("stock_movements.id"), unique=True)
    order_line = db.relationship("PurchaseOrderLine")

    def to_dict(self):
        return {"line_id": self.purchase_order_line_id, "product_id": self.product_id,
                "product_name": self.order_line.product_name, "previous_quantity": self.previous_quantity,
                "target_quantity": self.target_quantity, "delta": self.target_quantity-self.previous_quantity,
                "movement_id": self.stock_movement_id}
