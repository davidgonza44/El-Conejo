"""Cotización (Quotes Core v1): documento comercial que NUNCA toca el stock.

No hay columnas de stock ni enlaces a StockMovement. ``quote_number`` es NULL en el
flush (obtiene el id) y se fija a ``COT-{id:06d}`` antes del único commit.
"Vencida" no se persiste: se calcula (pending/sent con ``valid_until`` anterior al
día comercial) y nunca se escribe en un GET.
"""
from datetime import datetime

from app.extensions import db

NUMBER_PREFIX = "COT"
STATUS_PENDING, STATUS_SENT, STATUS_ACCEPTED, STATUS_REJECTED = "pending", "sent", "accepted", "rejected"
STATUS_EXPIRED = "expired"  # solo computado
QUOTE_STATUSES = (STATUS_PENDING, STATUS_SENT, STATUS_ACCEPTED, STATUS_REJECTED)
STATUS_LABELS = {STATUS_PENDING: "Pendiente", STATUS_SENT: "Enviada", STATUS_ACCEPTED: "Aceptada",
                 STATUS_REJECTED: "Rechazada", STATUS_EXPIRED: "Vencida"}
STATUS_TONES = {STATUS_PENDING: "orange", STATUS_SENT: "blue", STATUS_ACCEPTED: "green",
                STATUS_REJECTED: "red", STATUS_EXPIRED: "red"}
TRANSITIONS = {
    STATUS_PENDING: (STATUS_SENT, STATUS_ACCEPTED, STATUS_REJECTED),
    STATUS_SENT: (STATUS_ACCEPTED, STATUS_REJECTED),
    STATUS_ACCEPTED: (),
    STATUS_REJECTED: (),
}
OPEN_STATUSES = (STATUS_PENDING, STATUS_SENT)  # los únicos que pueden vencer
VALIDITY_DAYS = (7, 15, 30)


def _money(value) -> str:
    return f"{value:.2f}"


class Quote(db.Model):
    __tablename__ = "quotes"

    id = db.Column(db.Integer, primary_key=True)
    quote_number = db.Column(db.String(20), unique=True, nullable=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True)
    seller_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    quoted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    validity_days = db.Column(db.Integer, nullable=False)
    valid_until = db.Column(db.Date, nullable=False, index=True)
    subtotal = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    discount_percent = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    discount_total = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    total = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    status = db.Column(db.String(20), nullable=False, default=STATUS_PENDING, index=True)
    notes = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = db.relationship("Customer")
    seller = db.relationship("User")
    lines = db.relationship("QuoteLine", back_populates="quote", order_by="QuoteLine.id")

    def effective_status(self, today) -> str:
        if self.status in OPEN_STATUSES and self.valid_until < today:
            return STATUS_EXPIRED
        return self.status

    def to_summary_dict(self, today) -> dict:
        status = self.effective_status(today)
        return {
            "id": self.id,
            "quote_number": self.quote_number,
            "quoted_at": self.quoted_at.isoformat() if self.quoted_at else None,
            "customer": self.customer.to_summary_dict() if self.customer else None,
            "seller": {"id": self.seller_id, "name": self.seller.name if self.seller else None},
            "validity_days": self.validity_days,
            "validity_label": f"{self.validity_days} días",
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "status": self.status,
            "effective_status": status,
            "status_label": STATUS_LABELS.get(status, status),
            "status_tone": STATUS_TONES.get(status, "gray"),
            "allowed_transitions": list(TRANSITIONS.get(self.status, ())),
            "subtotal": _money(self.subtotal),
            "discount_percent": _money(self.discount_percent),
            "discount_total": _money(self.discount_total),
            "total": _money(self.total),
            "notes": self.notes,
            "line_count": len(self.lines),
        }

    def to_dict(self, today) -> dict:
        data = self.to_summary_dict(today)
        data["items"] = [line.to_dict() for line in self.lines]
        return data

    def __repr__(self) -> str:
        return f"<Quote {self.quote_number}>"


class QuoteLine(db.Model):
    """Renglón cotizado: copia código, nombre y precio; el producto puede cambiar después."""
    __tablename__ = "quote_lines"

    id = db.Column(db.Integer, primary_key=True)
    quote_id = db.Column(db.Integer, db.ForeignKey("quotes.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    product_code = db.Column(db.String(50), nullable=False)
    product_name = db.Column(db.String(150), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False)
    line_subtotal = db.Column(db.Numeric(12, 2), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    quote = db.relationship("Quote", back_populates="lines")
    product = db.relationship("Product")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "product_id": self.product_id,
            "product_code": self.product_code,
            "product_name": self.product_name,
            "image_url": self.product.image_url if self.product else None,
            "quantity": self.quantity,
            "unit_price": _money(self.unit_price),
            "line_subtotal": _money(self.line_subtotal),
        }

    def __repr__(self) -> str:
        return f"<QuoteLine quote={self.quote_id} product={self.product_id}>"
