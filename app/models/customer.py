"""Directorio único de clientes (Ventas → Clientes y, más adelante, /customers).

El cliente "Público en general" es una fila real protegida (``is_walk_in``):
no admite renombrar, desactivar, borrar ni duplicar. Así ninguna venta
necesita un ``customer_id`` nulo ni cadenas mágicas.
"""
from datetime import datetime

from app.extensions import db

WALK_IN_NAME = "Público en general"


class Customer(db.Model):
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False, index=True)
    tax_id = db.Column(db.String(20), unique=True, nullable=True)
    phone = db.Column(db.String(30), nullable=True)
    email = db.Column(db.String(120), unique=True, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    is_walk_in = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    sales = db.relationship("Sale", back_populates="customer")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "tax_id": self.tax_id,
            "phone": self.phone,
            "email": self.email,
            "is_active": self.is_active,
            "is_walk_in": self.is_walk_in,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_summary_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "tax_id": self.tax_id,
                "phone": self.phone, "is_walk_in": self.is_walk_in}

    def __repr__(self) -> str:
        return f"<Customer {self.id} {self.name}>"
