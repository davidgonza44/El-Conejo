"""Solicitud manual; solo la aprobación enlaza un movimiento aplicado."""
from datetime import datetime

from app.extensions import db


class InventoryAdjustmentRequest(db.Model):
    __tablename__ = "inventory_adjustment_requests"
    __table_args__ = (
        db.CheckConstraint("status IN ('pending','approved','rejected')", name="ck_adjustment_status"),
        db.CheckConstraint("previous_stock >= 0 AND new_stock >= 0", name="ck_adjustment_stock"),
        db.CheckConstraint("reviewer_user_id IS NULL OR reviewer_user_id <> requester_user_id",
                           name="ck_adjustment_reviewer"),
        db.CheckConstraint(
            "(status = 'pending' AND reviewer_user_id IS NULL AND reviewed_at IS NULL AND stock_movement_id IS NULL) OR "
            "(status = 'approved' AND reviewer_user_id IS NOT NULL AND reviewed_at IS NOT NULL AND stock_movement_id IS NOT NULL) OR "
            "(status = 'rejected' AND reviewer_user_id IS NOT NULL AND reviewed_at IS NOT NULL AND stock_movement_id IS NULL)",
            name="ck_adjustment_resolution"),
    )

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    requester_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    requested_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    previous_stock = db.Column(db.Integer, nullable=False)
    previous_movement_id = db.Column(db.Integer, db.ForeignKey("stock_movements.id"))
    new_stock = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    external_reference = db.Column(db.String(120))
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    reviewer_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime)
    review_comment = db.Column(db.String(255))
    stock_movement_id = db.Column(db.Integer, db.ForeignKey("stock_movements.id"), unique=True)
    # Origen conciliación: al aprobar se aplica la diferencia de la línea sobre el stock vigente.
    reconciliation_line_id = db.Column(db.Integer, db.ForeignKey("reconciliation_lines.id"), index=True)

    product = db.relationship("Product")
    reconciliation_line = db.relationship(
        "ReconciliationLine", backref=db.backref(
            "adjustment_requests", lazy="selectin", order_by="InventoryAdjustmentRequest.id.desc()"))

    def to_dict(self):
        result = {column.name: getattr(self, column.name) for column in self.__table__.columns}
        for key in ("requested_at", "reviewed_at"):
            result[key] = result[key].isoformat() if result[key] else None
        result["product"] = self.product.name
        result["source"] = "reconciliation" if self.reconciliation_line_id else "manual"
        return result
