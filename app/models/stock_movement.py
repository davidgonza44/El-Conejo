from datetime import datetime

from app.extensions import db

MOVEMENT_ENTRADA = "entrada"
MOVEMENT_SALIDA = "salida"
MOVEMENT_AJUSTE = "ajuste"

MOVEMENT_TYPES = (MOVEMENT_ENTRADA, MOVEMENT_SALIDA, MOVEMENT_AJUSTE)


class StockMovement(db.Model):
    __tablename__ = "stock_movements"
    __table_args__ = (
        db.CheckConstraint("creation_origin IS NULL OR creation_origin IN ('manual','initial_stock')",
                           name="ck_movement_creation_origin"),
        db.CheckConstraint("external_reference IS NULL OR (creation_origin IS NOT NULL AND creation_origin = 'manual')",
                           name="ck_movement_external_manual"),
        # Las reglas del stock también viven en la base: un script o una importación no las salta.
        db.CheckConstraint("movement_type IN ('entrada','salida','ajuste')", name="ck_movement_type"),
        db.CheckConstraint("quantity > 0 AND previous_stock >= 0 AND new_stock >= 0", name="ck_movement_quantities"),
        db.CheckConstraint(
            "(movement_type = 'entrada' AND new_stock = previous_stock + quantity) OR "
            "(movement_type = 'salida' AND new_stock = previous_stock - quantity) OR "
            "(movement_type = 'ajuste' AND (new_stock = previous_stock + quantity OR new_stock = previous_stock - quantity))",
            name="ck_movement_arithmetic"),
        db.CheckConstraint("idempotency_key IS NULL OR creation_origin = 'manual'", name="ck_movement_idempotency_manual"),
    )

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=False, index=True
    )
    movement_type = db.Column(db.String(20), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False)
    previous_stock = db.Column(db.Integer, nullable=False)
    new_stock = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(255))
    creation_origin = db.Column(db.String(20), nullable=True)
    external_reference = db.Column(db.String(120), nullable=True)
    # Clave del cliente para entradas y salidas manuales: un reintento no duplica el movimiento.
    idempotency_key = db.Column(db.String(64), nullable=True, unique=True)
    # Nullable mientras no exista autenticación; se guarda si está disponible.
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, index=True
    )

    product = db.relationship("Product", back_populates="stock_movements")
    user = db.relationship("User", back_populates="stock_movements")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "movement_number": f"MOV-{self.id:06d}",
            "product_id": self.product_id,
            "product": self.product.name if self.product else None,
            "movement_type": self.movement_type,
            "quantity": self.quantity,
            "previous_stock": self.previous_stock,
            "new_stock": self.new_stock,
            "reason": self.reason,
            "creation_origin": self.creation_origin,
            "external_reference": self.external_reference,
            "user_id": self.user_id,
            "user": self.user.name if self.user else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<StockMovement {self.movement_type} product={self.product_id}>"
