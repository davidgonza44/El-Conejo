from datetime import datetime

from app.extensions import db


class Product(db.Model):
    __tablename__ = "products"
    __table_args__ = tuple(
        db.CheckConstraint(
            field + " IS NULL OR HEX(" + field + ") IN ('555344','564553')",
            name="ck_products_" + field)
        for field in ("sale_price_currency", "purchase_price_currency")
    ) + (
        db.CheckConstraint("current_stock >= 0 AND minimum_stock >= 0", name="ck_products_stock"),
        db.CheckConstraint("proposed_initial_stock IS NULL OR proposed_initial_stock > 0",
                           name="ck_products_proposed_initial_stock"),
    )

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text)
    brand = db.Column(db.String(100))
    category_id = db.Column(
        db.Integer, db.ForeignKey("categories.id"), nullable=False
    )
    unit = db.Column(db.String(30), nullable=False, default="unidad")
    current_stock = db.Column(db.Integer, nullable=False, default=0)
    minimum_stock = db.Column(db.Integer, nullable=False, default=0)
    # Stock inicial de un alta pendiente: entra al inventario solo cuando se aprueba el producto.
    proposed_initial_stock = db.Column(db.Integer, nullable=True)
    purchase_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    sale_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    sale_price_currency = db.Column(db.String(8), nullable=True)
    purchase_price_currency = db.Column(db.String(8), nullable=True)
    image_url = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    approval_status = db.Column(db.String(20), nullable=False, default="approved", server_default="approved")
    proposed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    rejection_reason = db.Column(db.String(1000))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    category = db.relationship("Category", back_populates="products")
    stock_movements = db.relationship("StockMovement", back_populates="product")
    state_history = db.relationship("ProductStateHistory", back_populates="product")

    @property
    def is_low_stock(self) -> bool:
        """Regla 8: bajo stock cuando current_stock <= minimum_stock."""
        return self.current_stock <= self.minimum_stock

    @property
    def stock_status(self) -> str:
        """Grupos mutuamente excluyentes para la UI de gestión."""
        if not self.is_active:
            return "inactivo"
        if self.current_stock == 0:
            return "agotado"
        if self.current_stock <= self.minimum_stock:
            return "bajo_stock"
        return "disponible"

    def to_dict(self) -> dict:
        from app.utils.permissions import response_includes_cost, response_includes_sale_price

        payload = {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "description": self.description,
            "brand": self.brand,
            "category_id": self.category_id,
            "category": self.category.name if self.category else None,
            "unit": self.unit,
            "current_stock": self.current_stock,
            "minimum_stock": self.minimum_stock,
            "proposed_initial_stock": self.proposed_initial_stock,
            "image_url": self.image_url,
            "is_active": self.is_active,
            "approval_status": self.approval_status,
            "is_low_stock": self.is_low_stock,
            "stock_status": self.stock_status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if response_includes_sale_price():
            payload["sale_price"] = float(self.sale_price)
            payload["sale_price_currency"] = self.sale_price_currency
        if response_includes_cost():
            payload["purchase_price"] = float(self.purchase_price)
            payload["purchase_price_currency"] = self.purchase_price_currency
        return payload

    def __repr__(self) -> str:
        return f"<Product {self.code} {self.name}>"
