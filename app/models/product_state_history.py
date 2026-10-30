from datetime import datetime

from app.extensions import db

EVENT_BASELINE = "baseline"
EVENT_CHANGE = "change"

EVENT_TYPES = (EVENT_BASELINE, EVENT_CHANGE)


class ProductStateHistory(db.Model):
    """Historial tipado de minimum_stock e is_active por producto.

    Una fila por transición relevante (o baseline inicial). Guarda el estado
    anterior y posterior completos de ambos atributos en una sola fila, sin
    old_value/new_value como texto. ``changed_at`` es UTC naive, igual que
    Product.created_at/updated_at. Orden estable por (changed_at, id).
    """

    __tablename__ = "product_state_histories"
    __table_args__ = (
        db.Index("ix_product_state_histories_product_changed", "product_id", "changed_at", "id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    previous_minimum_stock = db.Column(db.Integer, nullable=False)
    new_minimum_stock = db.Column(db.Integer, nullable=False)
    previous_is_active = db.Column(db.Boolean, nullable=False)
    new_is_active = db.Column(db.Boolean, nullable=False)
    event_type = db.Column(db.String(20), nullable=False, default=EVENT_CHANGE)
    changed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    # Nullable: operaciones de sistema/scripts sin sesión no tienen responsable.
    changed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    product = db.relationship("Product", back_populates="state_history")
    changed_by = db.relationship("User", back_populates="product_state_changes")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "product_id": self.product_id,
            "previous_minimum_stock": self.previous_minimum_stock,
            "new_minimum_stock": self.new_minimum_stock,
            "previous_is_active": self.previous_is_active,
            "new_is_active": self.new_is_active,
            "event_type": self.event_type,
            "changed_at": self.changed_at.isoformat() if self.changed_at else None,
            "changed_by_user_id": self.changed_by_user_id,
        }

    def __repr__(self) -> str:
        return f"<ProductStateHistory {self.event_type} product={self.product_id}>"
