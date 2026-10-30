"""Before/after acceptance of the browser import, only in the disposable lab."""
import argparse
import csv
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from flask_login import login_user
from sqlalchemy import select

from app import create_app
from app.extensions import db
from app.models import Category, Product, StockMovement, User
from app.models.real_2026 import Real2026Daily, Real2026Import, Real2026Product
from app.services import inventory_service, real_2026_import_service as imports
from app.services import real_2026_source_service as source
from docker_support.real2026_lab import validate_lab
from scripts.migrate_real_2026_history import validate_isolated_mariadb


OPERATIONAL_TABLES = (
    "products", "categories", "stock_movements", "delivery_notes", "delivery_note_items",
    "customers", "sales", "sale_lines", "quotes", "quote_lines", "orders", "order_lines",
    "purchase_orders", "purchase_order_lines", "purchase_receptions", "purchase_reception_lines",
    "inventory_snapshots", "historical_imports", "historical_demand_records", "historical_import_errors")
FIXTURE_CODE = "LAB-STOCK-2026"


def snapshot():
    values = {}
    for name in OPERATIONAL_TABLES:
        table = db.metadata.tables[name]
        rows = [json.dumps(dict(row), sort_keys=True, default=str)
                for row in db.session.execute(table.select()).mappings()]
        values[name] = dict(count=len(rows), sha256=hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest())
    db.session.rollback()
    return values


def _target(app):
    base = Path(app.instance_path).resolve()
    target = base / "real2026_lab_before.json"
    if base.is_symlink() or target.is_symlink():
        raise RuntimeError("El respaldo de comprobación debe permanecer en el almacenamiento privado del laboratorio.")
    return target


def begin(app):
    target = _target(app)
    if target.exists() or Product.query.filter_by(code=FIXTURE_CODE).first() is not None:
        raise RuntimeError("La comprobación inicial ya existe; no se sobrescribe el trabajo anterior.")
    if Real2026Import.query.count() or Real2026Product.query.count() or Real2026Daily.query.count():
        raise RuntimeError("La comprobación inicial requiere un histórico 2026 vacío.")
    user = User.query.filter_by(role="admin").first()
    if user is None:
        raise RuntimeError("Falta el administrador temporal del laboratorio.")
    with app.test_request_context():
        login_user(user)
        category = Category(name="Prueba sintética de aislamiento 2026")
        db.session.add(category)
        db.session.flush()
        product = Product(code=FIXTURE_CODE, name="Prueba sintética de stock aislado",
                          category_id=category.id, unit="unidad", current_stock=0, minimum_stock=0,
                          purchase_price=1, sale_price=2, purchase_price_currency="USD",
                          sale_price_currency="USD", is_active=True)
        db.session.add(product)
        db.session.flush()
        inventory_service.register_entry(dict(product_id=product.id, quantity=777,
            reason="Fixture sintético exclusivo del laboratorio 2026"), commit=False, creation_origin="initial_stock")
        db.session.commit()
    state = dict(environment_id=os.environ["ENVIRONMENT_ID"], tables=snapshot())
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as stream:
        json.dump(state, stream, sort_keys=True)
    return dict(result="PASS", stage="begin", operational_fixture_stock=777,
                operational_fixture_movements=1, tables_snapshotted=len(OPERATIONAL_TABLES))


def _exact_series(batch):
    imports._verify_archive(batch)
    folder = imports._directory(batch.public_id)
    expected = {}
    for filename in source.SERIES_FILES.values():
        with (folder / filename).open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                key = row["codigo_producto"], row["fecha_real"]
                if key in expected:
                    raise RuntimeError("Las fuentes canónicas contienen una clave diaria repetida.")
                expected[key] = Decimal(row["demanda_observada"])
    count = len(expected)
    if count != 328374:
        raise RuntimeError("El paquete canónico no coincide con la cantidad auditada de observaciones.")
    statement = (select(Real2026Product.code, Real2026Daily.event_date, Real2026Daily.quantity)
                 .join(Real2026Daily, Real2026Product.id == Real2026Daily.product_id)
                 .where(Real2026Product.batch_id == batch.id)
                 .order_by(Real2026Product.code, Real2026Daily.event_date))
    result = db.session.execute(statement.execution_options(stream_results=True, max_row_buffer=1000))
    observed = 0
    for code, day, quantity in result:
        if expected.pop((code, day.isoformat()), None) != quantity:
            raise RuntimeError("La base no conserva exactamente las fechas, códigos y cantidades canónicas.")
        observed += 1
    if expected or observed != count:
        raise RuntimeError("La base contiene observaciones faltantes o adicionales.")
    return observed


def finish(app):
    with _target(app).open(encoding="utf-8") as stream:
        before = json.load(stream)
    if before.get("environment_id") != os.environ["ENVIRONMENT_ID"]:
        raise RuntimeError("La comprobación pertenece a otro laboratorio.")
    if snapshot() != before.get("tables"):
        raise RuntimeError("La importación modificó tablas operativas o el histórico de 2025.")
    batch = Real2026Import.query.filter_by(status="confirmed").one()
    if (Real2026Import.query.count() != 1 or not batch.provenance.get("reference_match")
            or Real2026Product.query.count() != 2539 or Real2026Daily.query.count() != 328374):
        raise RuntimeError("La base no contiene exclusivamente el paquete real de referencia confirmado.")
    observed = _exact_series(batch)
    stock = Product.query.filter_by(code=FIXTURE_CODE).one()
    movements = StockMovement.query.filter_by(product_id=stock.id).count()
    if stock.current_stock != 777 or movements != 1:
        raise RuntimeError("La importación alteró el fixture operativo de control.")
    return dict(result="PASS", stage="finish", products=2539, daily_observations=observed,
                all_daily_codes_dates_decimal_quantities_exact=True,
                coverage_start=batch.coverage_start.isoformat(), coverage_end=batch.coverage_end.isoformat(),
                operational_and_2025_tables_unchanged=True, operational_fixture_stock=stock.current_stock,
                operational_fixture_movements=movements)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("begin", "finish"))
    args = parser.parse_args()
    validate_lab()  # Validate the private destination before opening any connection.
    app = create_app()
    with app.app_context():
        with db.engine.connect() as connection:
            validate_isolated_mariadb(connection)
        result = {"begin": begin, "finish": finish}[args.action](app)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
