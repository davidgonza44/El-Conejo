"""Cierre comercial diario por producto (America/Caracas).

Una fila por producto y día. ``snapshot_date`` es el día comercial cuyo cierre se
capturó con el job programado después de medianoche; no reconstruye el pasado.
``category_id`` no lleva FK: la fila debe sobrevivir a la eliminación posterior
de una categoría vacía.
"""
from datetime import datetime

from app.extensions import db


class InventorySnapshot(db.Model):
    __tablename__ = "inventory_snapshots"
    __table_args__ = (db.UniqueConstraint("snapshot_date", "product_id", name="uq_inventory_snapshots_date_product"),)

    id = db.Column(db.Integer, primary_key=True)
    snapshot_date = db.Column(db.Date, nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    stock = db.Column(db.Integer, nullable=False)
    minimum_stock = db.Column(db.Integer, nullable=False)
    product_is_active = db.Column(db.Boolean, nullable=False)
    category_id = db.Column(db.Integer, nullable=False)
    category_is_active = db.Column(db.Boolean, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<InventorySnapshot {self.snapshot_date} product={self.product_id}>"
