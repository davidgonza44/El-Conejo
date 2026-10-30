"""Solicitud de cierre comercial de una orden ya aprobada. No la cierra hasta que el administrador decide."""
from datetime import datetime

from app.extensions import db

CLOSURE_KINDS = ("price_change", "unavailable")


class PurchaseClosureRequest(db.Model):
    __tablename__ = "purchase_closure_requests"
    id = db.Column(db.Integer, primary_key=True)
    purchase_order_id = db.Column(db.Integer, db.ForeignKey("purchase_orders.id"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False)
    reason = db.Column(db.String(1000), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    requested_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    requested_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    decided_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    decided_at = db.Column(db.DateTime)
    decision_note = db.Column(db.String(1000))
    requester = db.relationship("User", foreign_keys=[requested_by_user_id])
    decider = db.relationship("User", foreign_keys=[decided_by_user_id])

    def to_dict(self):
        return {
            "id": self.id, "kind": self.kind, "reason": self.reason, "status": self.status,
            "requested_by_user_id": self.requested_by_user_id,
            "requested_by": self.requester.name if self.requester else None,
            "requested_at": self.requested_at.isoformat() if self.requested_at else None,
            "decided_by_user_id": self.decided_by_user_id,
            "decided_by": self.decider.name if self.decider else None,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "decision_note": self.decision_note,
        }
