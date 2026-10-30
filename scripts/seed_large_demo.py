"""Synthetic, local-only demonstration data; never empirical thesis evidence.

Run explicitly: python scripts/seed_large_demo.py [--reset-seed]
Pure, database-free checks: python scripts/seed_large_demo.py --self-test
Stop Flask/schedulers while running. Reset refuses modified rows or external use.
No application factory, migrations, dataset files, credentials or reports are written.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import random
import socket
import sys
from types import SimpleNamespace
from unittest.mock import patch
import zlib

SEED = 20260921
MARK = "DEMO-LARGE-20260921"
START, END = date(2026, 1, 1), date(2026, 9, 20)
MANIFEST_TAG = "\nSEED-MANIFEST-V1:"
LOCK_NAME = "conejo.seed_large_demo.20260921"
TABLES = ("categories", "products", "suppliers", "customers", "users", "sales",
          "sale_lines", "stock_movements", "purchase_orders", "purchase_order_lines",
          "purchase_receptions", "purchase_reception_lines")
DELETE_ORDER = ("sale_lines", "purchase_reception_lines", "purchase_receptions",
                "purchase_order_lines", "sales", "purchase_orders", "stock_movements",
                "products", "categories", "customers", "suppliers", "users")
# Synthetic commercial descriptions; no real people, brands, addresses or contacts.
CATALOG = (
    ("Cementos y morteros", "saco", 950, ("Cemento gris", "Cemento blanco", "Mortero de pega", "Friso premezclado"), ("25 kg", "42,5 kg", "50 kg")),
    ("Pinturas", "galón", 1800, ("Pintura caucho blanca", "Pintura satinada marfil", "Esmalte azul", "Fondo anticorrosivo gris"), ("1 galón", "1/4 galón", "4 galones")),
    ("Brochas y rodillos", "unidad", 350, ("Brocha plana", "Brocha angular", "Rodillo de felpa", "Minirodillo"), ('2 pulgadas', '3 pulgadas', '4 pulgadas')),
    ("Tuberías PVC", "tramo", 750, ("Tubo PVC presión", "Tubo PVC sanitario", "Tubo PVC conduit", "Tubo PVC ventilación"), ('1/2 pulgada x 3 m', '3/4 pulgada x 3 m', '1 pulgada x 3 m')),
    ("Conexiones", "unidad", 150, ("Codo PVC 90 grados", "Tee PVC", "Unión PVC", "Adaptador PVC roscado"), ('1/2 pulgada', '3/4 pulgada', '1 pulgada')),
    ("Tornillería", "caja", 450, ("Tornillo autorroscante", "Tornillo drywall", "Tornillo para madera", "Perno hexagonal"), ('1 pulgada, 100 uds.', '1 1/2 pulgadas, 100 uds.', '2 pulgadas, 100 uds.')),
    ("Clavos y fijaciones", "paquete", 280, ("Clavo de acero", "Clavo para madera", "Grapa galvanizada", "Clavo de concreto"), ('1 pulgada, 1 kg', '2 pulgadas, 1 kg', '3 pulgadas, 1 kg')),
    ("Herramientas manuales", "unidad", 1100, ("Martillo de carpintero", "Alicate universal", "Llave ajustable", "Pinza de presión"), ('6 pulgadas', '8 pulgadas', '10 pulgadas')),
    ("Cables eléctricos", "rollo", 2400, ("Cable THHN cobre rojo", "Cable THHN cobre negro", "Cable THHN cobre azul", "Cable THHN cobre verde"), ('12 AWG x 20 m', '14 AWG x 20 m', '10 AWG x 20 m')),
    ("Protección eléctrica", "unidad", 850, ("Breaker monopolar", "Breaker bipolar", "Interruptor termomagnético", "Protector de voltaje"), ('20 A', '30 A', '40 A')),
    ("Grifería", "unidad", 2200, ("Grifo para lavamanos", "Grifo de fregadero", "Llave de paso", "Mezcladora de ducha"), ('1/2 pulgada cromado', '1/2 pulgada satinado', '3/4 pulgada cromado')),
    ("Pegamentos y selladores", "envase", 650, ("Pegamento PVC", "Adhesivo de contacto", "Silicón transparente", "Sellador acrílico"), ('250 ml', '500 ml', '1 litro')),
    ("Discos y abrasivos", "unidad", 390, ("Disco de corte metal", "Disco diamantado", "Disco de desbaste", "Disco flap"), ('4 1/2 pulgadas', '7 pulgadas', '9 pulgadas')),
    ("Cerraduras", "unidad", 1850, ("Cerradura de pomo", "Cerradura de sobreponer", "Cerrojo de seguridad", "Cerradura de embutir"), ('acabado latón', 'acabado níquel', 'acabado negro')),
    ("Materiales de construcción", "unidad", 800, ("Bloque de concreto", "Adoquín de concreto", "Ladrillo de arcilla", "Bloque de ventilación"), ('10 cm', '15 cm', '20 cm')),
)


class SeedError(ValueError):
    """Only deliberately safe messages may be shown by the CLI."""


def require(condition, message):
    if not condition:
        raise SeedError(message)


def stamp(day, slot=0):
    # Noon Caracas = 16:00 UTC. Unique chronological slots within each day.
    return datetime.combine(day, time(16)) + timedelta(seconds=slot)


def profile(index):
    for bound, name in ((8, "diaria"), (20, "creciente"), (32, "decreciente"),
                        (52, "estable"), (132, "semanal"), (292, "media"),
                        (392, "baja"), (472, "intermitente"), (492, "escasa")):
        if index < bound:
            return name
    return "sin ventas"


def catalog_rows():
    rows = []
    for index in range(500):
        category = index % len(CATALOG)
        _, unit, cents, names, sizes = CATALOG[category]
        variant = index // len(CATALOG)
        price = Decimal(cents + variant * 35) / 100
        rows.append({"code": f"{MARK}-P{index:03d}",
                     "name": f"{names[variant % 4]} {sizes[(variant // 4) % 3]} · Serie demo {variant // 12 + 1}",
                     "description": f"{MARK}; producto sintético; demanda {profile(index)}",
                     "unit": unit, "current_stock": 0, "minimum_stock": 12 + index % 19,
                     "purchase_price": str(price), "sale_price": str((price * Decimal("1.35")).quantize(Decimal("0.01"))),
                     "category_index": category})
    return rows


def demand_weight(index, day):
    if index < 8:
        return 0  # Explicitly one guaranteed sale per day.
    if index < 52:
        return 4
    if index < 132:
        return 8 if (day + index) % 7 == 0 else 0
    if index < 292:
        return 2
    if index < 392:
        return 0.4
    if index < 472:
        return 2 if (day + index) % 65 < 9 else 0
    if index < 492:
        return 4 if day == 244 + (index - 472) % 18 else 0
    return 0


def sale_plan():
    rng = random.Random(SEED)
    days = (END - START).days + 1
    result = []
    for day in range(days):
        count = 2000 // days + (day < 2000 % days)
        carts = [[] for _ in range(count)]
        for index in range(8):
            carts[index % count].append(index)
        weights = [demand_weight(i, day) for i in range(500)]
        for cart in carts:
            available = weights.copy()
            for index in cart:
                available[index] = 0
            while len(cart) < 5:
                index = rng.choices(range(500), weights=available)[0]
                cart.append(index)
                available[index] = 0
            items = []
            for index in cart:
                if 8 <= index < 20:
                    quantity = 2 + day // 12 + rng.randrange(3)
                elif 20 <= index < 32:
                    quantity = 25 - day // 12 + rng.randrange(3)
                elif index < 52:
                    quantity = 5 + rng.randrange(3)
                else:
                    quantity = 1 + rng.randrange(6)
                items.append((index, quantity))
            result.append((START + timedelta(days=day), items))
    return result


def configured_url():
    # Imports/configuration loading occur ONLY on explicitly authorized CLI execution.
    from app.config import Config
    from sqlalchemy.engine import make_url

    require(os.getenv("DB_HOST") == "localhost" and os.getenv("DB_NAME") == "ferreteria_conejo",
            "Se requieren DB_HOST=localhost y DB_NAME=ferreteria_conejo explícitos; no se aceptan valores por defecto.")
    url = make_url(Config.SQLALCHEMY_DATABASE_URI)
    require(url.drivername == "mysql+pymysql" and url.host == "localhost" and url.database == "ferreteria_conejo",
            "La conexión efectiva no corresponde a la base local autorizada.")
    require(url.port == int(os.getenv("DB_PORT", "3306")) and 1 <= url.port <= 65535
            and dict(url.query) == {"charset": "utf8mb4"}, "Puerto u opciones de conexión no permitidos.")
    for name in ("DATABASE_URL", "SQLALCHEMY_DATABASE_URI"):
        require(not os.getenv(name) or make_url(os.environ[name]) == url,
                "Existe una URL alternativa de conexión; retire esa configuración antes de continuar.")
    addresses = {row[4][0] for row in socket.getaddrinfo("localhost", url.port, type=socket.SOCK_STREAM)}
    require(addresses and all(ipaddress.ip_address(value).is_loopback for value in addresses),
            "localhost no resuelve exclusivamente a direcciones loopback.")
    # Pin a numeric loopback after validation: no later DNS change can redirect PyMySQL.
    return url.set(host="127.0.0.1" if "127.0.0.1" in addresses else sorted(addresses)[0])


def load_domain():
    from app.extensions import db, login_manager
    from app import models
    from app.services import (category_service, customer_service, inventory_service,
                              product_service, purchase_service, sale_service,
                              supplier_service, notification_service)
    return SimpleNamespace(**locals())


@contextmanager
def domain_session(domain, connection):
    from sqlalchemy.orm import scoped_session, sessionmaker

    original = domain.db.session
    session = scoped_session(sessionmaker(bind=connection, join_transaction_mode="create_savepoint"))
    domain.db.session = session
    try:
        # CLI-only: do not dispatch alerts or contact external providers.
        with patch.object(domain.notification_service, "_safe", lambda *args, **kwargs: None):
            yield session
    finally:
        session.remove()
        domain.db.session = original


def preflight(connection, domain):
    from sqlalchemy import inspect, text

    inspector = inspect(connection)
    require(set(TABLES).issubset(inspector.get_table_names()), "Faltan tablas del dominio; aplique las migraciones por separado.")
    engines = dict(connection.execute(text("SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()")).all())
    require(all(engines.get(name) == "InnoDB" for name in TABLES), "Todas las tablas del seed deben usar InnoDB; no se escribió nada.")
    cross_schema = connection.execute(text("SELECT 1 FROM information_schema.KEY_COLUMN_USAGE WHERE REFERENCED_TABLE_SCHEMA=DATABASE() AND TABLE_SCHEMA<>DATABASE() LIMIT 1")).first()
    require(cross_schema is None, "Hay relaciones entre bases; no se permite ejecutar el seed.")
    triggers = connection.execute(text("SELECT EVENT_OBJECT_TABLE FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()")).scalars()
    require(not set(triggers).intersection(TABLES), "Hay triggers en tablas del seed; sus efectos requieren revisión antes de ejecutarlo.")
    require(connection.execute(text("SELECT DATABASE()")).scalar() == "ferreteria_conejo", "Base efectiva no autorizada.")
    require(END < date.today(), "El período de demostración debe estar completamente en el pasado.")
    historical_guard(domain)


def historical_guard(domain):
    from sqlalchemy import func

    model = domain.models.HistoricalImport
    cutoff = domain.db.session.query(func.max(model.period_end)).filter(model.status == "confirmed").scalar()
    require(cutoff is None or cutoff < START,
            "Un histórico confirmado excluiría parte de las ventas sintéticas del análisis predictivo. No se modificará ese histórico.")


def table(domain, name):
    return domain.db.metadata.tables[name]


def select_rows(domain, name, ids):
    from sqlalchemy import select

    target = table(domain, name)
    # Lock the exact parents before reset checks; concurrent FK references cannot
    # appear between the check and a cascading database deletion.
    return domain.db.session.execute(select(target).where(target.c.id.in_(ids)).order_by(target.c.id).with_for_update()).mappings().all()


def fingerprint(domain, name, ids):
    rows = [dict(row) for row in select_rows(domain, name, ids)]
    require(len(rows) == len(ids), "El seed está incompleto; no se permite borrar ni duplicar datos.")
    for row in rows:
        if name == "products" and row["code"] == f"{MARK}-P000":
            row["description"] = row["description"].split(MANIFEST_TAG)[0]
    encoded = json.dumps(rows, default=str, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def owned_rows(domain):
    from sqlalchemy import select

    predicates = {
        "categories": lambda t: t.c.description.startswith(MARK),
        "products": lambda t: t.c.code.startswith(MARK),
        "suppliers": lambda t: t.c.notes.startswith(MARK),
        "customers": lambda t: t.c.email.endswith("@" + MARK.lower() + ".invalid"),
        "users": lambda t: t.c.username == MARK.lower(),
        "sales": lambda t: t.c.idempotency_key.startswith(MARK),
        "stock_movements": lambda t: t.c.reason.startswith(MARK),
        "purchase_orders": lambda t: t.c.notes.startswith(MARK),
        "purchase_receptions": lambda t: t.c.notes.startswith(MARK),
    }
    result = {}
    for name, predicate in predicates.items():
        target = table(domain, name)
        result[name] = list(domain.db.session.execute(select(target.c.id).where(predicate(target)).order_by(target.c.id)).scalars())
    for name, key, parent in (("sale_lines", "sale_id", "sales"),
                              ("purchase_order_lines", "purchase_order_id", "purchase_orders"),
                              ("purchase_reception_lines", "reception_id", "purchase_receptions")):
        target = table(domain, name)
        result[name] = list(domain.db.session.execute(select(target.c.id).where(target.c[key].in_(result[parent])).order_by(target.c.id)).scalars())
    return result


def existing_manifest(domain):
    rows = owned_rows(domain)
    if not any(rows.values()):
        return None
    carrier = domain.models.Product.query.filter_by(code=f"{MARK}-P000").first()
    require(carrier is not None and MANIFEST_TAG in (carrier.description or ""),
            "Se detectó un seed incompleto o sin manifiesto. Se requiere revisión manual; no se borró nada.")
    try:
        blob = carrier.description.split(MANIFEST_TAG, 1)[1]
        manifest = json.loads(zlib.decompress(base64.b85decode(blob)).decode("utf-8"))
    except (ValueError, zlib.error, UnicodeError):
        raise SeedError("Manifiesto del seed ilegible; no se borró nada.") from None
    require(manifest.get("version") == 1 and manifest.get("seed") == SEED and set(manifest.get("rows", {})) == set(TABLES),
            "Manifiesto no reconocido; no se borró nada.")
    require(rows == manifest["rows"], "Los identificadores del seed cambiaron; no se borrará ningún dato.")
    for name in TABLES:
        require(fingerprint(domain, name, rows[name]) == manifest["hashes"].get(name),
                "Existen registros sintéticos modificados. El reset no borrará sus cambios; requiere revisión manual.")
    return manifest


def reject_external_references(connection, domain, rows):
    from sqlalchemy import MetaData, Table, inspect, select, or_, and_

    inspector = inspect(connection)
    for name in inspector.get_table_names():
        for fk in inspector.get_foreign_keys(name):
            parent = fk["referred_table"]
            if parent not in rows or not rows[parent]:
                continue
            require(fk.get("referred_schema") in (None, "ferreteria_conejo") and fk["referred_columns"] == ["id"]
                    and len(fk["constrained_columns"]) == 1, "Relación no reconocida: reset cancelado por seguridad.")
            child = Table(name, MetaData(), autoload_with=connection)
            query = select(child.c[fk["constrained_columns"][0]]).where(child.c[fk["constrained_columns"][0]].in_(rows[parent]))
            if name in rows:
                query = query.where(child.c.id.not_in(rows[name]))
            require(domain.db.session.execute(query.limit(1).with_for_update()).first() is None,
                    f"Hay referencias ajenas al seed en {name}; reset cancelado sin borrar datos.")
    notification = domain.models.Notification
    terms = [and_(notification.entity_type == kind, notification.entity_id.in_(rows[name]))
             for kind, name in (("product", "products"), ("sale", "sales"), ("purchase_order", "purchase_orders"))]
    require(domain.db.session.query(notification.id).filter(or_(*terms)).first() is None,
            "Hay notificaciones posteriores asociadas al seed; reset cancelado sin borrarlas.")
    snapshot = domain.models.InventorySnapshot
    require(domain.db.session.query(snapshot.id).filter(snapshot.category_id.in_(rows["categories"])).first() is None,
            "Hay cierres de inventario asociados al seed; reset cancelado sin borrarlos.")


def reset_rows(domain, rows):
    from sqlalchemy import delete

    for name in DELETE_ORDER:
        target = table(domain, name)
        domain.db.session.execute(delete(target).where(target.c.id.in_(rows[name])))
    domain.db.session.commit()  # Only releases the session SAVEPOINT.
    domain.db.session.expire_all()


def set_dates(row, when):
    for field in ("created_at", "updated_at"):
        if hasattr(row, field):
            setattr(row, field, when)


def movement_dates(movement, when):
    movement.created_at = when
    movement.reason = f"{MARK}; {movement.reason or 'Movimiento sintético'}"
    movement.product.updated_at = when


def create_catalog(domain):
    from flask_login import login_user

    session = domain.db.session
    actor = domain.models.User(name="Operador sintético (sin acceso)", username=MARK.lower(),
                               email=f"operador@{MARK.lower()}.invalid", role="admin", is_active=False,
                               email_verified=False, password_hash=None)
    set_dates(actor, stamp(START))
    session.add(actor)
    session.flush()
    # Dedicated CLI identity only; the persistent account stays inactive/passwordless.
    principal = SimpleNamespace(id=actor.id, role=actor.role, is_active=False, is_authenticated=True,
                                get_id=lambda: str(actor.id))
    login_user(principal, force=True)
    categories = []
    for name, *_ in CATALOG:
        category = domain.category_service.create_category({"name": f"{name} [DEMO 20260921]", "description": MARK})
        set_dates(category, stamp(START))
        categories.append(category)
    customers = []
    for index in range(200):
        row = domain.customer_service.create_customer({"name": f"Cliente sintético {index + 1:03d}",
                                                      "email": f"cliente{index:03d}@{MARK.lower()}.invalid"})
        set_dates(row, stamp(START))
        customers.append(row)
    suppliers = []
    for index in range(30):
        row = domain.supplier_service.save({"name": f"Distribuidora Demo {index + 1:02d}",
                    "tax_id": f"DEMO-20260921-{index:02d}", "contact_name": f"Contacto sintético {index + 1:02d}",
                    "phone": "0000-0000000", "email": f"proveedor{index:02d}@{MARK.lower()}.invalid",
                    "category": CATALOG[index % 15][0], "notes": MARK, "is_active": True})
        set_dates(row, stamp(START))
        suppliers.append(row)
    products = []
    for values in catalog_rows():
        values["category_id"] = categories[values.pop("category_index")].id
        row = domain.product_service.create_product(values)
        set_dates(row, stamp(START))
        products.append(row)
        movement = domain.inventory_service.register_entry({"product_id": row.id, "quantity": 30,
                                                            "reason": "Apertura sintética"}, commit=False)
        movement_dates(movement, stamp(START, 1))
    session.commit()
    return products, customers, suppliers


def create_purchase(domain, products, suppliers, index, day):
    selected = [products[(index * 7 + offset * 31) % 492] for offset in range(3)]
    order = domain.purchase_service.create_order({"supplier_id": suppliers[index % 30].id,
              "notes": f"{MARK}; orden {index:03d}", "estimated_delivery_date": (day + timedelta(days=5)).isoformat(),
              "items": [{"product_id": p.id, "quantity": 40 + index % 30, "unit_cost": str(p.purchase_price)} for p in selected]}, commit=False)
    order.ordered_at = stamp(day, 10)
    set_dates(order, order.ordered_at)
    for line in order.lines:
        set_dates(line, order.ordered_at)
    domain.db.session.commit()
    return order


def receive_purchase(domain, order, day, complete, sequence):
    data = {"received_at": day.isoformat(), "notes": f"{MARK}; recepción {sequence}", "items": []}
    for line in order.lines:
        remaining = line.quantity_ordered - line.quantity_received
        quantity = remaining if complete else max(1, remaining // 2)
        data["items"].append({"line_id": line.id, "quantity": quantity})
    domain.purchase_service.receive_order(order.id, data)
    when = stamp(day, 50 + sequence)
    reception = order.receptions[-1]
    set_dates(reception, when)
    for line in reception.lines:
        movement = domain.db.session.get(domain.models.StockMovement, line.stock_movement_id)
        movement_dates(movement, when)
    order.updated_at = when
    domain.db.session.commit()


def create_sale(domain, products, customers, index, day, items):
    when = stamp(day, 1000 + index * 5)
    for product_index, quantity in items:
        product = products[product_index]
        if product.current_stock < quantity:
            movement = domain.inventory_service.register_entry({"product_id": product.id, "quantity": quantity + 120,
                                                                "reason": "Reposición sintética de demostración"}, commit=False)
            movement_dates(movement, when - timedelta(seconds=1))
    sale, created = domain.sale_service.create_sale({"customer_id": customers[index % 200].id,
                  "payment_method": ("cash", "transfer", "debit_card")[index % 3], "payment_condition": "cash",
                  "idempotency_key": f"{MARK}-V{index:04d}",
                  "items": [{"product_id": products[p].id, "quantity": q} for p, q in items]})
    require(created, "Colisión de idempotencia: se aborta toda la transacción.")
    sale.sold_at = when
    set_dates(sale, when)
    for line in sale.lines:
        set_dates(line, when)
        movement_dates(line.stock_movement, when)
    domain.db.session.commit()


def generate(domain):
    products, customers, suppliers = create_catalog(domain)
    plan = sale_plan()
    purchases, receipts = {}, {}
    # 140 completed (70 in two receipts), 40 partial, 20 pending.
    for index in range(200):
        day = START + timedelta(days=(index * 251) // 200)
        purchases.setdefault(day, []).append(index)
    sale_index = 0
    for offset in range((END - START).days + 1):
        day = START + timedelta(days=offset)
        for index in purchases.get(day, []):
            order = create_purchase(domain, products, suppliers, index, day)
            if index < 180:
                complete = index < 70
                receipts.setdefault(day + timedelta(days=2), []).append((order, complete))
                if 70 <= index < 140:
                    receipts.setdefault(day + timedelta(days=5), []).append((order, True))
        for sequence, (order, complete) in enumerate(receipts.get(day, [])):
            receive_purchase(domain, order, day, complete, sequence)
        while sale_index < len(plan) and plan[sale_index][0] == day:
            create_sale(domain, products, customers, sale_index, day, plan[sale_index][1])
            sale_index += 1
    for index, product in enumerate(products):
        target = 0 if index % 10 == 0 else (max(1, product.minimum_stock // 2) if index % 10 < 3 else 100 + index % 151)
        if product.current_stock != target:
            movement = domain.inventory_service.register_adjustment({"product_id": product.id, "new_stock": target,
                                      "reason": f"{MARK}; escenario final de stock sintético"})
            movement_dates(movement, stamp(END, 20000 + index))
    domain.db.session.commit()


def validate_generated(domain, rows):
    require(len(rows["products"]) == 500 and len(rows["categories"]) == 15 and len(rows["suppliers"]) == 30
            and len(rows["customers"]) == 200 and len(rows["sales"]) == 2000 and len(rows["sale_lines"]) == 10000
            and len(rows["purchase_orders"]) == 200 and 8000 <= len(rows["stock_movements"]) <= 15000,
            "El volumen generado no cumple el contrato; se revierte todo.")
    movements = select_rows(domain, "stock_movements", rows["stock_movements"])
    balances = {}
    for row in sorted(movements, key=lambda r: (r["created_at"], r["id"])):
        previous = balances.get(row["product_id"], 0)
        require(row["previous_stock"] == previous and row["new_stock"] >= 0,
                "El saldo cronológico de inventario no es coherente; se revierte todo.")
        delta = row["new_stock"] - previous
        require(abs(delta) == row["quantity"] and (row["movement_type"] == "ajuste"
                or (row["movement_type"] == "entrada" and delta > 0)
                or (row["movement_type"] == "salida" and delta < 0)), "Movimiento incoherente; se revierte todo.")
        balances[row["product_id"]] = row["new_stock"]
    for product in select_rows(domain, "products", rows["products"]):
        require(product["current_stock"] == balances[product["id"]], "Saldo final incorrecto; se revierte todo.")
    statuses = {row["status"] for row in select_rows(domain, "purchase_orders", rows["purchase_orders"])}
    require(statuses == {"pending", "partial", "completed"}, "Faltan escenarios de compras; se revierte todo.")


def save_manifest(domain, rows):
    from sqlalchemy import update

    carrier = domain.models.Product.query.filter_by(code=f"{MARK}-P000").one()
    carrier.updated_at = stamp(END, 21000)
    domain.db.session.commit()
    manifest = {"version": 1, "seed": SEED, "rows": rows,
                "hashes": {name: fingerprint(domain, name, rows[name]) for name in TABLES}}
    blob = base64.b85encode(zlib.compress(json.dumps(manifest, sort_keys=True).encode("utf-8"))).decode("ascii")
    description = carrier.description + MANIFEST_TAG + blob
    require(len(description.encode("utf-8")) < 65000, "El manifiesto excede el campo disponible; se revierte todo.")
    # Core UPDATE includes the timestamp explicitly even when its value is unchanged;
    # an ORM no-op assignment would let onupdate replace it and break the fingerprint.
    domain.db.session.execute(update(domain.models.Product).where(domain.models.Product.id == carrier.id)
                              .values(description=description, updated_at=stamp(END, 21000)))
    domain.db.session.commit()
    require(existing_manifest(domain) is not None, "No se pudo verificar el manifiesto.")
    return manifest


def seed_transaction(connection, domain, reset=False):
    historical_guard(domain)
    manifest = existing_manifest(domain)
    if manifest and not reset:
        return manifest, False
    if manifest:
        reject_external_references(connection, domain, manifest["rows"])
        reset_rows(domain, manifest["rows"])
    generate(domain)
    rows = owned_rows(domain)
    validate_generated(domain, rows)
    return save_manifest(domain, rows), True


def print_summary(manifest, created):
    print("SEED COMPLETADO" if created else "SEED YA EXISTENTE: no se insertaron duplicados")
    for label, name in (("Categorías", "categories"), ("Productos", "products"), ("Proveedores", "suppliers"),
                        ("Clientes", "customers"), ("Usuarios sin acceso", "users"), ("Ventas", "sales"),
                        ("Líneas de venta", "sale_lines"), ("Movimientos", "stock_movements"),
                        ("Órdenes de compra", "purchase_orders"), ("Recepciones", "purchase_receptions")):
        print(f"{label}: {len(manifest['rows'][name])}")
    print(f"Período histórico: {START} – {END}")
    print("Datos exclusivamente sintéticos para probar software; NO son resultados empíricos de la tesis.")
    print("No se crearon credenciales de acceso. El reset rechaza datos editados o utilizados después del seed.")


def run(reset):
    from flask import Flask
    from sqlalchemy import text

    url = configured_url()
    domain = load_domain()
    app = Flask("large_demo_seed")
    app.config.update(SECRET_KEY=os.urandom(32), SQLALCHEMY_DATABASE_URI=url,
                      SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      SQLALCHEMY_ENGINE_OPTIONS={"connect_args": {"connect_timeout": 5}, "isolation_level": "READ COMMITTED"})
    domain.db.init_app(app)
    domain.login_manager.init_app(app)
    with app.app_context(), app.test_request_context():
        with domain.db.engine.connect() as connection:
            acquired = connection.execute(text("SELECT GET_LOCK(:name, 0)"), {"name": LOCK_NAME}).scalar()
            require(acquired == 1, "Ya hay otro seed ejecutándose; no se escribió nada.")
            connection.commit()
            try:
                with connection.begin(), domain_session(domain, connection):
                    preflight(connection, domain)
                    manifest, created = seed_transaction(connection, domain, reset)
                    domain.db.session.commit()
                print_summary(manifest, created)
            finally:
                connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": LOCK_NAME})
                connection.commit()


def self_test():
    # Pure checks: no app import, dotenv loading, SQLAlchemy, files or connections.
    plan = sale_plan()
    require(plan == sale_plan(), "La generación no es determinista.")
    require(len(plan) == 2000 and sum(len(items) for _, items in plan) == 10000, "Volumen incorrecto.")
    rows = catalog_rows()
    require(len({row["code"] for row in rows}) == 500 and len({row["name"] for row in rows}) == 500, "Catálogo duplicado.")
    demand_days = {i: set() for i in range(500)}
    for day, items in plan:
        require(START <= day <= END and len({p for p, _ in items}) == 5, "Venta fuera del contrato.")
        for index, quantity in items:
            require(quantity > 0, "Cantidad inválida.")
            demand_days[index].add(day)
    require(all(len(demand_days[i]) == (END - START).days + 1 for i in range(8)), "Falta demanda diaria.")
    require(all(not demand_days[i] for i in range(492, 500)), "Falta escenario sin demanda.")
    require(all(len(demand_days[i]) <= 1 for i in range(472, 492)), "Demanda escasa incorrecta.")
    print("SELF-TEST OK: determinismo, catálogo, 2.000 ventas/10.000 líneas y patrones; ninguna conexión ni escritura.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--reset-seed", action="store_true", help="Recrear únicamente el seed intacto, sin referencias externas.")
    group.add_argument("--self-test", action="store_true", help="Verificar el plan en memoria, sin leer configuración ni conectar bases.")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        if args.self_test:
            self_test()
        else:
            run(args.reset_seed)
    except SeedError as exc:
        print(f"ABORTADO: {exc}", file=sys.stderr)
        return 1
    except Exception:
        # Database/configuration exceptions may contain credentials or existing data.
        print("ABORTADO: fallo de configuración, esquema o servicio. La transacción no se completó; no se muestran datos sensibles.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
