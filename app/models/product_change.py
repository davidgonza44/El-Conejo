"""Propuesta de modificación: separada de los datos vigentes del catálogo."""
from datetime import datetime

from app.extensions import db


class ProductChange(db.Model):
    __tablename__ = "product_changes"
    __table_args__ = (
        db.CheckConstraint("status IN ('pending','approved','rejected')", name="ck_product_change_status"),
        db.Index("ix_product_changes_product_status", "product_id", "status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    proposed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    changes = db.Column(db.JSON, nullable=False)
    previous_values = db.Column(db.JSON, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    reviewed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime)
    rejection_reason = db.Column(db.String(1000))

    def to_dict(self):
        from app.utils.permissions import response_includes_cost, response_includes_sale_price

        hidden = set()
        if not response_includes_cost():
            hidden.add("purchase_price")
            hidden.add("purchase_price_currency")
        if not response_includes_sale_price():
            hidden.add("sale_price")
            hidden.add("sale_price_currency")
        return {
            "id": self.id, "product_id": self.product_id,
            "proposed_by_user_id": self.proposed_by_user_id,
            "changes": {key: value for key, value in self.changes.items() if key not in hidden},
            "previous_values": {key: value for key, value in self.previous_values.items() if key not in hidden},
            "status": self.status, "created_at": self.created_at.isoformat(),
            "reviewed_by_user_id": self.reviewed_by_user_id,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "rejection_reason": self.rejection_reason,
        }
