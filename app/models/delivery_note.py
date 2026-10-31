from datetime import datetime

from app.extensions import db

STATUS_ISSUED = "issued"
STATUS_CANCELLED = "cancelled"

DELIVERY_NOTE_STATUSES = (STATUS_ISSUED, STATUS_CANCELLED)


class DeliveryNote(db.Model):
    """Nota de entrega simple (no es una factura fiscal)."""

    __tablename__ = "delivery_notes"

    id = db.Column(db.Integer, primary_key=True)
    note_number = db.Column(db.String(30), unique=True, nullable=False, index=True)
    # Cliente del directorio (opcional). La nota conserva su propia copia de nombre y datos.
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=True, index=True)
    customer_name = db.Column(db.String(150), nullable=False)
    customer_document = db.Column(db.String(30))
    customer_phone = db.Column(db.String(30))
    customer_address = db.Column(db.String(255))
    status = db.Column(db.String(20), nullable=False, default=STATUS_ISSUED, index=True)
    total_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    # db.Integer coincide exactamente con users.id (INT en MySQL).
    created_by_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    cancelled_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    cancellation_reason = db.Column(db.String(500), nullable=True)
    # Clave por envío del formulario: un reintento devuelve la misma nota en vez de emitir otra.
    idempotency_key = db.Column(db.String(64), unique=True, nullable=True)

    customer = db.relationship("Customer")
    creator = db.relationship(
        "User", back_populates="delivery_notes", foreign_keys=[created_by_user_id]
    )
    cancelled_by = db.relationship("User", foreign_keys=[cancelled_by_user_id])
    items = db.relationship(
        "DeliveryNoteItem",
        back_populates="delivery_note",
        cascade="all, delete-orphan",
    )

    def to_summary_dict(self) -> dict:
        return {
            "id": self.id,
            "note_number": self.note_number,
            "customer_name": self.customer_name,
            "status": self.status,
            "total_amount": self._visible_total(),
            "created_by": self.creator.name if self.creator else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def _visible_total(self):
        from app.utils.permissions import response_includes_sale_price
        return float(self.total_amount) if response_includes_sale_price() else None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "note_number": self.note_number,
            "customer_id": self.customer_id,
            "customer_name": self.customer_name,
            "customer_document": self.customer_document,
            "customer_phone": self.customer_phone,
            "customer_address": self.customer_address,
            "status": self.status,
            "total_amount": self._visible_total(),
            "created_by": self.creator.name if self.creator else None,
            "created_by_user_id": self.created_by_user_id,
            "cancelled_by": self.cancelled_by.name if self.cancelled_by else None,
            "cancelled_by_user_id": self.cancelled_by_user_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "cancelled_at": self.cancelled_at.isoformat() if self.cancelled_at else None,
            "cancellation_reason": self.cancellation_reason,
            "items": [item.to_dict() for item in self.items],
        }

    def __repr__(self) -> str:
        return f"<DeliveryNote {self.note_number}>"
