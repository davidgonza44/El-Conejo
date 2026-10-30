"""Pedido (Orders Core v1): compromiso comercial que NO descuenta ni reserva stock.

Completar un pedido no crea una venta ni un StockMovement (limitación explícita de v1).
``order_number`` es NULL en el flush (obtiene el id) y se fija a ``PED-{id:06d}``
antes del único commit.
"""
from datetime import datetime

from app.extensions import db

NUMBER_PREFIX = "PED"
STATUS_PENDING, STATUS_PROCESSING, STATUS_COMPLETED, STATUS_CANCELLED = "pending", "processing", "completed", "cancelled"
ORDER_STATUSES = (STATUS_PENDING, STATUS_PROCESSING, STATUS_COMPLETED, STATUS_CANCELLED)
CREATE_STATUSES = (STATUS_PENDING, STATUS_PROCESSING)
STATUS_LABELS = {STATUS_PENDING: "Pendiente", STATUS_PROCESSING: "En proceso",
                 STATUS_COMPLETED: "Completado", STATUS_CANCELLED: "Cancelado"}
STATUS_TONES = {STATUS_PENDING: "orange", STATUS_PROCESSING: "orange",
                STATUS_COMPLETED: "green", STATUS_CANCELLED: "red"}
TRANSITIONS = {
    STATUS_PENDING: (STATUS_PROCESSING, STATUS_CANCELLED),
    STATUS_PROCESSING: (STATUS_COMPLETED, STATUS_CANCELLED),
    STATUS_COMPLETED: (),
    STATUS_CANCELLED: (),
}
# Metadatos del pedido (Core v1). Prioridad y estado de pago son valores canónicos persistidos;
# el estado de pago es independiente del estado operativo (completar NO marca pagado).
PRIORITY_LOW, PRIORITY_MEDIUM, PRIORITY_HIGH, PRIORITY_URGENT = "low", "medium", "high", "urgent"
PRIORITIES = (PRIORITY_LOW, PRIORITY_MEDIUM, PRIORITY_HIGH, PRIORITY_URGENT)
PRIORITY_LABELS = {PRIORITY_LOW: "Baja", PRIORITY_MEDIUM: "Media", PRIORITY_HIGH: "Alta", PRIORITY_URGENT: "Urgente"}
PRIORITY_TONES = {PRIORITY_LOW: "blue", PRIORITY_MEDIUM: "yellow", PRIORITY_HIGH: "orange", PRIORITY_URGENT: "red"}
PAYMENT_PENDING, PAYMENT_PAID = "pending", "paid"
PAYMENT_STATUSES = (PAYMENT_PENDING, PAYMENT_PAID)  # v1: sin pagos parciales (no existe libro de cobros)
PAYMENT_LABELS = {PAYMENT_PENDING: "Pendiente", PAYMENT_PAID: "Pagado"}
PAYMENT_TONES = {PAYMENT_PENDING: "orange", PAYMENT_PAID: "green"}
CONTACT_PHONE_MAX, DELIVERY_ADDRESS_MAX, NOTES_MAX = 30, 255, 500


def _money(value) -> str:
    return f"{value:.2f}"


class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(20), unique=True, nullable=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True)
    seller_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    ordered_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    estimated_delivery_date = db.Column(db.Date, nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default=STATUS_PENDING, index=True)
    priority = db.Column(db.String(20), nullable=False, default=PRIORITY_MEDIUM)
    payment_status = db.Column(db.String(20), nullable=False, default=PAYMENT_PENDING)
    contact_phone = db.Column(db.String(CONTACT_PHONE_MAX), nullable=True)
    delivery_address = db.Column(db.String(DELIVERY_ADDRESS_MAX), nullable=True)
    notes = db.Column(db.String(NOTES_MAX), nullable=True)
    subtotal = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    total = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = db.relationship("Customer")
    seller = db.relationship("User")
    lines = db.relationship("OrderLine", back_populates="order", order_by="OrderLine.id")

    def to_summary_dict(self) -> dict:
        return {
            "id": self.id,
            "order_number": self.order_number,
            "ordered_at": self.ordered_at.isoformat() if self.ordered_at else None,
            "customer": self.customer.to_summary_dict() if self.customer else None,
            "seller": {"id": self.seller_id, "name": self.seller.name if self.seller else None},
            "estimated_delivery_date": self.estimated_delivery_date.isoformat() if self.estimated_delivery_date else None,
            "status": self.status,
            "status_label": STATUS_LABELS.get(self.status, self.status),
            "status_tone": STATUS_TONES.get(self.status, "gray"),
            "allowed_transitions": list(TRANSITIONS.get(self.status, ())),
            "editable": self.status in (STATUS_PENDING, STATUS_PROCESSING),
            "structural_editable": self.status == STATUS_PENDING,
            "priority": self.priority,
            "priority_label": PRIORITY_LABELS.get(self.priority, self.priority),
            "priority_tone": PRIORITY_TONES.get(self.priority, "gray"),
            "payment_status": self.payment_status,
            "payment_status_label": PAYMENT_LABELS.get(self.payment_status, self.payment_status),
            "payment_status_tone": PAYMENT_TONES.get(self.payment_status, "gray"),
            "contact_phone": self.contact_phone,
            "delivery_address": self.delivery_address,
            "notes": self.notes,
            "subtotal": _money(self.subtotal),
            "total": _money(self.total),
            # "Productos" de la lista = unidades (SUM(quantity)), no número de renglones.
            "units": sum(line.quantity for line in self.lines),
            "line_count": len(self.lines),
        }

    def to_dict(self) -> dict:
        data = self.to_summary_dict()
        data["items"] = [line.to_dict() for line in self.lines]
        return data

    def __repr__(self) -> str:
        return f"<Order {self.order_number}>"


class OrderLine(db.Model):
    __tablename__ = "order_lines"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    product_code = db.Column(db.String(50), nullable=False)
    product_name = db.Column(db.String(150), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False)
    line_subtotal = db.Column(db.Numeric(12, 2), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    order = db.relationship("Order", back_populates="lines")
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
        return f"<OrderLine order={self.order_id} product={self.product_id}>"
