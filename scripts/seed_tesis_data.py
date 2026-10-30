"""Plan comercial 100 % sintético; no contiene ni lee históricos del negocio."""
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import random

SEED = 20260921
START, END = date(2026, 1, 1), date(2026, 9, 20)
# Each family has compatible commercial variants (4 articles x 3 presentations).
FAMILIES = (
    ("Cementos y morteros", "CEM", "saco", 9, ("Cemento gris", "Cemento blanco", "Mortero de pega", "Friso premezclado"), ("25 kg", "42,5 kg", "50 kg")),
    ("Pinturas", "PIN", "envase", 14, ("Pintura caucho blanca", "Pintura satinada marfil", "Esmalte azul", "Fondo anticorrosivo gris"), ("1/4 galón", "1 galón", "4 galones")),
    ("Tuberías PVC", "PVC", "tramo", 4, ("Tubo PVC presión", "Tubo PVC sanitario", "Tubo PVC conduit", "Tubo PVC ventilación"), ("1/2 pulgada x 3 m", "3/4 pulgada x 3 m", "1 pulgada x 3 m")),
    ("Conexiones PVC", "CON", "unidad", 1, ("Codo PVC 90 grados", "Tee PVC", "Unión PVC", "Adaptador PVC roscado"), ("1/2 pulgada", "3/4 pulgada", "1 pulgada")),
    ("Tornillería", "TOR", "caja", 3, ("Tornillo autorroscante", "Tornillo drywall", "Tornillo para madera", "Perno hexagonal"), ("1 pulgada, 100 uds.", "1 1/2 pulgadas, 100 uds.", "2 pulgadas, 100 uds.")),
    ("Herramientas manuales", "HER", "unidad", 8, ("Alicate universal", "Llave ajustable", "Pinza de presión", "Alicate de corte"), ("6 pulgadas", "8 pulgadas", "10 pulgadas")),
    ("Cables eléctricos", "CAB", "rollo", 18, ("Cable THHN cobre rojo", "Cable THHN cobre negro", "Cable THHN cobre azul", "Cable THHN cobre verde"), ("14 AWG x 20 m", "12 AWG x 20 m", "10 AWG x 20 m")),
    ("Pegamentos y selladores", "PEG", "envase", 3, ("Pegamento PVC", "Adhesivo de contacto", "Sellador acrílico blanco", "Sellador acrílico gris"), ("250 ml", "500 ml", "1 litro")),
    ("Discos y abrasivos", "DIS", "unidad", 2, ("Disco de corte metal", "Disco diamantado", "Disco de desbaste", "Disco flap"), ("4 1/2 pulgadas", "7 pulgadas", "9 pulgadas")),
    ("Cerraduras", "CER", "unidad", 12, ("Cerradura de pomo", "Cerradura de sobreponer", "Cerrojo de seguridad", "Cerradura de embutir"), ("acabado latón", "acabado níquel", "acabado negro")),
)
FIRST_NAMES = ("Ana", "Luis", "María", "Carlos", "Elena", "José", "Adriana", "Pedro", "Valeria", "Miguel")
LAST_NAMES = ("Salcedo", "Figueroa", "Mendoza", "Paredes", "Rivas", "Cordero")
SUPPLIERS = ("Materiales La Ceiba", "Pinturas Los Cedros", "Tuberías El Roble", "Conexiones La Colina",
             "Fijaciones El Samán", "Herramientas Los Olivos", "Conductores La Loma", "Adhesivos El Nogal",
             "Abrasivos La Cumbre", "Cerraduras El Portal")
FAMILY_WEIGHTS = (22, 16, 14, 12, 11, 9, 8, 7, 4, 3)
RELATED_FAMILIES = ((4, 5), (7, 8), (3, 7), (2, 7), (5, 8), (4, 8), (3, 5), (2, 1), (5, 4), (5, 4))
# Commercial preferences within each family, independent of SKU ordering.
# Gray cement; gallon paints; pressure/sanitary pipe; elbows/unions; drywall/wood
# screws; 8-inch pliers/wrenches; red/black cable; PVC glue/sealant; cutting discs.
POPULAR_REFERENCES = ((1, 3), (2, 5), (1, 5), (3, 7), (4, 7),
                      (2, 5), (2, 4), (1, 8), (1, 5), (1, 2))


def demand_order(family):
    order = list(range(12))
    for position, reference in enumerate(POPULAR_REFERENCES[family]):
        previous = order.index(reference - 1)
        order[position], order[previous] = order[previous], order[position]
    return order


def stamp(day, seconds=0):
    return datetime.combine(day, time(13)) + timedelta(seconds=seconds)  # 09:00 Caracas


def catalog():
    result = []
    for family, (_, prefix, unit, base, names, sizes) in enumerate(FAMILIES):
        for variant in range(12):
            size = variant % 3
            presentation = sizes[size]
            if family == 2 and variant // 3 in (1, 3):
                presentation = ("2 pulgadas x 3 m", "3 pulgadas x 3 m", "4 pulgadas x 3 m")[size]
            cost = Decimal(base) * (Decimal("0.75"), Decimal("1.00"), Decimal("1.60"))[size]
            result.append(dict(code=f"{prefix}-{variant + 1:03d}", name=f"{names[variant // 3]} {presentation}",
                description=f"Presentación: {presentation}.", unit=unit, current_stock=0,
                minimum_stock=6 if family in (5, 6, 9) else 12,
                purchase_price=str(cost.quantize(Decimal("0.01"))),
                sale_price=str((cost * Decimal("1.35")).quantize(Decimal("0.01"))), family=family))
    return result


def requested_sales():
    rng = random.Random(SEED)
    result = defaultdict(list)
    for offset in range((END - START).days + 1):
        day = START + timedelta(days=offset)
        for _ in range(6):
            result[day].append(requested_cart(rng, offset))
    return result


def requested_cart(rng, offset):
    # A purchase purpose selects the main family and compatible complementary items.
    main = rng.choices(range(10), weights=FAMILY_WEIGHTS)[0]
    count = rng.choices((1, 2, 3, 4), weights=(15, 40, 30, 15))[0]
    items, seen = [], set()
    for slot in range(count):
        family = main if slot == 0 or rng.random() < 0.7 else rng.choice(RELATED_FAMILIES[main])
        weights = variant_weights(offset)
        order = demand_order(family)
        for profile, variant in enumerate(order):
            if family * 12 + variant in seen:
                weights[profile] = 0
        profile = rng.choices(range(12), weights=weights)[0]
        index = family * 12 + order[profile]
        seen.add(index)
        cap = (8, 4, 4, 6, 4, 2, 2, 4, 3, 2)[family]
        quantity = rng.randint(1, cap)
        if family == 0 and profile < 2:
            quantity += offset // 90
        items.append((index, quantity))
    return items


def variant_weights(offset):
    # Two popular references; occasional items; discontinued, recent and unsold items.
    return [18, 18, 3, 3, 3, 2, 2, 2, 1, 2 if offset < 90 else 0,
            1 if offset >= 245 else 0, 0]


def payment_method(sequence):
    return random.Random(SEED + 100000 + sequence).choices(
        ("cash", "transfer", "debit_card", "credit_card"), weights=(31, 44, 21, 4))[0]


def commercial_plan():
    """Plan receipts before sales; shortages reduce fulfilled quantities, never stock < 0."""
    products, demand = catalog(), requested_sales()
    stocks = [p["minimum_stock"] * 3 for p in products]
    initial = stocks.copy()
    receipts, events, purchase_index = defaultdict(list), [], 0
    for offset in range((END - START).days + 1):
        day = START + timedelta(days=offset)
        if offset % 7 == 0 and day + timedelta(days=2) <= END:
            purchase_index = plan_purchases(day, demand, stocks, receipts, events, purchase_index)
        fulfill_day(day, receipts[day], demand[day], stocks, events)
        if offset % 31 == 15:
            index = 60 + offset % 12
            if stocks[index] > 0:
                stocks[index] -= 1
                events.append(dict(kind="adjust", day=day, product=index, stock=stocks[index],
                                   reason="Unidad deteriorada durante revisión de almacén"))
    open_purchases(stocks, events, purchase_index)
    return dict(initial=initial, events=events, stocks=stocks)


def fulfill_day(day, receipts, demand, stocks, events):
    for order_index, items in receipts:
        events.append(dict(kind="receive", day=day, order=order_index, items=items))
        for index, quantity in items:
            stocks[index] += quantity
    for requested in demand:
        items = [(i, min(q, stocks[i])) for i, q in requested if stocks[i] > 0]
        if items:
            events.append(dict(kind="sale", day=day, items=items))
            for index, quantity in items:
                stocks[index] -= quantity


def open_purchases(stocks, events, purchase_index):
    # Open commercial commitments near the cutoff, without invented stock effects.
    for family in range(10):
        for status in ("pending", "partial", "cancelled"):
            index = family * 12 + 5
            items = [(index, 12)]
            events.append(dict(kind="purchase", day=END, order=purchase_index, items=items, family=family))
            if status == "partial":
                events.append(dict(kind="receive", day=END, order=purchase_index, items=[(index, 6)]))
                stocks[index] += 6
            if status == "cancelled":
                events.append(dict(kind="cancel", day=END, order=purchase_index))
            purchase_index += 1


def plan_purchases(day, demand, stocks, receipts, events, sequence):
    needs = Counter()
    for offset in range(9):
        for items in demand.get(day + timedelta(days=offset), []):
            needs.update(dict(items))
    for family in range(10):
        items = []
        for index in range(family * 12, (family + 1) * 12):
            quantity = purchase_quantity(index, needs[index], stocks[index], day)
            if quantity:
                items.append((index, quantity))
        if items:
            events.append(dict(kind="purchase", day=day, order=sequence, items=items, family=family))
            receipts[day + timedelta(days=2)].append((sequence, items))
            sequence += 1
    return sequence


def purchase_quantity(index, demand, stock, day):
    # Intermittent suppliers replenish these references every other week.
    delayed = index % 11 == 0 and (day - START).days % 14 == 0
    if not demand or delayed:
        return 0
    target = demand + (6 if index // 12 in (5, 6, 9) else 12)
    if index % 11 == 0:
        target = int(demand * 0.6)  # limited replenishment availability all period
    return max(0, ((target - stock + 5) // 6) * 6)


def plan_summary(plan):
    counts = Counter(event["kind"] for event in plan["events"])
    lines = lambda kind: sum(len(e["items"]) for e in plan["events"] if e["kind"] == kind)
    notes = delivery_note_schedule(plan)
    note_items = sum(len(note["items"]) for note in notes)
    cancelled_items = sum(len(note["items"]) for note in notes if note["cancel"])
    issued = Counter()
    for note in notes:
        if not note["cancel"]:
            issued.update(dict(note["items"]))
    final = [stock - issued[index] for index, stock in enumerate(plan["stocks"])]
    return dict(categories=10, products=120, product_state_histories=120, suppliers=10, customers=60, users=3,
        sales=counts["sale"], sale_lines=lines("sale"), quotes=40, quote_lines=80,
        orders=40, order_lines=80, purchase_orders=counts["purchase"],
        purchase_order_lines=lines("purchase"), purchase_receptions=counts["receive"],
        purchase_reception_lines=lines("receive"),
        delivery_notes=len(notes), delivery_note_items=note_items,
        stock_movements=120 + lines("sale") + lines("receive") + counts["adjust"] + note_items + cancelled_items,
        adjustments=counts["adjust"], exhausted=sum(s == 0 for s in final),
        low_stock=sum(0 < s <= p["minimum_stock"] for s, p in zip(final, catalog())))


# Notas de entrega sintéticas integradas al plan: 5 emitidas + 2 canceladas.
# Cada nota se fecha después del último movimiento de sus productos para no
# romper la coherencia cronológica de saldos; las cantidades dejan holgura
# sobre el mínimo para no alterar agotados ni bajos de stock del plan.
NOTE_CUSTOMERS = (5, 22, 37, 44, 51, 9, 30)
NOTE_SIZES = (3, 3, 2, 3, 2, 3, 2)
NOTE_CANCELLED = frozenset((5, 6))


def delivery_note_schedule(plan):
    """Deriva deterministamente las notas desde los eventos y saldos del plan."""
    stocks = plan["stocks"]
    minimums = [product["minimum_stock"] for product in catalog()]
    last = {}
    for event in plan["events"]:
        kind = event["kind"]
        if kind in ("sale", "receive"):
            for index, _ in event["items"]:
                last[index] = max(last.get(index, START), event["day"])
        elif kind == "adjust":
            last[event["product"]] = max(last.get(event["product"], START), event["day"])
    horizon = END - timedelta(days=8)
    candidates = [index for index in range(120)
                  if index // 12 != 9 and stocks[index] - minimums[index] >= 4
                  and last.get(index, START) <= horizon]
    candidates.sort(key=lambda index: (-stocks[index], index))
    notes, cursor = [], 0
    for number, size in enumerate(NOTE_SIZES):
        day = END - timedelta(days=6 - number)
        items = []
        for slot in range(size):
            index = candidates[cursor]
            cursor += 1
            headroom = stocks[index] - minimums[index] - 1
            items.append((index, min(1 + (number + slot) % 3, headroom)))
        notes.append(dict(day=day, second=25200 + number * 900, customer=NOTE_CUSTOMERS[number],
                          items=items, cancel=number in NOTE_CANCELLED))
    return notes
