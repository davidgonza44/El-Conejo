"""Seed 100 % sintético para ferreteria_conejo_tesis. Nunca evidencia empírica.

No crea bases ni tablas. --plan no conecta; --reset-seed rechaza cambios posteriores.
"""
import argparse
from collections import Counter
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

from seed_tesis_data import (START, END, FAMILIES, FIRST_NAMES, LAST_NAMES, SUPPLIERS,
                            catalog, commercial_plan, delivery_note_schedule, payment_method, plan_summary, stamp)
import seed_tesis_safety as safety


def set_dates(row, when):
    for field in ("created_at", "updated_at"):
        if hasattr(row, field):
            setattr(row, field, when)


def movement_date(movement, when):
    movement.created_at = when
    movement.product.updated_at = when


# Color persistido por familia (tonos aprobados de CATEGORY_COLORS).
# Las 10 familias llevan 10 tonos distintos no grises (gray reservado a "Otros"):
# brown y navy eliminan las dos repeticiones del mapeo inicial.
FAMILY_COLORS = ("blue", "yellow", "teal", "purple", "pink",
                 "green", "red", "navy", "orange", "brown")


def use_actor(actor):
    from flask_login import login_user

    principal = SimpleNamespace(id=actor.id, role=actor.role, is_active=actor.is_active, is_authenticated=True,
                                get_id=lambda: str(actor.id))
    login_user(principal, force=True)  # CLI principal only; no access credentials are created.


def create_actors(domain):
    actors = []
    for name, username, role, active in (
        ("Rafael Villalba", "rafael.villalba", "admin", False),
        ("Mariana Duarte", "mariana.duarte", "inventario", True),
        ("Daniel Acosta", "daniel.acosta", "vendedor", True),
        ("Laura Mendoza", "laura.mendoza", "asistente", True),
    ):
        actor = domain.models.User(name=name, username=username, email=f"{username}@example.invalid",
            role=role, is_active=active, email_verified=False, password_hash=None)
        set_dates(actor, stamp(START))
        domain.db.session.add(actor)
        actors.append(actor)
    domain.db.session.flush()
    return actors


def create_catalog(domain, plan):
    actors = create_actors(domain)
    use_actor(actors[0])
    categories = []
    for index, family in enumerate(FAMILIES):
        row = domain.category_service.create_category(
            {"name": family[0], "description": "", "color": FAMILY_COLORS[index]})
        set_dates(row, stamp(START))
        categories.append(row)
    customers = []
    for last in LAST_NAMES:
        for first in FIRST_NAMES:
            row = domain.customer_service.create_customer({"name": f"{first} {last}"})
            set_dates(row, stamp(START))
            customers.append(row)
    suppliers = []
    for index, name in enumerate(SUPPLIERS):
        row = domain.supplier_service.save(dict(name=name + " C.A.", tax_id=f"J-90000{index:03d}-0",
            contact_name=f"{FIRST_NAMES[index]} Alvarado", phone="No registrado",
            email=f"ventas@{FAMILIES[index][1].lower()}.example.invalid", category=FAMILIES[index][0],
            notes="Recepción de mercancía en horario de almacén.", is_active=True))
        set_dates(row, stamp(START))
        suppliers.append(row)
    products = []
    for index, values in enumerate(catalog()):
        values["category_id"] = categories[values.pop("family")].id
        row = domain.product_service.create_product(values)
        set_dates(row, stamp(START))
        baselines = domain.models.ProductStateHistory.query.filter_by(product_id=row.id).all()
        safety.require(len(baselines) == 1 and baselines[0].event_type == "baseline",
                       "El alta no dejó un baseline único; se revierte todo.")
        baselines[0].changed_at = stamp(START)
        movement = domain.inventory_service.register_entry(dict(product_id=row.id,
            quantity=plan["initial"][index], reason="Existencia inicial según conteo de almacén"), commit=False)
        movement_date(movement, stamp(START, 1))
        products.append(row)
    domain.db.session.commit()
    return products, customers, suppliers, actors


def purchase(domain, event, products, suppliers, when):
    from app.services import supplier_quote_service
    supplier = suppliers[event["family"]]
    items = [dict(product_id=products[i].id, quantity=q, unit_price=str(products[i].purchase_price))
             for i, q in event["items"]]
    quote = supplier_quote_service.create(dict(
        supplier_id=supplier.id, issued_on=str(event["day"]), currency="VES",
        valid_until=str(event["day"] + timedelta(days=30)), notes="Cotización de reposición.",
        items=items), commit=False)
    row = domain.purchase_service.create_order(dict(supplier_id=supplier.id, supplier_quote_id=quote.id,
        notes="Reposición de mercancía para almacén.",
        estimated_delivery_date=str(event["day"] + timedelta(days=2)),
        items=[dict(product_id=item["product_id"], quantity=item["quantity"]) for item in items]), commit=False)
    row.ordered_at = when
    set_dates(row, when)
    for line in row.lines:
        set_dates(line, when)
    domain.db.session.commit()
    return row


def receive(domain, event, order, products, when):
    quantities = {products[i].id: q for i, q in event["items"]}
    domain.purchase_service.receive_order(order.id, dict(received_at=str(event["day"]),
        notes="Mercancía recibida y verificada en almacén.",
        items=[dict(line_id=line.id, quantity=quantities[line.product_id]) for line in order.lines
               if line.product_id in quantities]))
    reception = order.receptions[-1]
    set_dates(reception, when)
    for line in reception.lines:
        set_dates(line, when)
        movement_date(domain.db.session.get(domain.models.StockMovement, line.stock_movement_id), when)
    order.updated_at = when


def sell(domain, event, products, customers, sequence, when):
    row, created = domain.sale_service.create_sale(dict(customer_id=customers[sequence % 60].id,
        payment_method=payment_method(sequence), payment_condition="cash",
        idempotency_key=str(uuid5(NAMESPACE_URL, f"{safety.DATASET}/sale/{sequence}")),
        items=[dict(product_id=products[i].id, quantity=q) for i, q in event["items"]]))
    safety.require(created, "Colisión de idempotencia; se revierte todo.")
    row.sold_at = when
    set_dates(row, when)
    for line in row.lines:
        set_dates(line, when)
        movement_date(line.stock_movement, when)


def documents(domain, products, customers):
    # Clock changes are scoped to CLI services, never the application or database clock.
    for index in range(40):
        day = END - timedelta(days=45 if index % 5 == 4 else index % 7)
        when = stamp(day, 25000 + index)
        items = [dict(product_id=products[12 * (index % 10) + j].id, quantity=2 + index % 3) for j in (0, 1)]
        with patch.object(domain.sales_common, "commercial_today", return_value=day), \
             patch.object(domain.sales_common, "utc_now", return_value=when):
            customer = customers[(index * 17) % len(customers)]
            quote_document(domain, customer.id, items, index, when)
            order_document(domain, customer.id, items, index, when, day)
            domain.db.session.commit()


def quote_document(domain, customer_id, items, index, when):
    quote = domain.quote_service.create_quote(dict(customer_id=customer_id, validity_days=15,
        status="pending", items=items, notes="Precios sujetos a disponibilidad al confirmar la compra."))
    status = ("pending", "sent", "accepted", "rejected", "pending")[index % 5]
    if status != "pending":
        domain.quote_service.change_status(quote.quote_number, {"status": status})
    set_dates(quote, when)
    for line in quote.lines:
        set_dates(line, when)


def order_document(domain, customer_id, items, index, when, day):
    order = domain.order_service.create_order(dict(customer_id=customer_id,
        estimated_delivery_date=str(day + timedelta(days=7)), items=items,
        priority=("low", "medium", "high", "urgent")[index % 4], payment_status="pending",
        notes="Retiro de mercancía coordinado con el cliente."))
    target = ("pending", "processing", "completed", "cancelled")[index % 4]
    if target in ("processing", "completed"):
        domain.order_service.change_status(order.order_number, {"status": "processing"})
    if target in ("completed", "cancelled"):
        domain.order_service.change_status(order.order_number, {"status": target})
    if target == "completed" or (target == "processing" and index % 8 == 1):
        domain.order_service.change_payment_status(order.order_number, {"payment_status": "paid"})
    set_dates(order, when)
    for line in order.lines:
        set_dates(line, when)


def note_movements(domain, product_ids, when):
    for product_id in product_ids:
        movement = (domain.models.StockMovement.query
                    .filter_by(product_id=product_id).order_by(domain.models.StockMovement.id.desc()).first())
        movement_date(movement, when)


def delivery_notes(domain, plan, products, customers, actors):
    schedule = sorted(delivery_note_schedule(plan), key=lambda note: (note["day"], note["second"]))
    for note in schedule:
        use_actor(actors[2])
        customer = customers[note["customer"]]
        when = stamp(note["day"], note["second"])
        row = domain.delivery_note_service.create_note(dict(customer_name=customer.name,
            customer_document=customer.tax_id, customer_phone=customer.phone,
            customer_address="Valencia, Carabobo",
            items=[dict(product_id=products[index].id, quantity=quantity) for index, quantity in note["items"]]))
        set_dates(row, when)
        for line in row.items:
            set_dates(line, when)
        note_movements(domain, [products[index].id for index, _ in note["items"]], when)
        if note["cancel"]:
            use_actor(actors[1])
            domain.delivery_note_service.cancel_note(row.id)
            cancelled_at = when + timedelta(minutes=30)
            row.cancelled_at = cancelled_at
            for line in row.items:
                entry = (domain.models.StockMovement.query
                         .filter(domain.models.StockMovement.reason ==
                                 f"Cancelación de nota de entrega #{row.note_number}",
                                 domain.models.StockMovement.product_id == line.product_id)
                         .order_by(domain.models.StockMovement.id.desc()).first())
                movement_date(entry, cancelled_at)
        domain.db.session.commit()


def generate(domain, plan):
    products, customers, suppliers, actors = create_catalog(domain, plan)
    purchases, slots, sales = {}, Counter(), 0
    for event in plan["events"]:
        day, kind = event["day"], event["kind"]
        slots[day] += 1
        when = stamp(day, 60 + slots[day] * 30)
        use_actor(actors[3] if kind == "purchase" else actors[2] if kind == "sale" else actors[1])
        if kind == "purchase":
            purchases[event["order"]] = purchase(domain, event, products, suppliers, when)
        elif kind == "receive":
            order = purchases[event["order"]]
            if order.status == "pending_approval":
                use_actor(actors[0])
                domain.purchase_service.decide_order(order.id, {}, approve=True)
                order.approved_at = when
                use_actor(actors[1])
            receive(domain, event, order, products, when)
        elif kind == "cancel":
            row = domain.purchase_service.cancel_order(purchases[event["order"]].id)
            row.updated_at = when
        elif kind == "sale":
            sell(domain, event, products, customers, sales, when)
            sales += 1
        elif kind == "adjust":
            movement = domain.inventory_service.register_adjustment(dict(product_id=products[event["product"]].id,
                new_stock=event["stock"], reason=event["reason"]))
            movement_date(movement, when)
        domain.db.session.commit()
    use_actor(actors[2])
    documents(domain, products, customers)
    delivery_notes(domain, plan, products, customers, actors)


def validate_generated(domain, rows, plan):
    expected = plan_summary(plan)
    safety.require(all(len(rows[name]) == expected[name] for name in safety.TABLES), "Volúmenes inesperados; rollback.")
    balances = {}
    movements = safety.select_rows(domain, "stock_movements", rows["stock_movements"])
    for movement in sorted(movements, key=lambda m: (m["created_at"], m["id"])):
        before = balances.get(movement["product_id"], 0)
        delta = movement["new_stock"] - before
        kind = movement["movement_type"]
        safety.require(movement["previous_stock"] == before and movement["new_stock"] >= 0
            and abs(delta) == movement["quantity"] and (kind == "ajuste" or
                (kind == "entrada" and delta > 0) or (kind == "salida" and delta < 0)), "Saldo cronológico incoherente.")
        balances[movement["product_id"]] = movement["new_stock"]
    products = safety.select_rows(domain, "products", rows["products"])
    issued = Counter()
    for note in delivery_note_schedule(plan):
        if not note["cancel"]:
            issued.update(dict(note["items"]))
    final = [stock - issued[index] for index, stock in enumerate(plan["stocks"])]
    safety.require(all(p["current_stock"] == balances[p["id"]] == stock for p, stock in zip(products, final)),
                   "Saldo final incoherente con las operaciones.")


def seed_transaction(connection, domain, created, reset=False):
    safety.require(domain.models.HistoricalImport.query.first() is None
        and domain.models.HistoricalDemandRecord.query.first() is None, "No se permite mezclar históricos con esta demostración.")
    previous = safety.existing_manifest(domain)
    if previous and not reset:
        return previous, False
    if previous:
        safety.reject_external_references(connection, domain, previous["payload"]["rows"])
        safety.reset_rows(domain, previous["payload"]["rows"])
    plan = commercial_plan()
    generate(domain, plan)
    rows = {name: sorted(created[name]) for name in safety.TABLES}
    validate_generated(domain, rows, plan)
    return safety.write_manifest(domain, rows, previous), True


def run(reset=False):
    url = safety.configured_url()
    from flask import Flask

    safety.require(END < date.today(), "El período debe estar completamente en el pasado.")
    domain = safety.load_domain()
    app = Flask("tesis_seed")
    app.config.update(SECRET_KEY=os.urandom(32), SQLALCHEMY_DATABASE_URI=url, SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={"connect_args": {"connect_timeout": 5}, "isolation_level": "READ COMMITTED"})
    domain.db.init_app(app)
    domain.login_manager.init_app(app)
    with app.app_context(), app.test_request_context(), domain.db.engine.connect() as connection:
        with safety.locked(connection):
            created = {name: set() for name in safety.TABLES}
            with connection.begin(), safety.domain_session(domain, connection, created):
                safety.mysql_preflight(connection)
                safety.validate_domain_schema(connection, domain)
                record, changed = seed_transaction(connection, domain, created, reset)
            print("SEED COMPLETADO" if changed else "SEED YA EXISTENTE; sin duplicados")
            print(json.dumps({name: len(ids) for name, ids in record["payload"]["rows"].items()}, indent=2))
            print("100 % sintético. No es evidencia empírica ni sustituye históricos reales auditados.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--reset-seed", action="store_true")
    group.add_argument("--plan", action="store_true", help="Plan puro: no lee configuración ni conecta bases.")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    if args.plan:
        print(json.dumps(plan_summary(commercial_plan()), indent=2))
        return 0
    try:
        run(args.reset_seed)
    except safety.SeedError as exc:
        print(f"ABORTADO: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("ABORTADO: fallo de configuración, esquema o servicio; transacción no completada. No se muestran datos sensibles.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
