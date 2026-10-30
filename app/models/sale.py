"""Venta registrada (Sales Core v1).

Una venta se confirma en una sola transacción junto con sus renglones y las
salidas de stock; por eso en v1 el único estado escrito es ``completed``.
``sale_number`` es NULL durante el flush (obtiene el id) y se fija a
``VTA-{id:06d}`` antes del único commit: no hay COUNT+1 ni literal repetido.
"""
from datetime import datetime

from app.extensions import db

STATUS_COMPLETED = "completed"
SALE_STATUSES = (STATUS_COMPLETED,)
STATUS_LABELS = {STATUS_COMPLETED: "Completada"}

PAYMENT_METHODS = {
    "transfer": "Transferencia",
    "cash": "Efectivo",
    "credit_card": "Tarjeta de crédito",
    "debit_card": "Tarjeta de débito",
    "check": "Cheque",
    "receivable": "Por cobrar",
}
# Metadatos comerciales: v1 no implementa cuentas por cobrar ni vencimientos.
PAYMENT_CONDITIONS = {
    "cash": "Contado",
    "net_15": "15 días",
    "net_30": "30 días",
    "net_60": "60 días",
    "net_90": "90 días",
    "custom": "Personalizada",
}
NUMBER_PREFIX = "VTA"


def _money(value) -> str:
    return f"{value:.2f}"


class Sale(db.Model):
    __tablename__ = "sales"

    id = db.Column(db.Integer, primary_key=True)
    sale_number = db.Column(db.String(20), unique=True, nullable=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True)
    seller_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    sold_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    payment_method = db.Column(db.String(20), nullable=False)
    payment_condition = db.Column(db.String(20), nullable=False)
    subtotal = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    discount_total = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    total = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    # NULL means non-cash or an unknown legacy cash amount; never backfill guesses.
    cash_received = db.Column(db.Numeric(12, 2), nullable=True)
    change_due = db.Column(db.Numeric(12, 2), nullable=True)
    status = db.Column(db.String(20), nullable=False, default=STATUS_COMPLETED, index=True)
    idempotency_key = db.Column(db.String(64), unique=True, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = db.relationship("Customer", back_populates="sales")
    seller = db.relationship("User")
    lines = db.relationship("SaleLine", back_populates="sale", order_by="SaleLine.id")

    def to_summary_dict(self) -> dict:
        return {
            "id": self.id,
            "sale_number": self.sale_number,
            "sold_at": self.sold_at.isoformat() if self.sold_at else None,
            "customer": self.customer.to_summary_dict() if self.customer else None,
            "seller": {"id": self.seller_id, "name": self.seller.name if self.seller else None},
            "payment_method": self.payment_method,
            "payment_method_label": PAYMENT_METHODS.get(self.payment_method, self.payment_method),
            "payment_condition": self.payment_condition,
            "payment_condition_label": PAYMENT_CONDITIONS.get(self.payment_condition, self.payment_condition),
            "status": self.status,
            "status_label": STATUS_LABELS.get(self.status, self.status),
            "subtotal": _money(self.subtotal),
            "discount_total": _money(self.discount_total),
            "total": _money(self.total),
            "cash_received": _money(self.cash_received) if self.cash_received is not None else None,
            "change_due": _money(self.change_due) if self.change_due is not None else None,
            "line_count": len(self.lines),
            # "Productos" en listados y dashboard: unidades vendidas (SUM(quantity)).
            "units": sum(line.quantity for line in self.lines),
        }

    def to_dict(self) -> dict:
        data = self.to_summary_dict()
        data["items"] = [line.to_dict() for line in self.lines]
        return data

    def __repr__(self) -> str:
        return f"<Sale {self.sale_number}>"
