"""Pure deterministic fixture specification; no DB, thesis files or secrets."""
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
import json
import random

DEMO_SEED_VERSION = 2
RANDOM_SEED = 20261003
ANCHOR = date(2026, 10, 3)
OPENING = datetime(2026, 6, 5, 12)

CATEGORIES = (
    ("Herramientas", "hammer"), ("Electricidad", "zap"), ("Plomería", "wrench"),
    ("Pinturas", "paintbrush"), ("Construcción", "hard-hat"), ("Tornillería", "package"),
    ("Adhesivos y selladores", "droplet"), ("Seguridad", "shield"),
    ("Jardinería", "tag"), ("Ferretería general", "package"),
)
# name, category index, unit, unit purchase cost. No real brands.
CATALOG = (
    ("Tornillo para madera 2 pulgadas", 5, "caja", "3.20"),
    ("Cinta aislante 18 mm", 1, "unidad", "0.85"),
    ("Cemento gris 42,5 kg", 4, "saco", "7.80"),
    ("Pintura blanca interior 4 litros", 3, "galón", "12.50"),
    ("Codo PVC de media pulgada", 2, "unidad", "0.40"),
    ("Sellador de juntas 280 ml", 6, "cartucho", "2.90"),
    ("Llave ajustable 12 pulgadas", 0, "unidad", "8.40"),
    ("Martillo de uña 16 onzas", 0, "unidad", "5.70"),
    ("Alicate universal 8 pulgadas", 0, "unidad", "4.80"),
    ("Juego de destornilladores", 0, "juego", "6.60"),
    ("Cable eléctrico calibre 12", 1, "metro", "0.72"),
    ("Tomacorriente doble", 1, "unidad", "1.40"),
    ("Interruptor sencillo", 1, "unidad", "1.15"),
    ("Bombillo LED 12 W", 1, "unidad", "1.95"),
    ("Tubo PVC de media pulgada", 2, "tramo", "2.30"),
    ("Llave de paso de media pulgada", 2, "unidad", "3.10"),
    ("Adaptador roscado PVC", 2, "unidad", "0.55"),
    ("Cinta de teflón 12 mm", 2, "rollo", "0.35"),
    ("Brocha de 2 pulgadas", 3, "unidad", "1.25"),
    ("Rodillo de pintura 9 pulgadas", 3, "unidad", "2.65"),
    ("Lija para pared grano 120", 3, "hoja", "0.25"),
    ("Fondo anticorrosivo gris", 3, "litro", "4.20"),
    ("Bloque de concreto 15 cm", 4, "unidad", "0.65"),
    ("Arena lavada ensacada", 4, "saco", "2.10"),
    ("Mortero para pego 20 kg", 4, "saco", "5.50"),
    ("Cabilla de acero 3 octavos", 4, "barra", "5.25"),
    ("Clavo de acero 2 pulgadas", 5, "caja", "2.15"),
    ("Tuerca hexagonal M8", 5, "paquete", "1.30"),
    ("Arandela plana M8", 5, "paquete", "0.95"),
    ("Tornillo autoperforante", 5, "caja", "3.70"),
    ("Silicona transparente 280 ml", 6, "cartucho", "2.40"),
    ("Adhesivo para PVC 125 ml", 6, "envase", "1.85"),
    ("Pegamento de contacto", 6, "litro", "4.60"),
    ("Espuma expansiva 500 ml", 6, "envase", "4.95"),
    ("Guante de trabajo tejido", 7, "par", "0.90"),
    ("Lente de protección transparente", 7, "unidad", "1.60"),
    ("Casco de seguridad blanco", 7, "unidad", "4.30"),
    ("Mascarilla antipolvo", 7, "paquete", "2.20"),
    ("Chaleco reflectivo", 7, "unidad", "3.80"),
    ("Manguera de jardín media pulgada", 8, "metro", "0.85"),
    ("Rastrillo metálico", 8, "unidad", "5.15"),
    ("Pala cuadrada", 8, "unidad", "7.20"),
    ("Tijera de podar", 8, "unidad", "5.90"),
    ("Regadera de jardín 5 litros", 8, "unidad", "3.50"),
    ("Candado de latón 40 mm", 9, "unidad", "3.65"),
    ("Bisagra para puerta 3 pulgadas", 9, "par", "1.45"),
    ("Cuerda de nylon 6 mm", 9, "metro", "0.30"),
    ("Cinta métrica 5 metros", 0, "unidad", "2.75"),
    ("Cadena galvanizada 4 mm", 9, "metro", "1.80"),
    ("Escuadra metálica de soporte", 9, "par", "1.10"),
)


def products():
    rng = random.Random(RANDOM_SEED)
    result = []
    for number, (name, category, unit, cost) in enumerate(CATALOG, 1):
        target = 35 + number % 7 * 9
        if number <= 5:
            target = 0
        elif number <= 13:
            target = number - 4
        elif 36 <= number <= 40:
            target = 300 + number
        result.append(dict(number=number, code=f"DEMO-{number:03d}", name=f"DEMO · {name}",
            category=category, unit=unit, purchase_price=cost,
            sale_price=str((Decimal(cost) * Decimal(rng.choice(("1.30", "1.40", "1.50"))))
                           .quantize(Decimal("0.01"))), minimum_stock=10 if number <= 13 else 15,
            target=target, active=number < 49))
    return result


def sales():
    result = []
    for i in range(120):
        number = i % 5 + 1
        quantities = {1: 18 + i % 4, 2: 5, 3: 2 + i // 15, 4: 18 - i // 10, 5: 8}
        items = [(number, quantities[number])]
        if i % 3 == 0:
            extra = 6 if i % 15 == 0 else 7 + (i // 3) % 38
            items.append((extra, 1 + i % 4))
        result.append(dict(index=i, at=OPENING + timedelta(days=i + 1, hours=3), items=items))
    return result


def purchases():
    states = ["completed"] * 10 + ["partial"] * 2 + ["approved"] * 3 + ["pending_approval"] * 3 + ["rejected", "cancelled"]
    result = []
    for i, state in enumerate(states):
        at = OPENING + timedelta(days=i * 6 + 1, hours=1)
        items = [(16 + i % 10 * 2, 20 + i % 3 * 5), (17 + i % 10 * 2, 18 + i % 4 * 4)]
        received = [(n, q if state == "completed" else q // 2) for n, q in items] if i < 12 else []
        result.append(dict(index=i, at=at, state=state, items=items, received=received,
                           receive_at=at + timedelta(days=2), supplier=i % 10))
    return result


def notes():
    return [dict(index=i, at=OPENING + timedelta(days=5 + i * 10, hours=4),
                 items=[(25 + i, 1 + i % 3)]) for i in range(12)]


def adjustments():
    return [dict(number=n, delta=d, at=OPENING + timedelta(days=119, hours=5, minutes=n),
                 reason="DEMO · Conteo físico: " + reason)
            for n, d, reason in ((41, 2, "dos unidades omitidas al contar"),
                                 (42, -1, "una unidad dañada retirada"),
                                 (43, 1, "unidad recuperada en estante"),
                                 (44, -2, "dos envases dañados retirados"))]


def initial_stocks():
    deltas = defaultdict(int)
    for event in sales() + notes():
        for n, q in event["items"]:
            deltas[n] -= q
    for order in purchases():
        for n, q in order["received"]:
            deltas[n] += q
    for event in adjustments():
        deltas[event["number"]] += event["delta"]
    return {p["number"]: p["target"] - deltas[p["number"]] for p in products()}


def historical_rows():
    catalogue = {p["number"]: p for p in products()}
    for i in range(365):
        day = date(2025, 1, 1) + timedelta(days=i)
        quantities = {1: 18 + i % 4, 2: 5, 3: 2 + i // 45, 4: 18 - i // 30, 5: 8}
        if i % 14 == 0:
            quantities[6] = 3 + i % 5
        if i % 45 == 0:
            quantities[7] = 1
        if i in (14, 164):
            quantities[46] = 1
        for n, q in quantities.items():
            p = catalogue[n]
            key = f"DEMO-V2-{i:03d}-{n:03d}"
            yield (day.isoformat(), p["code"], p["name"], q, "sale", "issued", key, key, "1", p["sale_price"])


def manifest_hash():
    content = (DEMO_SEED_VERSION, RANDOM_SEED, ANCHOR, products(), sales(), purchases(),
               notes(), adjustments(), list(historical_rows()))
    return hashlib.sha256(json.dumps(content, default=str, sort_keys=True).encode()).hexdigest()
