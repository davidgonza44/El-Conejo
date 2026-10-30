"""Renglón de venta: copia código, nombre y precio para preservar el histórico.

``stock_movement_id`` enlaza uno a uno con la salida de inventario que creó
``inventory_service.register_exit``; StockMovement no cambia de esquema.
"""
from datetime import datetime

from app.extensions import db


def _money(value) -> str:
    return f"{value:.2f}"


class SaleLine(db.Model):
    __tablename__ = "sale_lines"

    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    stock_movement_id = db.Column(
        db.Integer, db.ForeignKey("stock_movements.id"), nullable=False, unique=True
    )
    product_code = db.Column(db.String(50), nullable=False)
    product_name = db.Column(db.String(150), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False)
    discount_percent = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    discount_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    line_subtotal = db.Column(db.Numeric(12, 2), nullable=False)
    line_total = db.Column(db.Numeric(12, 2), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    sale = db.relationship("Sale", back_populates="lines")
    product = db.relationship("Product")
    stock_movement = db.relationship("StockMovement")

    def to_dict(self) -> dict:
        product = self.product
        return {
            "id": self.id,
            "product_id": self.product_id,
            "stock_movement_id": self.stock_movement_id,
            "product_code": self.product_code,
            "product_name": self.product_name,
            "image_url": product.image_url if product else None,
            "quantity": self.quantity,
            "unit_price": _money(self.unit_price),
            "discount_percent": _money(self.discount_percent),
            "discount_amount": _money(self.discount_amount),
            "line_subtotal": _money(self.line_subtotal),
            "line_total": _money(self.line_total),
        }

    def __repr__(self) -> str:
        return f"<SaleLine sale={self.sale_id} product={self.product_id}>"
