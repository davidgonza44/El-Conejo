"""Deterministic historical fixtures for Products KPIs. Writes ONLY ferreteria_conejo_e2e.

Creates synthetic categories "E2E-HIST ..." and products E2E-HIST-001..009 plus one
inventory_snapshots row per product per day from SEED_START through yesterday
(America/Caracas). The snapshots are test fixtures: NO StockMovement rows are created
and nothing here says anything about the valuable database's history.

Safety: hard-coded E2E URI (never .env), refuses any other --database value, verifies
SELECT DATABASE() at runtime, deletes only rows it owns. Idempotent: rerun => same rows.
    python -B scripts/seed_product_history_e2e.py --database ferreteria_conejo_e2e
    python -B scripts/seed_product_history_e2e.py --database ferreteria_conejo_e2e --cleanup
"""
import argparse
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.models import Category, InventorySnapshot, Product, StockMovement
from app.services.inventory_snapshot_service import commercial_today
from serve_inventory_e2e import E2EConfig

DATABASE = "ferreteria_conejo_e2e"
SEED_START = date(2025, 7, 1)
CATEGORIES = {"A": "E2E-HIST Categoría A", "B": "E2E-HIST Categoría B", "C": "E2E-HIST Categoría C"}
CATEGORY_INACTIVE_FROM = {"C": date(2026, 9, 5)}
# code: (name, [(from_date, stock, minimum_stock, is_active, category_key), ...]); a segment lasts until the next one.
PRODUCTS = {
    "E2E-HIST-001": ("E2E-HIST Sin cambios", [(SEED_START, 50, 10, True, "A")]),
    "E2E-HIST-002": ("E2E-HIST Disponible a bajo stock", [(SEED_START, 40, 10, True, "A"), (date(2026, 9, 5), 8, 10, True, "A")]),
    "E2E-HIST-003": ("E2E-HIST Bajo stock a sin stock", [(SEED_START, 5, 10, True, "A"), (date(2026, 8, 16), 0, 10, True, "A")]),
    "E2E-HIST-004": ("E2E-HIST Sin stock a disponible",
                     [(SEED_START, 30, 5, True, "B"), (date(2026, 1, 1), 0, 5, True, "B"), (date(2026, 9, 1), 30, 5, True, "B")]),
    "E2E-HIST-005": ("E2E-HIST Creado en el período", [(date(2026, 9, 6), 20, 5, True, "B")]),
    "E2E-HIST-006": ("E2E-HIST Desactivado y reactivado",
                     [(SEED_START, 15, 5, True, "B"), (date(2026, 8, 21), 15, 5, False, "B"), (date(2026, 9, 8), 15, 5, True, "B")]),
    "E2E-HIST-007": ("E2E-HIST Stock mínimo modificado", [(SEED_START, 12, 5, True, "A"), (date(2026, 9, 1), 12, 20, True, "A")]),
    "E2E-HIST-008": ("E2E-HIST Cambio de categoría", [(SEED_START, 25, 5, True, "C"), (date(2026, 9, 1), 25, 5, True, "A")]),
    "E2E-HIST-009": ("E2E-HIST Desactivado", [(SEED_START, 3, 10, True, "B"), (date(2026, 6, 1), 3, 10, False, "B")]),
}
# Hand-designed end-of-period states (snapshot rows only; live "today" also includes other E2E products).
EXPECTED = {
    date(2025, 8, 31): {"total_products": 8, "low_stock": 2, "out_of_stock": 0, "active_categories": 3},
    date(2026, 7, 31): {"total_products": 7, "low_stock": 1, "out_of_stock": 1, "active_categories": 3},
    date(2026, 8, 31): {"total_products": 6, "low_stock": 0, "out_of_stock": 2, "active_categories": 3},
    date(2026, 9, 9): {"total_products": 8, "low_stock": 2, "out_of_stock": 1, "active_categories": 2},
}
# Windows end on or before 2026-09-09: the production job only ever writes "yesterday" (>= 2026-09-10),
# so these closes contain E2E-HIST rows only and stay exact no matter what else the disposable DB holds.
WINDOWS = {
    "A": {"start": "2026-09-01", "end": "2026-09-09", "compare": "previous", "current": date(2026, 9, 9), "previous": date(2026, 8, 31),
          "comparison": ("2026-08-23", "2026-08-31")},
    "B": {"start": "2026-08-01", "end": "2026-08-31", "compare": "previous", "current": date(2026, 8, 31), "previous": date(2026, 7, 31),
          "comparison": ("2026-07-01", "2026-07-31")},
    "YEAR": {"start": "2026-08-01", "end": "2026-08-31", "compare": "year", "current": date(2026, 8, 31), "previous": date(2025, 8, 31),
             "comparison": ("2025-08-01", "2025-08-31")},
}


def state_at(code, day):
    """(stock, minimum, active, category_key) or None when the product does not exist yet."""
    current = None
    for segment in PRODUCTS[code][1]:
        if segment[0] <= day:
            current = segment[1:]
    return current


def category_active_at(key, day):
    return day < CATEGORY_INACTIVE_FROM.get(key, date.max)


def kpis_at(day):
    """Same rule as product_service.kpi_counts, evaluated on the fixture state."""
    states = [state for code in PRODUCTS if (state := state_at(code, day)) and state[2]]
    return {
        "total_products": len(states),
        "low_stock": sum(1 for stock, minimum, _, _ in states if 0 < stock <= minimum),
        "out_of_stock": sum(1 for stock, _, _, _ in states if stock == 0),
        "active_categories": len({category for _, _, _, category in states}),
    }


def make_app():
    if E2EConfig.SQLALCHEMY_DATABASE_URI.rsplit("/", 1)[1].split("?")[0] != DATABASE:
        raise SystemExit("REFUSED: the harness URI does not point to " + DATABASE)
    app = create_app(E2EConfig)
    with app.app_context():
        if db.session.execute(text("SELECT DATABASE()")).scalar() != DATABASE:
            raise SystemExit("REFUSED: connected database is not " + DATABASE)
    return app


def own_rows():
    categories = Category.query.filter(Category.name.in_(CATEGORIES.values())).all()
    products = Product.query.filter(Product.code.in_(PRODUCTS.keys())).all()
    return categories, products


def seed(today):
    yesterday = today - timedelta(days=1)
    categories = {category.name: category for category in Category.query.filter(Category.name.in_(CATEGORIES.values()))}
    for key, name in CATEGORIES.items():
        category = categories.get(name) or Category(name=name)
        category.description = "Fixture histórico E2E; no es dato real."
        category.is_active = category_active_at(key, today)
        db.session.add(category)
        categories[name] = category
    db.session.flush()
    products = {product.code: product for product in Product.query.filter(Product.code.in_(PRODUCTS.keys()))}
    for code, (name, segments) in PRODUCTS.items():
        stock, minimum, active, category = state_at(code, today) or segments[0][1:]
        product = products.get(code) or Product(code=code)
        product.name, product.unit = name, "unidad"
        product.description = "Fixture histórico E2E; stock asignado directamente, sin movimientos."
        product.current_stock, product.minimum_stock, product.is_active = stock, minimum, active
        product.category_id = categories[CATEGORIES[category]].id
        product.purchase_price, product.sale_price = 1, 2
        product.created_at = datetime.combine(segments[0][0], time(12))
        db.session.add(product)
        products[code] = product
    db.session.flush()
    ids = [product.id for product in products.values()]
    InventorySnapshot.query.filter(InventorySnapshot.product_id.in_(ids)).delete(synchronize_session=False)
    rows, taken_at = [], datetime.utcnow()
    for offset in range((yesterday - SEED_START).days + 1):
        day = SEED_START + timedelta(days=offset)
        for code, product in products.items():
            state = state_at(code, day)
            if state is None:
                continue
            stock, minimum, active, category = state
            rows.append(InventorySnapshot(snapshot_date=day, product_id=product.id, stock=stock, minimum_stock=minimum,
                                          product_is_active=active, category_id=categories[CATEGORIES[category]].id,
                                          category_is_active=category_active_at(category, day), created_at=taken_at))
    db.session.bulk_save_objects(rows)
    db.session.commit()
    return len(rows), ids


def cleanup():
    categories, products = own_rows()
    ids = [product.id for product in products]
    snapshots = InventorySnapshot.query.filter(InventorySnapshot.product_id.in_(ids)).delete(synchronize_session=False) if ids else 0
    movements = StockMovement.query.filter(StockMovement.product_id.in_(ids)).delete(synchronize_session=False) if ids else 0
    for product in products:
        db.session.delete(product)
    db.session.flush()
    kept = []
    for category in categories:
        if Product.query.filter_by(category_id=category.id).count():
            kept.append(category.name)
        else:
            db.session.delete(category)
    db.session.commit()
    return {"snapshots": snapshots, "movements": movements, "products": len(ids), "categories_kept": kept}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True)
    parser.add_argument("--cleanup", action="store_true", help="remove only the rows this script created")
    args = parser.parse_args()
    if args.database != DATABASE:
        raise SystemExit(f"REFUSED: this seed only writes {DATABASE}; '{args.database}' is not allowed.")
    for day, expected in EXPECTED.items():
        assert kpis_at(day) == expected, (day, kpis_at(day), expected)
    app = make_app()
    with app.app_context():
        if args.cleanup:
            print("[OK] cleanup", cleanup())
            return
        today = commercial_today()
        count, ids = seed(today)
        print(f"[OK] {DATABASE}: {len(ids)} productos E2E-HIST, {count} cierres diarios {SEED_START} .. {today - timedelta(days=1)}."
              " Sin StockMovement sintéticos.")


if __name__ == "__main__":
    main()
