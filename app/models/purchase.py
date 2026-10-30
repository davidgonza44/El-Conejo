"""Purchase intent and receipts are separate from customer sales orders."""
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Computed

from app.extensions import db

STATUS_LABELS = {
    "pending_approval": "Borrador pendiente de aprobación",
    "approved": "Aprobada / pendiente de recepción",
    "rejected": "Rechazada",
    "pending": "Pendiente (flujo anterior)",
    "partial": "Parcialmente recibida (flujo anterior)",
    "completed": "Completada (flujo anterior)",
    "cancelled": "Anulada por error",
    "closed_complete": "Cerrada completa",
    "closed_partial": "Cerrada parcial",
    "closed_unavailable": "Cerrada por falta de disponibilidad",
    "closed_price_change": "Cerrada por cambio de precio",
}
PO_STATUSES = tuple(STATUS_LABELS)
# Estados cuyo saldo pedido-recibido sigue siendo mercancía por recibir. Los del flujo anterior
# conservan su lectura histórica: nunca registraron quantity_not_supplied.
EXPECTING_STATUSES = ("pending_approval", "approved", "pending", "partial")


class Supplier(db.Model):
    __tablename__ = "suppliers"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False, index=True)
    tax_id = db.Column(db.String(30), unique=True, nullable=False)
    contact_name = db.Column(db.String(150))
    phone = db.Column(db.String(30))
    email = db.Column(db.String(150))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    approval_status = db.Column(db.String(20), nullable=False, default="approved", server_default="approved")
    proposed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    rejection_reason = db.Column(db.String(1000))
    contact_title = db.Column(db.String(150))
    address = db.Column(db.String(300))
    city = db.Column(db.String(100))
    state_province = db.Column(db.String(100))
    category = db.Column(db.String(100))
    is_preferred = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    notes = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        payload = {key: getattr(self, key) for key in ("id", "name", "tax_id", "contact_name", "phone", "email", "is_active")}
        payload["approval_status"] = self.approval_status
        return payload


class ProductSupplier(db.Model):
    """Catálogo producto–proveedor. No copia el costo de referencia ni el de la orden."""

    __tablename__ = "product_suppliers"
    __table_args__ = (
        db.UniqueConstraint("product_id", "supplier_id", name="uq_product_supplier"),
        db.UniqueConstraint("preferred_product_id", name="uq_product_supplier_preferred"),
        db.CheckConstraint("purchase_price >= 0", name="ck_product_supplier_price"),
        db.CheckConstraint("purchase_price_currency IS NULL OR HEX(purchase_price_currency) IN ('555344','564553')",
                           name="ck_product_suppliers_purchase_price_currency"),
        db.CheckConstraint("lead_time_days IS NULL OR lead_time_days >= 0", name="ck_product_supplier_lead"),
        db.CheckConstraint(
            "minimum_order_quantity IS NULL OR minimum_order_quantity > 0",
            name="ck_product_supplier_moq",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    supplier_id = db.Column(
        db.Integer, db.ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    supplier_product_code = db.Column(db.String(50))
    purchase_price = db.Column(db.Numeric(12, 2), nullable=False)
    purchase_price_currency = db.Column(db.String(8), nullable=True)
    lead_time_days = db.Column(db.Integer)
    minimum_order_quantity = db.Column(db.Integer)
    is_preferred = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default="1")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    # MariaDB 10.4 no tiene índice parcial. Varios NULL caben en el único; un solo preferido, no.
    preferred_product_id = db.Column(
        db.Integer,
        Computed("CASE WHEN is_preferred = 1 THEN product_id ELSE NULL END", persisted=False),
    )
    product = db.relationship("Product")
    supplier = db.relationship("Supplier")


class PurchaseOrder(db.Model):
    __tablename__ = "purchase_orders"
    __table_args__ = (db.CheckConstraint(
        "status IN ('pending','partial','completed','cancelled','pending_approval','approved','rejected',"
        "'closed_complete','closed_partial','closed_unavailable','closed_price_change')",
        name="ck_po_status"),
        db.CheckConstraint("HEX(currency) IN (HEX('USD'),HEX('VES'))", name="ck_po_currency"))
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(20), unique=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=False, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    ordered_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    estimated_delivery_date = db.Column(db.Date, index=True)
    status = db.Column(db.String(20), nullable=False, default="pending_approval", index=True)
    subtotal = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    total = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    notes = db.Column(db.String(1000))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    supplier = db.relationship("Supplier")
    creator = db.relationship("User", foreign_keys=[created_by_user_id])
    approved_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    approved_at = db.Column(db.DateTime)
    rejected_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    rejected_at = db.Column(db.DateTime)
    rejection_reason = db.Column(db.String(1000))
    supplier_quote_id = db.Column(db.Integer, db.ForeignKey("supplier_quotes.id"), index=True)
    # Room for invalid suffixes lets CHECK reject them before MariaDB can trim
    # trailing spaces to a valid three-character value during assignment.
    currency = db.Column(db.String(8), nullable=False)
    supplier_quote = db.relationship("SupplierQuote")
    replenishment_request_id = db.Column(db.Integer, db.ForeignKey("replenishment_requests.id"), unique=True)
    closed_at = db.Column(db.DateTime)
    closed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    closure_reason = db.Column(db.String(1000))
    approver = db.relationship("User", foreign_keys=[approved_by_user_id])
    rejector = db.relationship("User", foreign_keys=[rejected_by_user_id])
    lines = db.relationship("PurchaseOrderLine", back_populates="order", order_by="PurchaseOrderLine.id", cascade="all, delete-orphan")
    receptions = db.relationship("PurchaseReception", back_populates="order", order_by="PurchaseReception.id")

    @property
    def commercial_order_date(self):
        return self.ordered_at.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("America/Caracas")).date()

    def to_dict(self, detail=True):
        from app.utils.permissions import response_includes_cost

        units = sum(line.quantity_ordered for line in self.lines)
        received = sum(line.quantity_received for line in self.lines)
        # Una orden cerrada, rechazada o anulada ya no espera mercancía: lo que faltó es no suministrado.
        expecting = self.status in EXPECTING_STATUSES
        pending = sum(((line.quantity_ordered - line.quantity_received) * line.unit_cost for line in self.lines),
                      Decimal(0)) if expecting else Decimal(0)
        not_supplied = sum(line.quantity_not_supplied for line in self.lines)
        not_supplied_amount = sum((line.quantity_not_supplied * line.unit_cost for line in self.lines), Decimal(0))
        result = {"id": self.id, "order_number": self.order_number, "supplier": self.supplier.to_dict(),
                  "creator": {"id": self.created_by_user_id, "name": self.creator.name},
                  "ordered_at": self.ordered_at.isoformat(),
                  "ordered_date": self.commercial_order_date.isoformat(),
                  "estimated_delivery_date": self.estimated_delivery_date.isoformat() if self.estimated_delivery_date else None,
                  "status": self.status, "status_label": STATUS_LABELS[self.status], "notes": self.notes or "",
                  "units": units, "line_count": len(self.lines), "received_units": received,
                  "received_percent": int(min(100, max(0, Decimal(received) * 100 / units))) if units else 0,
                  "pending_units": units - received if expecting else 0, "quantity_not_supplied": not_supplied,
                  "expecting_supply": expecting,
                  "supplier_quote_id": self.supplier_quote_id,
                  "currency": self.currency,
                  "replenishment_request_id": self.replenishment_request_id,
                  "closure_reason": self.closure_reason,
                  "closed_at": self.closed_at.isoformat() if self.closed_at else None,
                  "closed_by_user_id": self.closed_by_user_id}
        if self.replenishment_request_id:
            from app.models.replenishment_request import ReplenishmentRequest
            source = db.session.get(ReplenishmentRequest, self.replenishment_request_id)
            result['replenishment_request_number'] = source.request_number if source else None
        if response_includes_cost():
            result.update(subtotal=f"{self.subtotal:.2f}", total=f"{self.total:.2f}", pending_amount=f"{pending:.2f}",
                          not_supplied_amount=f"{not_supplied_amount:.2f}")
        result.update(approved_by_user_id=self.approved_by_user_id,
                      approved_by=self.approver.name if self.approver else None,
                      approved_at=self.approved_at.isoformat() if self.approved_at else None,
                      rejected_by_user_id=self.rejected_by_user_id,
                      rejected_by=self.rejector.name if self.rejector else None,
                      rejected_at=self.rejected_at.isoformat() if self.rejected_at else None,
                      rejection_reason=self.rejection_reason, created_at=self.created_at.isoformat())
        from app.models.purchase_closure import PurchaseClosureRequest
        latest = (PurchaseClosureRequest.query.filter_by(purchase_order_id=self.id)
                  .order_by(PurchaseClosureRequest.id.desc()).first())
        result["closure_request"] = latest.to_dict() if latest is not None else None
        if detail:
            result.update(items=[line.to_dict() for line in self.lines], receptions=[row.to_dict() for row in self.receptions],
                          shortage_requests=self._shortage_requests())
        return result

    def _shortage_requests(self):
        """Solicitudes nuevas creadas con el faltante de esta orden; la orden no cambia."""
        from app.models.replenishment_request import (
            STATUS_LABELS as REQUEST_LABELS, ReplenishmentRequest, shortage_still_covered,
        )

        rows = (ReplenishmentRequest.query.filter_by(source_purchase_order_id=self.id)
                .order_by(ReplenishmentRequest.id).all())
        result = []
        for row in rows:
            generated = db.session.get(PurchaseOrder, row.purchase_order_id) if row.purchase_order_id else None
            order_status = generated.status if generated else None
            result.append({"id": row.id, "request_number": row.request_number, "status": row.status,
                           "status_label": REQUEST_LABELS.get(row.status, row.status),
                           "purchase_order_id": row.purchase_order_id,
                           "purchase_order_number": generated.order_number if generated else None,
                           "purchase_order_status_label": STATUS_LABELS.get(order_status) if order_status else None,
                           "covers_shortage": shortage_still_covered(row.status, order_status)})
        return result


class PurchaseOrderLine(db.Model):
    __tablename__ = "purchase_order_lines"
    __table_args__ = (db.UniqueConstraint("purchase_order_id", "product_id", name="uq_po_product"),
                     db.CheckConstraint("quantity_ordered > 0 AND quantity_received >= 0 AND quantity_received <= quantity_ordered", name="ck_po_quantities"),
                     db.CheckConstraint("unit_cost >= 0", name="ck_po_cost"))
    id = db.Column(db.Integer, primary_key=True)
    purchase_order_id = db.Column(db.Integer, db.ForeignKey("purchase_orders.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    product_code = db.Column(db.String(50), nullable=False)
    product_name = db.Column(db.String(150), nullable=False)
    quantity_ordered = db.Column(db.Integer, nullable=False)
    quantity_received = db.Column(db.Integer, nullable=False, default=0)
    quantity_not_supplied = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    unit_cost = db.Column(db.Numeric(12, 2), nullable=False)
    line_total = db.Column(db.Numeric(12, 2), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    order = db.relationship("PurchaseOrder", back_populates="lines")

    def to_dict(self):
        from app.utils.permissions import response_includes_cost

        payload = {"id": self.id, "product_id": self.product_id, "product_code": self.product_code,
                   "product_name": self.product_name, "quantity_ordered": self.quantity_ordered,
                   "quantity_received": self.quantity_received,
                   "quantity_pending": self.quantity_ordered - self.quantity_received,
                   # Una orden cerrada no espera mercancía: documenta el faltante en quantity_not_supplied.
                   "quantity_expected": (self.quantity_ordered - self.quantity_received
                                         if self.order.status in EXPECTING_STATUSES else 0),
                   "quantity_not_supplied": self.quantity_not_supplied}
        if response_includes_cost():
            payload.update(unit_cost=f"{self.unit_cost:.2f}", line_total=f"{self.line_total:.2f}")
        return payload


class PurchaseReception(db.Model):
    __tablename__ = "purchase_receptions"
    id = db.Column(db.Integer, primary_key=True)
    purchase_order_id = db.Column(db.Integer, db.ForeignKey("purchase_orders.id"), nullable=False, index=True)
    received_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    received_at = db.Column(db.Date, nullable=False)
    notes = db.Column(db.String(1000))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    order = db.relationship("PurchaseOrder", back_populates="receptions")
    responsible = db.relationship("User")
    lines = db.relationship("PurchaseReceptionLine", order_by="PurchaseReceptionLine.id")

    def to_dict(self):
        return {"id": self.id, "received_at": self.received_at.isoformat(), "notes": self.notes or "",
                "responsible": self.responsible.name,
                "items": [{"line_id": line.purchase_order_line_id, "quantity": line.quantity,
                           "movement_id": line.stock_movement_id} for line in self.lines]}


class PurchaseReceptionLine(db.Model):
    __tablename__ = "purchase_reception_lines"
    __table_args__ = (db.UniqueConstraint("reception_id", "purchase_order_line_id", name="uq_reception_po_line"),
                     db.CheckConstraint("quantity > 0", name="ck_reception_qty"))
    id = db.Column(db.Integer, primary_key=True)
    reception_id = db.Column(db.Integer, db.ForeignKey("purchase_receptions.id"), nullable=False, index=True)
    purchase_order_line_id = db.Column(db.Integer, db.ForeignKey("purchase_order_lines.id"), nullable=False)
    stock_movement_id = db.Column(db.Integer, db.ForeignKey("stock_movements.id"), nullable=False, unique=True)
    quantity = db.Column(db.Integer, nullable=False)
