from datetime import datetime

from app.extensions import db


class DeliveryNoteItem(db.Model):
    """Renglón de una nota de entrega.

    Guarda una copia de product_code, product_name y unit_price para que la
    nota conserve su histórico aunque el producto cambie después.
    """

    __tablename__ = "delivery_note_items"

    id = db.Column(db.Integer, primary_key=True)
    delivery_note_id = db.Column(
        db.Integer, db.ForeignKey("delivery_notes.id"), nullable=False, index=True
    )
    # db.Integer coincide exactamente con products.id (INT en MySQL).
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=False, index=True
    )
    product_code = db.Column(db.String(50), nullable=False)
    product_name = db.Column(db.String(150), nullable=False)
    # DECIMAL(12,2) para admitir cantidades decimales en el futuro; hoy el
    # stock del inventario es entero, así que el service valida enteros.
    quantity = db.Column(db.Numeric(12, 2), nullable=False)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False)
    line_total = db.Column(db.Numeric(12, 2), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    delivery_note = db.relationship("DeliveryNote", back_populates="items")
    product = db.relationship("Product")
    issue_stock_movement_id = db.Column(
        db.Integer, db.ForeignKey("stock_movements.id"), nullable=True, unique=True
    )
    reversal_stock_movement_id = db.Column(
        db.Integer, db.ForeignKey("stock_movements.id"), nullable=True, unique=True
    )
    issue_stock_movement = db.relationship("StockMovement", foreign_keys=[issue_stock_movement_id])
    reversal_stock_movement = db.relationship("StockMovement", foreign_keys=[reversal_stock_movement_id])

    def to_dict(self) -> dict:
        from app.utils.permissions import response_includes_sale_price

        payload = {
            "id": self.id,
            "delivery_note_id": self.delivery_note_id,
            "product_id": self.product_id,
            "product_code": self.product_code,
            "product_name": self.product_name,
            "quantity": float(self.quantity),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if response_includes_sale_price():
            payload["unit_price"] = float(self.unit_price)
            payload["line_total"] = float(self.line_total)
        return payload

    def __repr__(self) -> str:
        return f"<DeliveryNoteItem note={self.delivery_note_id} product={self.product_id}>"
