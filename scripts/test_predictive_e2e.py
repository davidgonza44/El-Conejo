"""E2E MariaDB del módulo predictivo. Escribe SOLO en ferreteria_conejo_e2e.

Uso:
    .\\venv312\\Scripts\\python.exe scripts/test_predictive_e2e.py

No lee DB_NAME del .env. No llama migraciones. Limpia únicamente las filas de esta corrida.
"""
from __future__ import annotations

import math
import os
import secrets
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import inspect, text  # noqa: E402

from purchase_e2e_support import DATABASE, engine_for, guard_engine  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Category, Customer, Product, Sale, SaleLine, StockMovement, User  # noqa: E402
from app.models.delivery_note import DeliveryNote  # noqa: E402
from app.models.historical_demand_record import HistoricalDemandRecord, RECORD_STATUS_ISSUED, RECORD_TYPE_SALE  # noqa: E402
from app.models.historical_import import HistoricalImport, IMPORT_STATUS_CONFIRMED  # noqa: E402
from app.models.purchase import PurchaseOrder, PurchaseOrderLine, Supplier  # noqa: E402
from app.models.sale import STATUS_COMPLETED  # noqa: E402
from app.services import backtest_service, critical_product_service as risk  # noqa: E402
from app.services import demand_data_service as demand_src, forecast_service  # noqa: E402
from app.services import predictive_analysis_service, predictive_data_service as data  # noqa: E402
from app.services import predictive_metrics_service as metrics, replenishment_service, trend_service  # noqa: E402
from app.services.demand_analysis_service import institutional_today  # noqa: E402
from app.utils.schema import reset_cache  # noqa: E402

failures: list[str] = []
passed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed
    if ok:
        passed += 1
        print(f"  ok   {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label} {detail}")


def close(a, b, tol=1e-6) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


def make_app():
    engine = engine_for(DATABASE)
    guard_engine(engine)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT DATABASE()")).scalar() == DATABASE
    uri = engine.url.render_as_string(hide_password=False)
    engine.dispose()

    class E2EConfig:
        SECRET_KEY = secrets.token_urlsafe(32)
        DEBUG = True
        TESTING = True
        APP_ENV = "testing"
        MAIL_ENABLED = False
        SQLALCHEMY_DATABASE_URI = uri
        SQLALCHEMY_TRACK_MODIFICATIONS = False

    app = create_app(E2EConfig)
    writes = []
    with app.app_context():
        assert db.engine.url.database == DATABASE
        assert db.session.execute(text("SELECT DATABASE()")).scalar() == DATABASE
        guard_engine(db.engine, writes)
    return app, writes


def tables(app) -> set[str]:
    with app.app_context():
        return set(inspect(db.engine).get_table_names())


def seed(app) -> dict:
    run = secrets.token_hex(4)
    today = institutional_today()
    with app.app_context():
        assert db.session.execute(text("SELECT DATABASE()")).scalar() == DATABASE
        admin = User(name="Predictivo E2E", username=f"px_e2e_{run}", email=f"px_{run}@example.invalid",
                     role="admin")
        admin.set_password("PxE2ePass999")
        db.session.add(admin)
        db.session.flush()
        category = Category(name=f"PX E2E {run}")
        db.session.add(category)
        db.session.flush()
        product = Product(code=f"PX-E2E-{run}-A", name=f"Cemento E2E {run}", category_id=category.id,
                          unit="saco", current_stock=40, minimum_stock=12, purchase_price=Decimal("8.50"),
                          sale_price=10)
        other = Product(code=f"PX-E2E-{run}-B", name=f"Varilla E2E {run}", category_id=category.id,
                        unit="uds.", current_stock=0, minimum_stock=20, purchase_price=Decimal("5"),
                        sale_price=7)
        db.session.add_all([product, other])
        customer = Customer(name=f"Cliente PX {run}", is_walk_in=True)
        db.session.add(customer)
        db.session.flush()

        def sell(target: Product, day: date, qty: int, status: str = STATUS_COMPLETED):
            when = demand_src.commercial_datetime_as_utc_naive(day, 12, 0)
            sale = Sale(customer_id=customer.id, seller_id=admin.id, sold_at=when, payment_method="cash",
                        payment_condition="cash", status=status, subtotal=qty, total=qty)
            db.session.add(sale)
            db.session.flush()
            movement = StockMovement(product_id=target.id, movement_type="exit", quantity=qty,
                                     previous_stock=0, new_stock=0, user_id=admin.id, created_at=when)
            db.session.add(movement)
            db.session.flush()
            db.session.add(SaleLine(sale_id=sale.id, product_id=target.id, stock_movement_id=movement.id,
                                    product_code=target.code, product_name=target.name, quantity=qty,
                                    unit_price=1, line_subtotal=qty, line_total=qty))

        for back in range(80, 0, -1):
            if back in (15, 16):
                continue
            sell(product, today - timedelta(days=back), 2 if back > 10 else 3)
        sell(product, today - timedelta(days=3), 40, "cancelled")
        sell(other, today - timedelta(days=5), 1)

        supplier = None
        if inspect(db.engine).has_table("suppliers"):
            supplier = Supplier(name=f"PX Prov {run}", tax_id=f"PX-{run}", is_active=True)
            db.session.add(supplier)
            db.session.flush()
            pending = PurchaseOrder(supplier_id=supplier.id, created_by_user_id=admin.id, status="approved",
                                    subtotal=10, total=10)
            db.session.add(pending)
            db.session.flush()
            pending.order_number = f"OC-PX-{pending.id}"
            db.session.add(PurchaseOrderLine(purchase_order_id=pending.id, product_id=product.id,
                                             product_code=product.code, product_name=product.name,
                                             quantity_ordered=10, quantity_received=0, unit_cost=product.purchase_price,
                                             line_total=10))
            partial = PurchaseOrder(supplier_id=supplier.id, created_by_user_id=admin.id, status="approved",
                                    subtotal=8, total=8)
            db.session.add(partial)
            db.session.flush()
            partial.order_number = f"OC-PX-{partial.id}"
            db.session.add(PurchaseOrderLine(purchase_order_id=partial.id, product_id=product.id,
                                             product_code=product.code, product_name=product.name,
                                             quantity_ordered=8, quantity_received=3, unit_cost=product.purchase_price,
                                             line_total=8))
            cancelled = PurchaseOrder(supplier_id=supplier.id, created_by_user_id=admin.id, status="cancelled",
                                      subtotal=50, total=50)
            db.session.add(cancelled)
            db.session.flush()
            cancelled.order_number = f"OC-PX-{cancelled.id}"
            db.session.add(PurchaseOrderLine(purchase_order_id=cancelled.id, product_id=product.id,
                                             product_code=product.code, product_name=product.name,
                                             quantity_ordered=50, quantity_received=0, unit_cost=product.purchase_price,
                                             line_total=50))

        hist_id = None
        if inspect(db.engine).has_table("historical_imports"):
            period_end = date(2025, 12, 31)
            hist = HistoricalImport(
                original_filename=f"px-{run}.csv", storage_key=f"px-{run}", file_size_bytes=32,
                sha256=secrets.token_hex(32), source_system="predictive-e2e",
                status=IMPORT_STATUS_CONFIRMED, period_start=date(2025, 1, 1), period_end=period_end,
                created_by_user_id=admin.id, confirmed_by_user_id=admin.id, confirmed_at=datetime.utcnow(),
            )
            db.session.add(hist)
            db.session.flush()
            hist_id = hist.id
            fp = secrets.token_hex(16)
            db.session.add(HistoricalDemandRecord(
                historical_import_id=hist.id, source_row_number=1,
                source_record_id_original="px-1", source_record_id_normalized="PX-1",
                source_line_id_original="l1", source_line_id_normalized="L1",
                document_type="historical_demand", document_number_original="D1",
                document_number_normalized="D1", event_date=period_end,
                product_code_original=product.code, product_code_normalized=product.code,
                product_id=product.id, quantity=Decimal("4"), record_type=RECORD_TYPE_SALE,
                record_status=RECORD_STATUS_ISSUED, effective_status=RECORD_STATUS_ISSUED,
                fingerprint=fp, fingerprint_strength="strong", dedupe_key=fp,
                match_status="matched", include_in_demand=True, raw_row_json={},
            ))
            sell(product, period_end, 9)
            sell(product, date(2026, 1, 1), 6)

        db.session.commit()
        return {"run": run, "admin_id": admin.id, "category_id": category.id, "product_id": product.id,
                "other_id": other.id, "customer_id": customer.id,
                "supplier_id": supplier.id if supplier else None, "hist_id": hist_id, "today": today}


def cleanup(app, ctx: dict) -> None:
    with app.app_context():
        assert db.session.execute(text("SELECT DATABASE()")).scalar() == DATABASE
        uid, pid, oid = ctx["admin_id"], ctx["product_id"], ctx["other_id"]
        statements = [
            "DELETE FROM sale_lines WHERE product_id IN (:p, :o)",
            "DELETE FROM sales WHERE seller_id=:u OR customer_id=:c",
            "DELETE FROM stock_movements WHERE product_id IN (:p, :o) OR user_id=:u",
        ]
        if inspect(db.engine).has_table("purchase_reception_lines"):
            statements.append(
                "DELETE FROM purchase_reception_lines WHERE reception_id IN "
                "(SELECT id FROM purchase_receptions WHERE received_by_user_id=:u)"
            )
            statements.append("DELETE FROM purchase_receptions WHERE received_by_user_id=:u")
        if inspect(db.engine).has_table("purchase_order_lines"):
            statements.append(
                "DELETE FROM purchase_order_lines WHERE purchase_order_id IN "
                "(SELECT id FROM purchase_orders WHERE created_by_user_id=:u)"
            )
            statements.append("DELETE FROM purchase_orders WHERE created_by_user_id=:u")
        if inspect(db.engine).has_table("notification_deliveries") and inspect(db.engine).has_table("notifications"):
            statements.append("DELETE FROM notification_deliveries WHERE notification_id IN "
                              "(SELECT id FROM notifications WHERE user_id=:u)")
        if inspect(db.engine).has_table("notifications"):
            statements.append("DELETE FROM notifications WHERE user_id=:u")
        if inspect(db.engine).has_table("login_events"):
            statements.append("DELETE FROM login_events WHERE user_id=:u")
        if inspect(db.engine).has_table("historical_demand_records") and ctx.get("hist_id"):
            statements.append("DELETE FROM historical_demand_records WHERE import_id=:h")
            statements.append("DELETE FROM historical_imports WHERE id=:h")
        statements.extend([
            "DELETE FROM products WHERE id IN (:p, :o)",
            "DELETE FROM categories WHERE id=:cat",
            "DELETE FROM customers WHERE id=:c",
            "DELETE FROM user_sessions WHERE user_id=:u",
            "DELETE FROM users WHERE id=:u",
        ])
        if ctx.get("supplier_id"):
            statements.append("DELETE FROM suppliers WHERE id=:s")
        params = {"u": uid, "p": pid, "o": oid, "c": ctx["customer_id"], "cat": ctx["category_id"],
                  "h": ctx.get("hist_id") or 0, "s": ctx.get("supplier_id") or 0}
        for statement in statements:
            db.session.execute(text(statement), params)
        db.session.commit()


def run(app, ctx: dict, writes: list) -> None:
    print("\n[mariadb e2e]")
    with app.app_context():
        assert db.session.execute(text("SELECT DATABASE()")).scalar() == DATABASE
        reset_cache()
        check("DATABASE() es ferreteria_conejo_e2e", True)
        check("venta no origina nota (sin sale_id)", "sale_id" not in DeliveryNote.__table__.c)
        check("seed no creó notas de entrega", DeliveryNote.query.filter(
            DeliveryNote.customer_name.like(f"%{ctx['run']}%")).count() == 0)

        series_map = data.load_series(data.GRANULARITY_DAILY, ctx["today"])
        series = series_map.get(ctx["product_id"])
        check("serie generada para el producto E2E", series is not None and series.periods > 0)
        check("períodos en cero se conservan", series is not None and any(v == 0 for v in series.values))
        if ctx.get("hist_id"):
            cutoff = demand_src.TemporalCutoff(True, 1, date(2025, 1, 1), date(2025, 12, 31), date(2026, 1, 1))
            check("frontera: period_end 2025-12-31, operativa 2026-01-01",
                  cutoff.historical_period_end == date(2025, 12, 31)
                  and cutoff.operational_starts_on == date(2026, 1, 1))
            events = [e for e in demand_src.load_demand_events(cutoff) if e.product_id == ctx["product_id"]]
            historical = [e for e in events if e.source == demand_src.SOURCE_HISTORICAL]
            operational = [e for e in events if e.source == demand_src.SOURCE_OPERATIONAL]
            check("period_end histórico entra y no se solapa con operativa",
                  any(e.event_date == date(2025, 12, 31) for e in historical)
                  and not ({e.event_date for e in historical} & {e.event_date for e in operational}))
            check("venta 2026-01-01 es operativa",
                  any(e.event_date == date(2026, 1, 1) and e.source == demand_src.SOURCE_OPERATIONAL for e in events))
            check("venta del period_end Caracas no es operativa",
                  not any(e.event_date == date(2025, 12, 31) and e.source == demand_src.SOURCE_OPERATIONAL
                          for e in events))
            end_qty = sum((e.signed_quantity for e in events if e.event_date == date(2025, 12, 31)), Decimal("0"))
            check("precedencia: period_end = 4 histórico, no 4+9 de la venta", end_qty == Decimal("4"))
        else:
            check("tablas históricas ausentes: frontera omitida con honestidad", True)

        fc = forecast_service.forecast(series, 30, forecast_service.BASELINE_MODEL)
        check("forecast 30 días no negativo y horizonte real", fc.total > 0 and len(fc.path) == 30)
        bt = backtest_service.rolling_backtest(series, forecast_service.BASELINE_MODEL, 7, 30)
        check("rolling backtest con pliegues y sin fuga",
              bt.folds and all(fold.predicted == forecast_service.predict(series.values[:fold.origin], 7,
                                                                          forecast_service.BASELINE_MODEL,
                                                                          series.granularity) for fold in bt.folds))
        scored = metrics.compute(bt.actual, bt.predicted)
        check("métricas finitas o None",
              scored.mae is not None and math.isfinite(scored.mae)
              and (scored.wape is None or math.isfinite(scored.wape)))
        check("WAPE denominador 0 -> None", metrics.wape([0, 0], [1, 2]) is None)
        zeros = data.aggregate_daily(1, [(date(2026, 1, 1) + timedelta(days=i), 0) for i in range(90)],
                                     data.GRANULARITY_DAILY)
        auto = backtest_service.evaluate_models(zeros, 7, 30)
        check("auto no elige WAPE None como mejor",
              auto is not None and auto.best == forecast_service.BASELINE_MODEL
              and all(r.metrics.wape is None for r in auto.results.values()))

        item = predictive_analysis_service.analyze_product(
            db.session.get(Product, ctx["product_id"]), series, 30, forecast_service.AUTO_MODEL, ctx["today"], 0)
        check("tendencia calculada sobre demanda observada",
              item.trend is not None and item.trend.status in trend_service.TREND_LABELS)
        check("criticidad explicable", item.risk.reasons and item.risk.level in risk.RISK_ORDER)
        rec = replenishment_service.compute(40, 12, fc.daily_rate, 0, Decimal("8.50"), ctx["today"])
        check("lead time supuesto = DEFAULT_LEAD_TIME_DAYS",
              rec.lead_time_source == replenishment_service.LEAD_TIME_ASSUMED
              and rec.lead_time_days == replenishment_service.DEFAULT_LEAD_TIME_DAYS)
        check("minimum_stock es umbral de reposición", rec.safety_stock == 12)

        if ctx.get("supplier_id"):
            pending = replenishment_service.pending_supply_by_product()
            check("pending + parcial (10 + 5) y cancelada excluida",
                  pending.get(ctx["product_id"]) == 15)
            reduced = replenishment_service.compute(40, 12, fc.daily_rate, pending[ctx["product_id"]],
                                                    Decimal("8.50"), ctx["today"])
            check("supply pendiente reduce la necesidad neta",
                  reduced.net_requirement < rec.net_requirement)
            check("el análisis no crea una orden de compra",
                  PurchaseOrder.query.filter_by(created_by_user_id=ctx["admin_id"]).count() == 3)
            check("el supply aprobado no cambia sin una OC nueva",
                  replenishment_service.pending_supply_by_product().get(ctx["product_id"]) == 15)
        else:
            check("tablas de compras ausentes: PO omitida con honestidad", True)

    client = app.test_client()
    r = client.post("/api/auth/login", json={"identifier": f"px_e2e_{ctx['run']}", "password": "PxE2ePass999"})
    check("login E2E", r.status_code == 200, str(getattr(r, "status_code", r)))
    writes_before_detail = len(writes)
    response = client.get(f"/api/predictions/products/{ctx['product_id']}/detail?status=out_of_stock&search=missing")
    detail = response.get_json()
    check("drawer ID independiente de filtros", response.status_code == 200 and detail["product"]["id"] == ctx["product_id"])
    check("drawer demanda real y forecast", detail["history"]["total"] == 166 and detail["periods"][1]["total"] > 0)
    check("drawer supply de OC aprobadas sin canceladas", detail["pending"] == 15 and sum(o["pending"] for o in detail["orders"]) == 15)
    check("drawer movimientos reales sin documento inventado", bool(detail["movements"]) and detail["movements"][0]["document"] is None)
    check("drawer no requiere compra cubierta", detail["recommendation"]["suggested_quantity"] == 0 and not detail["can_order"])
    reference = client.get("/api/predictions/products/0/detail?ref=1&sku=CEM-050")
    check("drawer ref fixture sin writes", reference.status_code == 200 and reference.json["reference"] and len(writes) == writes_before_detail)
    from unittest.mock import patch
    app.debug = True
    with app.app_context():
        before = {name: db.session.query(model).count()
                  for name, model in (("sales", Sale), ("products", Product), ("notes", DeliveryNote))}
    with patch.object(db.session, "commit", wraps=db.session.commit) as mocked:
        page = client.get("/predictive/demand?ref=1")
        check("ref=1 E2E 200 con fixtures", page.status_code == 200 and b"3,245" in page.data)
        check("ref=1 no commitea", mocked.call_count == 0, str(mocked.call_count))
    with app.app_context():
        after = {name: db.session.query(model).count()
                 for name, model in (("sales", Sale), ("products", Product), ("notes", DeliveryNote))}
    check("ref=1 no incrementa ventas/productos/notas", before == after)


def main() -> int:
    app, writes = make_app()
    present = tables(app)
    needed = {"users", "products", "sales", "sale_lines", "customers", "stock_movements"}
    missing = needed - present
    if missing:
        print("FAIL tablas operativas ausentes en E2E:", sorted(missing))
        return 1
    ctx = seed(app)
    try:
        run(app, ctx, writes)
    finally:
        cleanup(app, ctx)
        with app.app_context():
            leftover = Product.query.filter(Product.code.like(f"PX-E2E-{ctx['run']}-%")).count()
            check("cleanup: 0 productos de esta corrida", leftover == 0)
            db.engine.dispose()
    print(f"\n{passed} ok, {len(failures)} fallos")
    for name in failures:
        print(f"  - {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
