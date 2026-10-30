"""Modificación propuesta de un proveedor aprobado; los datos oficiales permanecen en Supplier."""
from datetime import datetime

from app.extensions import db


class SupplierChange(db.Model):
    __tablename__ = "supplier_changes"
    __table_args__ = (
        db.CheckConstraint("status IN ('pending','approved','rejected')", name="ck_supplier_change_status"),
        db.Index("ix_supplier_changes_supplier_status", "supplier_id", "status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=False)
    proposed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    changes = db.Column(db.JSON, nullable=False)
    previous_values = db.Column(db.JSON, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    reviewed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime)
    rejection_reason = db.Column(db.String(1000))
    reason = db.Column(db.String(1000))
    proposed_by_user = db.relationship("User", foreign_keys=[proposed_by_user_id])

    def to_dict(self):
        return {"id": self.id, "supplier_id": self.supplier_id, "proposed_by_user_id": self.proposed_by_user_id,
                "proposed_by_name": self.proposed_by_user.name if self.proposed_by_user else None,
                "changes": self.changes, "previous_values": self.previous_values, "status": self.status,
                "created_at": self.created_at.isoformat(), "reason": self.reason,
                "reviewed_by_user_id": self.reviewed_by_user_id,
                "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
                "rejection_reason": self.rejection_reason}
