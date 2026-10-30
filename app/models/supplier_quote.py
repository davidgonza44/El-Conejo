"""Cotización de proveedor. No es la cotización de cliente (COT-)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from app.extensions import db

QUOTE_STATUSES = ("vigente", "vencida", "reemplazada", "cancelada")


class SupplierQuote(db.Model):
    __tablename__ = "supplier_quotes"
    id = db.Column(db.Integer, primary_key=True)
    quote_number = db.Column(db.String(20), unique=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=False, index=True)
    issued_on = db.Column(db.Date, nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    valid_until = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="vigente", index=True)
    notes = db.Column(db.String(1000))
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    replaces_quote_id = db.Column(db.Integer, db.ForeignKey("supplier_quotes.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    supplier = db.relationship("Supplier")
    creator = db.relationship("User")
    lines = db.relationship(
        "SupplierQuoteLine", back_populates="quote",
        order_by="SupplierQuoteLine.id", cascade="all, delete-orphan",
    )

    def effective_status(self, today=None):
        if self.status in ("reemplazada", "cancelada", "vencida"):
            return self.status
        today = today or datetime.now(ZoneInfo("America/Caracas")).date()
        if self.valid_until < today:
            return "vencida"
        return "vigente"

    def to_dict(self):
        from app.utils.permissions import response_includes_cost

        payload = {
            "id": self.id, "quote_number": self.quote_number, "supplier_id": self.supplier_id,
            "supplier_name": self.supplier.name if self.supplier else None,
            "issued_on": self.issued_on.isoformat(), "currency": self.currency,
            "valid_until": self.valid_until.isoformat(), "status": self.status,
            "effective_status": self.effective_status(), "notes": self.notes or "",
            "replaces_quote_id": self.replaces_quote_id,
            "items": [line.to_dict() for line in self.lines],
        }
        if not response_includes_cost():
            for item in payload["items"]:
                item.pop("unit_price", None)
        return payload


class SupplierQuoteLine(db.Model):
    __tablename__ = "supplier_quote_lines"
    __table_args__ = (db.UniqueConstraint("quote_id", "product_id", name="uq_supplier_quote_product"),)
    id = db.Column(db.Integer, primary_key=True)
    quote_id = db.Column(db.Integer, db.ForeignKey("supplier_quotes.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    product_code = db.Column(db.String(50), nullable=False)
    product_name = db.Column(db.String(150), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False)
    quote = db.relationship("SupplierQuote", back_populates="lines")

    def to_dict(self):
        return {"id": self.id, "product_id": self.product_id, "product_code": self.product_code,
                "product_name": self.product_name, "quantity": self.quantity,
                "unit_price": f"{self.unit_price:.2f}"}
