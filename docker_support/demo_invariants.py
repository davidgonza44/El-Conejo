"""Read-only, exhaustive ledger/document checks for the pristine DEMO v2 fixture."""
from collections import Counter
from decimal import Decimal

from sqlalchemy import inspect, text

from app.extensions import db
from app.models import (Category, Customer, DeliveryNote, DeliveryNoteItem, HistoricalDemandRecord,
    HistoricalImport, Product, Sale, SaleLine, StockMovement, User)
from app.models.purchase import (ProductSupplier, PurchaseOrder, PurchaseOrderLine,
    PurchaseReception, PurchaseReceptionLine, Supplier)
from app.models.reconciliation import ReconciliationLine, ReconciliationRun
from app.services import demand_data_service, product_state_history_service
from docker_support import demo_data as data
from docker_support.guard import validate_demo

EXPECTED = {"products": 50, "categories": 10, "suppliers": 10, "customers": 26, "sales": 120,
    "sale_lines": 160, "stock_movements": 250, "purchase_orders": 20, "purchase_order_lines": 40,
    "purchase_receptions": 12, "purchase_reception_lines": 24, "delivery_notes": 12,
    "delivery_note_items": 12, "historical_imports": 1, "historical_demand_records": 1863,
    "product_suppliers": 50, "reconciliation_runs": 1, "reconciliation_lines": 17, "users": 3}
MODELS = (Product, Category, Supplier, Customer, Sale, SaleLine, StockMovement, PurchaseOrder,
    PurchaseOrderLine, PurchaseReception, PurchaseReceptionLine, DeliveryNote, DeliveryNoteItem,
    HistoricalImport, HistoricalDemandRecord, ProductSupplier, ReconciliationRun, ReconciliationLine, User)


def require(condition, label):
    if not condition:
        raise RuntimeError("DEMO invariant failed: " + label)


def verify_catalogue():
    rows = Product.query.order_by(Product.code).all()
    require([p.code for p in rows] == [p["code"] for p in data.products()], "50 exact DEMO codes")
    for product, spec in zip(rows, data.products()):
        for field in ("name", "unit", "minimum_stock"):
            require(getattr(product, field) == spec[field], "catalogue " + field)
        require(product.current_stock == spec["target"] and product.is_active == spec["active"], "target stock/status")
        require(product.sale_price == Decimal(spec["sale_price"]), "sale price")
        require(product.purchase_price == Decimal(spec["purchase_price"]), "purchase cost")
    require(sum(p.current_stock == 0 and p.is_active for p in rows) == 5, "five out of stock")
    require(sum(0 < p.current_stock < p.minimum_stock for p in rows) == 8, "eight below minimum")
    require(sum(p.current_stock > 10 * p.minimum_stock for p in rows) == 5, "five overstock")
    generic = Customer.query.filter_by(is_walk_in=True).all()
    require(len(generic) == 1 and generic[0].name == "Público en general", "protected walk-in customer")
    require(all(c.name.startswith("DEMO ") and c.email.endswith("@example.invalid") and not c.tax_id
                for c in Customer.query.filter_by(is_walk_in=False)), "synthetic customers, no plausible real RIF")
    require(all(s.tax_id.startswith("DEMO-RIF-") and s.email.endswith("@example.invalid")
                for s in Supplier.query), "synthetic suppliers")
    require(all(c.name.startswith("DEMO · ") for c in Category.query), "synthetic categories")
    require(not any(product_state_history_service.validate_invariants().values()), "state history")


def verify_ledger():
    initial = data.initial_stocks()
    for product in Product.query.order_by(Product.code):
        balance = 0
        movements = StockMovement.query.filter_by(product_id=product.id).order_by(
            StockMovement.created_at, StockMovement.id).all()
        require(movements[0].creation_origin == "initial_stock", "initial movement")
        require(movements[0].quantity == initial[int(product.code[-3:])], "specified opening balance")
        for m in movements:
            require(m.previous_stock == balance and m.new_stock >= 0 and m.quantity > 0, "nonnegative continuous ledger")
            delta = m.new_stock - m.previous_stock
            require(abs(delta) == m.quantity, "movement quantity matches delta")
            require(m.movement_type == "ajuste" or
                    (m.movement_type == "entrada" and delta > 0) or
                    (m.movement_type == "salida" and delta < 0), "movement direction")
            balance = m.new_stock
        require(balance == product.current_stock, "opening + receipts + adjustments - sales - deliveries")
    require(Counter(m.movement_type for m in StockMovement.query) ==
            {"entrada": 74, "salida": 172, "ajuste": 4}, "movement distribution")


def verify_documents():
    linked = []
    for sale in Sale.query:
        require(sale.idempotency_key.startswith("DEMO-V2-VENTA-"), "sale DEMO identifier")
        require(sale.total == sum(l.line_total for l in sale.lines), "sale total")
        require(sale.subtotal - sale.discount_total == sale.total, "sale discount total")
        for line in sale.lines:
            m = line.stock_movement
            require(m.product_id == line.product_id and m.quantity == line.quantity and m.movement_type == "salida", "sale link")
            require(m.created_at == sale.sold_at == line.created_at, "sale dates")
            require(line.line_subtotal == line.unit_price * line.quantity and
                    line.line_total == line.line_subtotal - line.discount_amount, "line amounts")
            linked.append(m.id)
    for note in DeliveryNote.query:
        require(note.total_amount == sum(l.line_total for l in note.items), "delivery total")
        require(note.customer_name.startswith("DEMO "), "delivery customer")
        for line in note.items:
            m = line.issue_stock_movement
            require(m and m.product_id == line.product_id and m.quantity == line.quantity and
                    m.movement_type == "salida" and m.created_at == note.created_at, "delivery link/date")
            linked.append(m.id)
    require(len(linked) == len(set(linked)) == 172, "no shared or double-counted sale/delivery exits")
    require(set(linked) == {m.id for m in StockMovement.query.filter_by(movement_type="salida")}, "all exits justified")


def verify_purchases():
    statuses = Counter(o.status for o in PurchaseOrder.query)
    require(statuses == {"completed": 10, "partial": 2, "approved": 3, "pending_approval": 3,
                         "rejected": 1, "cancelled": 1}, "purchase statuses")
    for order in PurchaseOrder.query:
        require(order.notes.startswith("DEMO "), "purchase identifier")
        require(order.total == sum(l.quantity_ordered * l.unit_cost for l in order.lines), "purchase total")
        for actor in (order.approved_by_user_id, order.rejected_by_user_id):
            require(actor is None or actor != order.created_by_user_id, "no self approval")
        for line in order.lines:
            receptions = PurchaseReceptionLine.query.filter_by(purchase_order_line_id=line.id).all()
            require(sum(r.quantity for r in receptions) == line.quantity_received <= line.quantity_ordered, "received vs ordered")
            for receipt_line in receptions:
                m = db.session.get(StockMovement, receipt_line.stock_movement_id)
                receipt = db.session.get(PurchaseReception, receipt_line.reception_id)
                require(receipt.purchase_order_id == order.id and m.product_id == line.product_id and
                        m.quantity == receipt_line.quantity and m.movement_type == "entrada", "receipt movement")
                require(order.ordered_at <= order.approved_at <= m.created_at and
                        receipt.received_at == m.created_at.date(), "purchase approval/receipt chronology")
    entries = {m.id for m in StockMovement.query.filter_by(movement_type="entrada") if m.creation_origin != "initial_stock"}
    require(entries == {r.stock_movement_id for r in PurchaseReceptionLine.query}, "every noninitial entry has a receipt")


def verify_history():
    batch = HistoricalImport.query.one()
    require(batch.status == "confirmed" and batch.source_system == "DEMO-VISUAL-V2", "confirmed DEMO-only historical batch")
    require(batch.original_filename == "DEMO-v2-historico-2025.csv", "independent synthetic CSV")
    records = HistoricalDemandRecord.query.all()
    require(all(r.include_in_demand and r.product_id and r.quantity > 0 and r.event_date.year == 2025 for r in records), "valid historical records")
    cutoff = demand_data_service.get_temporal_cutoff()
    require(cutoff.operational_starts_on.isoformat() == "2026-01-01", "nonoverlapping historical/operational cutoff")
    events = demand_data_service.load_demand_events(cutoff)
    require(len(events) == len(records) + 172, "canonical demand counts each operation once")
    run = ReconciliationRun.query.one()
    require((run.status, run.match_count, run.discrepancy_count, run.unmatched_count) ==
            ("confirmed", 14, 2, 1), "reconciliation exact/difference/unmatched")
    require(all(line.stock_movement_id is None for line in run.lines), "review never changes stock")
    require(sum(line.review_status == "reviewed" for line in run.lines) == 1, "one reviewed difference")


def verify_dataset():
    validate_demo()
    require(db.engine.url.database == validate_demo() and db.engine.url.host == "db", "isolated target")
    counts = {model.__tablename__: model.query.count() for model in MODELS}
    require(counts == EXPECTED, "exact main-table counts")
    for check in (verify_catalogue, verify_ledger, verify_documents, verify_purchases, verify_history):
        check()
    with db.engine.connect() as connection:
        require(connection.execute(text("SELECT @@foreign_key_checks")).scalar_one() == 1, "FK enforcement enabled")
        inspector = inspect(connection)
        for table in db.metadata.sorted_tables:
            require(len(inspector.get_foreign_keys(table.name)) == len(table.foreign_key_constraints), "FK count " + table.name)
    return counts
