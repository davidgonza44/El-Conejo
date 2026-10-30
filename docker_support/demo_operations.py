"""Execute the fixed DEMO event timeline using existing domain services."""
import csv
from datetime import timedelta
from decimal import Decimal
from io import BytesIO, StringIO
import secrets

from flask_login import login_user
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models import StockMovement
from app.models.product_state_history import ProductStateHistory
from app.models.purchase import PurchaseReceptionLine
from app.services import (auth_service, category_service, customer_service, delivery_note_service,
    historical_import_service, inventory_service, product_service, product_supplier_service,
    purchase_service, reconciliation_service, sale_service, supplier_service)
from app.services.historical_validation_service import CSV_HEADERS
from docker_support import demo_data as data


def stamp(row, at, *fields):
    """Only fixture dates, AFTER domain validation; never amounts, states or stock."""
    for field in fields or ("created_at",):
        setattr(row, field, at)


def create_directory(admin, buyer):
    categories = [category_service.create_category({"name": f"DEMO · {name}",
        "description": "Catálogo exclusivamente sintético para demostración", "icon": icon,
        "color": ("blue", "green", "orange", "purple", "teal")[i % 5]})
        for i, (name, icon) in enumerate(data.CATEGORIES)]
    login_user(buyer)
    suppliers = []
    for i, (category, _) in enumerate(data.CATEGORIES, 1):
        suppliers.append(supplier_service.save(dict(name=f"DEMO Suministros Taller {i:02d}",
            tax_id=f"DEMO-RIF-{i:03d}", contact_name=f"DEMO Contacto {i:02d}",
            phone=f"DEMO-SIN-LINEA-{i:02d}", email=f"proveedor{i:02d}@example.invalid",
            category=category, address=f"DEMO Zona ficticia, local {i}", city="Ciudad DEMO",
            notes="Identidad ficticia. No contactar; sin relación con la empresa real.", is_active=True)))
    login_user(admin)
    clients = [customer_service.walk_in_customer()]
    for i in range(1, 26):
        clients.append(customer_service.create_customer(dict(name=f"DEMO Cliente Taller {i:02d}",
            phone=f"DEMO-SIN-LINEA-{i:02d}", email=f"cliente{i:02d}@example.invalid")))
    for row in categories + suppliers + clients[1:]:
        stamp(row, data.OPENING)
        if hasattr(row, "updated_at"):
            stamp(row, data.OPENING, "updated_at")
    db.session.commit()
    return categories, suppliers, clients


def create_catalogue(categories, suppliers):
    initial = data.initial_stocks()
    products = {}
    for spec in data.products():
        number = spec["number"]
        product = product_service.create_product({key: spec[key] for key in (
            "code", "name", "unit", "purchase_price", "sale_price", "minimum_stock")} | {
            "category_id": categories[spec["category"]].id, "current_stock": initial[number],
            "description": "DEMO v2 · Datos sintéticos; uso visual y funcional, fuera del experimento de tesis."})
        stamp(product, data.OPENING, "created_at", "updated_at")
        movement = StockMovement.query.filter_by(product_id=product.id).one()
        stamp(movement, data.OPENING)
        movement.reason = "DEMO · Saldo inicial al comenzar el registro operativo"
        baseline = ProductStateHistory.query.filter_by(product_id=product.id).one()
        stamp(baseline, data.OPENING, "changed_at")
        db.session.commit()
        link = product_supplier_service.create_link(product.id, dict(
            supplier_id=suppliers[(number - 16) // 2 % 10].id,
            purchase_price=spec["purchase_price"], supplier_product_code=spec["code"],
            lead_time_days=3 + number % 8, minimum_order_quantity=2 + number % 4, is_preferred=True))
        stamp(link, data.OPENING, "created_at", "updated_at")
        db.session.commit()
        products[number] = product
    return products


def create_sale(event, products, clients):
    payload = dict(customer_id=clients[event["index"] % len(clients)].id,
        payment_method=("cash", "debit_card", "transfer")[event["index"] % 3], payment_condition="cash",
        idempotency_key=f"DEMO-V2-VENTA-{event['index']:03d}",
        items=[dict(product_id=products[n].id, quantity=q, discount_percent="5" if event["index"] % 12 == 0 else "0")
               for n, q in event["items"]])
    if payload["payment_method"] == "cash":
        # Explicit synthetic tender; the service still owns discounts and change.
        payload["cash_received"] = str(sum((products[n].sale_price * q for n, q in event["items"]), Decimal(0)))
    sale, created = sale_service.create_sale(payload)
    if not created:
        raise RuntimeError("Unexpected DEMO sale replay during a clean seed.")
    stamp(sale, event["at"], "sold_at", "created_at", "updated_at")
    for line in sale.lines:
        stamp(line, event["at"])
        stamp(line.stock_movement, event["at"])
    db.session.commit()


def create_note(event, products, clients):
    client = clients[1 + event["index"]]
    note = delivery_note_service.create_note(dict(customer_name=client.name,
        customer_document=f"DEMO-CLIENTE-{event['index'] + 1:02d}", customer_phone=client.phone,
        customer_address="DEMO · Retiro independiente en mostrador; no corresponde a una venta registrada",
        items=[dict(product_id=products[n].id, quantity=q) for n, q in event["items"]]))
    stamp(note, event["at"])
    for line in note.items:
        stamp(line, event["at"])
        stamp(line.issue_stock_movement, event["at"])
    db.session.commit()


def create_order(event, products, suppliers, buyer, admin):
    login_user(buyer)
    order = purchase_service.create_order(dict(supplier_id=suppliers[event["supplier"]].id,
        estimated_delivery_date=event["receive_at"].date().isoformat(),
        notes=f"DEMO v2 · Abastecimiento sintético {event['index'] + 1:02d}",
        items=[dict(product_id=products[n].id, quantity=q, unit_cost=str(products[n].purchase_price))
               for n, q in event["items"]]))
    stamp(order, event["at"], "ordered_at", "created_at", "updated_at")
    for line in order.lines:
        stamp(line, event["at"])
    db.session.commit()
    login_user(admin)
    if event["state"] in ("completed", "partial", "approved", "rejected"):
        approved = event["state"] != "rejected"
        purchase_service.decide_order(order.id, {} if approved else {"reason": "DEMO · Solicitud no prioritaria"},
                                      approve=approved)
        stamp(order, event["at"] + timedelta(minutes=30), "approved_at" if approved else "rejected_at", "updated_at")
    elif event["state"] == "cancelled":
        purchase_service.cancel_order(order.id)
        stamp(order, event["at"] + timedelta(minutes=30), "updated_at")
    db.session.commit()
    return order


def receive(event, order, products):
    ids = {line.product_id: line.id for line in order.lines}
    purchase_service.receive_order(order.id, dict(received_at=event["receive_at"].date().isoformat(),
        notes="DEMO · Mercancía sintética recibida y contada",
        items=[dict(line_id=ids[products[n].id], quantity=q) for n, q in event["received"]]))
    reception = order.receptions[-1]
    stamp(reception, event["receive_at"])
    stamp(order, event["receive_at"], "updated_at")
    for line in PurchaseReceptionLine.query.filter_by(reception_id=reception.id):
        stamp(db.session.get(StockMovement, line.stock_movement_id), event["receive_at"])
    db.session.commit()


def run_timeline(products, suppliers, clients, buyer, admin, seller):
    timeline = [(e["at"], "sale", e) for e in data.sales()]
    timeline += [(e["at"], "note", e) for e in data.notes()]
    timeline += [(e["at"], "order", e) for e in data.purchases()]
    timeline += [(e["receive_at"], "receive", e) for e in data.purchases() if e["received"]]
    timeline += [(e["at"], "adjustment", e) for e in data.adjustments()]
    orders = {}
    for _, kind, event in sorted(timeline, key=lambda e: (e[0], e[1])):
        if kind == "sale":
            login_user(seller)
            create_sale(event, products, clients)
        elif kind == "note":
            login_user(seller)
            create_note(event, products, clients)
        elif kind == "order":
            orders[event["index"]] = create_order(event, products, suppliers, buyer, admin)
        elif kind == "receive":
            login_user(buyer)
            receive(event, orders[event["index"]], products)
        else:
            login_user(buyer)
            product = products[event["number"]]
            movement = inventory_service.register_adjustment(dict(product_id=product.id,
                new_stock=product.current_stock + event["delta"], reason=event["reason"]), creation_origin="manual")
            stamp(movement, event["at"])
            db.session.commit()


def historical(admin):
    content = StringIO(newline="")
    writer = csv.writer(content, delimiter=";", lineterminator="\n")
    writer.writerow(CSV_HEADERS)
    writer.writerows(data.historical_rows())
    upload = FileStorage(stream=BytesIO(content.getvalue().encode("utf-8-sig")), filename="DEMO-v2-historico-2025.csv")
    batch = historical_import_service.upload_import(upload, source_system="DEMO-VISUAL-V2",
        document_type="DEMO-SYNTHETIC", actor_user_id=admin.id)
    historical_import_service.preview_import(batch.public_id, {}, actor_user_id=admin.id)
    _, token, _ = historical_import_service.dry_run_import(batch.public_id, actor_user_id=admin.id)
    historical_import_service.confirm_import(batch.public_id, confirmation_token=token, actor_user_id=admin.id)


def reconciliation(products):
    content = "product_code;quantity;product_name\n"
    for n in range(1, 17):
        p = products[n]
        difference = {14: 3, 15: -2}.get(n, 0)
        content += f"{p.code};{p.current_stock + difference};{p.name}\n"
    content += "DEMO-POR-REVISAR;4;DEMO Código pendiente de identificación\n"
    run = reconciliation_service.create_preview(content.encode(), "DEMO-v2-conteo.csv", "2026-10-03T16:00:00-04:00")
    reconciliation_service.transition(run.id, confirm=True)
    line = next(line for line in run.lines if line.product_code == "DEMO-014")
    reconciliation_service.process_line(run.id, line.id, {"note": "DEMO · Diferencia revisada; pendiente de decisión"}, resolve=False)


def populate(app, admin):
    with app.test_request_context():
        login_user(admin)
        buyer = auth_service.register(dict(name="DEMO Responsable de compras", username="demo_compras",
            email="demo_compras@example.invalid", password="Aa1!" + secrets.token_urlsafe(32), role="inventario"),
            allowed_roles=("inventario",))
        seller = auth_service.register(dict(name="DEMO Responsable de ventas", username="demo_ventas",
            email="demo_ventas@example.invalid", password="Aa1!" + secrets.token_urlsafe(32), role="vendedor"),
            allowed_roles=("vendedor",))
        categories, suppliers, clients = create_directory(admin, buyer)
        products = create_catalogue(categories, suppliers)
        run_timeline(products, suppliers, clients, buyer, admin, seller)
        login_user(admin)
        for n in (49, 50):
            product_service.deactivate_product(products[n].id)
            change = ProductStateHistory.query.filter_by(product_id=products[n].id, event_type="change").one()
            stamp(change, data.OPENING + timedelta(days=119, hours=8), "changed_at")
        for product in products.values():
            last = StockMovement.query.filter_by(product_id=product.id).order_by(StockMovement.created_at.desc()).first()
            stamp(product, last.created_at, "updated_at")
        db.session.commit()
        historical(admin)
        reconciliation(products)
