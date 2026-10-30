"""Pruebas del módulo Análisis predictivo sobre SQLite aislado y desechable (nunca la BD real).

Uso:
    .\\venv312\\Scripts\\python.exe scripts/test_predictive_module.py           # suite
    .\\venv312\\Scripts\\python.exe scripts/test_predictive_module.py --serve   # servidor 127.0.0.1:5038 para capturas

Cubre: ref=1 sin escrituras, LIVE sin fixtures, estados sin datos, períodos en cero, ausencia de fuga
temporal, MAE/RMSE/Bias/WAPE/MAPE, tendencias, cobertura sin división por cero, riesgo explicable,
supply de órdenes aprobadas, solicitud de reposición solo con confirmación humana, filtros, paginación,
exportación, permisos y zona horaria America/Caracas.
"""
from __future__ import annotations

import math
import re
import sys
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_settings_core import ADMIN_PASSWORD, IsolatedConfig, login  # noqa: E402
from test_settings_core import setup_app as _setup_app  # noqa: E402

from app.extensions import db  # noqa: E402
from app.models import Category, Customer, Product, Sale, SaleLine, StockMovement  # noqa: E402
from app.models.delivery_note import DeliveryNote  # noqa: E402
from app.models.historical_demand_record import HistoricalDemandRecord, RECORD_STATUS_ISSUED, RECORD_TYPE_SALE  # noqa: E402
from app.models.historical_import import HistoricalImport, IMPORT_STATUS_CONFIRMED  # noqa: E402
from app.models.purchase import PurchaseOrder, PurchaseOrderLine, Supplier  # noqa: E402
from app.models.replenishment_request import ReplenishmentRequest, ReplenishmentRequestLine  # noqa: E402
from app.models.sale import STATUS_COMPLETED  # noqa: E402
from app.services import backtest_service, critical_product_service as risk  # noqa: E402
from app.services import forecast_service, predictive_data_service as data  # noqa: E402
from app.services import predictive_metrics_service as metrics, replenishment_service, trend_service  # noqa: E402
from app.services import demand_data_service as demand_src  # noqa: E402
from app.services.demand_data_service import commercial_date  # noqa: E402
from app.services.inventory_service import COMMERCIAL_ZONE  # noqa: E402

PAGES = ("/predictive/demand", "/predictive/replenishment", "/predictive/critical", "/predictive/trends",
         "/predictive/accuracy")
# Valores que SOLO existen en las fixtures de referencia; jamás deben aparecer en LIVE.
FIXTURE_MARKERS = (b"3,245", b"Prophet", b"92.4%", b"1,248", b"87.4%", b"$ 18,450.00", b"Temporada de construcci")
WRITE_TABLES = (Product, Sale, SaleLine, StockMovement, PurchaseOrder, PurchaseOrderLine,
                ReplenishmentRequest, ReplenishmentRequestLine)

failures: list[str] = []
passed = 0


def setup_app(temp_root: Path):
    """App aislada con el RBAC real. Sin semilla, las tablas vacías niegan todos los permisos."""
    app, ids = _setup_app(temp_root)
    with app.app_context():
        from app.services import rbac_service
        rbac_service.ensure_seeded()
    return app, ids


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


# ---------------------------------------------------------------------------
# Semilla determinista
# ---------------------------------------------------------------------------
def _today() -> date:
    return datetime.now(COMMERCIAL_ZONE).date()


def _utc_noon(day: date) -> datetime:
    """Mediodía comercial expresado como UTC naive (como guarda la BD)."""
    return datetime.combine(day, time(12, 0), tzinfo=COMMERCIAL_ZONE).astimezone(timezone.utc).replace(tzinfo=None)


def seed(app, admin_id: int) -> dict:
    today = _today()
    with app.app_context():
        cats = {name: Category(name=name) for name in ("Construcción", "Pinturas", "Ferretería")}
        db.session.add_all(cats.values())
        db.session.flush()
        products = {
            "CEM": Product(code="CEM-050", name="Cemento Portland 50 kg", category_id=cats["Construcción"].id,
                           unit="saco", current_stock=48, minimum_stock=20, purchase_price=Decimal("8.50"), sale_price=10),
            "PIN": Product(code="PIN-18L", name="Pintura Blanca 18 L", category_id=cats["Pinturas"].id,
                           current_stock=3, minimum_stock=10, purchase_price=Decimal("30.00"), sale_price=40),
            "VAR": Product(code="VAR-12", name="Varilla Corrugada 12 mm", category_id=cats["Construcción"].id,
                           current_stock=0, minimum_stock=50, purchase_price=Decimal("5.00"), sale_price=7),
            "TOR": Product(code="TOR-61", name="Tornillo Drywall 6 x 1", category_id=cats["Ferretería"].id,
                           current_stock=500, minimum_stock=100, purchase_price=Decimal("0.05"), sale_price=0.1),
            "TAL": Product(code="TAL-750", name="Taladro Percutor 750W", category_id=cats["Ferretería"].id,
                           current_stock=5, minimum_stock=8, purchase_price=Decimal("120.00"), sale_price=150),
            "CIN": Product(code="CIN-5M", name="Cinta Métrica 5 m", category_id=cats["Ferretería"].id,
                           current_stock=20, minimum_stock=5, purchase_price=Decimal("2.00"), sale_price=3, is_active=False),
        }
        db.session.add_all(products.values())
        customer = Customer(name="Cliente mostrador", is_walk_in=True)
        db.session.add(customer)
        db.session.flush()

        def sell(product: Product, day: date, quantity: int, status: str = STATUS_COMPLETED):
            when = _utc_noon(day)
            sale = Sale(customer_id=customer.id, seller_id=admin_id, sold_at=when, payment_method="cash",
                        payment_condition="cash", status=status, subtotal=quantity, total=quantity)
            db.session.add(sale)
            db.session.flush()
            movement = StockMovement(product_id=product.id, movement_type="exit", quantity=quantity,
                                     previous_stock=0, new_stock=0, user_id=admin_id, created_at=when)
            db.session.add(movement)
            db.session.flush()
            db.session.add(SaleLine(sale_id=sale.id, product_id=product.id, stock_movement_id=movement.id,
                                    product_code=product.code, product_name=product.name, quantity=quantity,
                                    unit_price=1, line_subtotal=quantity, line_total=quantity))

        for back in range(120, 0, -1):
            day = today - timedelta(days=back)
            sell(products["CEM"], day, 3)                              # estable: 3/día
            sell(products["PIN"], day, 1 if back > 30 else 2)          # al alza: 1/día -> 2/día (últimos 30)
            if back % 10 == 0:
                sell(products["VAR"], day, 10)                         # intermitente (90% ceros)
            if back <= 10:
                sell(products["TAL"], day, 1)                          # historial corto
            if back % 3 == 0:
                sell(products["CIN"], day, 1)                          # producto inactivo
        sell(products["CEM"], today - timedelta(days=1), 500, "cancelled")  # solo cuentan ventas completadas

        suppliers = [Supplier(name="Distribuidora Andina", tax_id="J-1"), Supplier(name="Ferre Import", tax_id="J-2")]
        db.session.add_all(suppliers)
        db.session.flush()

        def order(supplier: Supplier, product: Product, ordered: int, received: int, status: str):
            po = PurchaseOrder(supplier_id=supplier.id, created_by_user_id=admin_id, status=status, currency="USD",
                               subtotal=ordered, total=ordered)
            db.session.add(po)
            db.session.flush()
            po.order_number = f"OC-{po.id:06d}"
            db.session.add(PurchaseOrderLine(purchase_order_id=po.id, product_id=product.id, product_code=product.code,
                                             product_name=product.name, quantity_ordered=ordered,
                                             quantity_received=received, unit_cost=product.purchase_price,
                                             line_total=ordered))

        order(suppliers[0], products["CEM"], 100, 0, "approved")     # supply 100
        order(suppliers[0], products["PIN"], 50, 20, "approved")     # supply 30
        order(suppliers[1], products["VAR"], 500, 0, "cancelled")    # supply 0
        db.session.commit()
        return {key: p.id for key, p in products.items()} | {"supplier": suppliers[0].id, "customer": customer.id,
                                                              "category_pinturas": cats["Pinturas"].id}


# ---------------------------------------------------------------------------
# Pruebas unitarias de servicios (sin BD)
# ---------------------------------------------------------------------------
def test_metrics() -> None:
    print("\n[metricas]")
    actual, predicted = [10, 20, 30, 40], [12, 18, 33, 36]
    check("MAE", close(metrics.mae(actual, predicted), 2.75))
    check("RMSE", close(metrics.rmse(actual, predicted), math.sqrt((4 + 4 + 9 + 16) / 4)))
    check("Bias (pred - real)", close(metrics.bias(actual, predicted), -0.25))
    check("WAPE (%)", close(metrics.wape(actual, predicted), 11 / 100 * 100))
    check("WAPE None si la demanda real total es 0", metrics.wape([0, 0], [1, 2]) is None)
    zero_wape = metrics.wape([0, 0], [1, 2])
    check("WAPE None no es 0 ni inf ni NaN", zero_wape is None and zero_wape != 0
          and zero_wape is not math.inf and not (isinstance(zero_wape, float) and math.isnan(zero_wape)))
    value, used, skipped = metrics.mape([0, 10, 20], [5, 11, 18])
    check("MAPE omite actual = 0", used == 2 and skipped == 1 and close(value, 10.0))
    value, used, skipped = metrics.mape([0, 0], [1, 2])
    check("MAPE None si todos los reales son 0", value is None and skipped == 2)
    check("métricas vacías -> None", metrics.mae([], []) is None and metrics.compute([], []).wape is None)
    check("exactitud aproximada = 100 - WAPE acotada", close(metrics.approximate_accuracy(20.0), 80.0)
          and metrics.approximate_accuracy(150.0) == 0.0 and metrics.approximate_accuracy(None) is None)


def test_series_and_eligibility() -> None:
    print("\n[series y elegibilidad]")
    d0 = date(2026, 1, 1)
    series = data.aggregate_daily(1, [(d0, 5), (d0 + timedelta(days=3), 2)], data.GRANULARITY_DAILY,
                                  end=d0 + timedelta(days=5))
    check("períodos sin venta se rellenan con 0 (no se eliminan)", series.values == [5, 0, 0, 2, 0, 0])
    weekly = data.aggregate_daily(1, [(date(2026, 1, 7), 5), (date(2026, 1, 8), 2), (date(2026, 1, 20), 1)],
                                  data.GRANULARITY_WEEKLY)
    check("agregación semanal (lunes ISO) con semana intermedia en 0",
          weekly.starts[0] == date(2026, 1, 5) and weekly.values == [7, 0, 1])
    empty = data.aggregate_daily(1, [], data.GRANULARITY_DAILY, end=d0)
    check("sin eventos -> serie de ceros hasta hoy", empty.values == [0.0])
    check("granularidad inválida rechazada",
          _raises(lambda: data.aggregate_daily(1, [], "monthly"), ValueError))

    rows = [
        {"Fecha": "01/02/2026", "Codigo": "A-1", "Cant": "3"},
        {"Fecha": "01/02/2026", "Codigo": "A-1", "Cant": "3"},                     # duplicado exacto
        {"Fecha": "02/02/2026", "Codigo": "A-1", "Cant": "x"},                     # cantidad inválida
        {"Fecha": "", "Codigo": "A-1", "Cant": "1"},                               # sin fecha
        {"Fecha": "03/02/2026", "Codigo": "ZZZ", "Cant": "1"},                     # producto desconocido
        {"Fecha": "03/02/2026", "Codigo": "A-1", "Cant": "9", "Estado": "anulada"},  # cancelada
        {"Fecha": "04/02/2026", "Codigo": "A-1", "Cant": "2", "Tipo": "cotizacion"},  # excluida
    ]
    dataset = data.normalize_rows(rows, {"Fecha": "date", "Codigo": "product_code", "Cant": "quantity",
                                         "Estado": "status", "Tipo": "operation_type"},
                                  product_lookup={"A-1": 7})
    reasons = sorted(r.reason for r in dataset.rejected)
    check("normalización acepta 1 y rechaza 6 con motivo",
          len(dataset.records) == 1 and reasons == sorted([data.REJECT_DUPLICATE, data.REJECT_MISSING_QUANTITY,
                                                           data.REJECT_MISSING_DATE, data.REJECT_UNKNOWN_PRODUCT,
                                                           data.REJECT_CANCELLED, data.REJECT_EXCLUDED_OPERATION]),
          str(reasons))
    check("registro normalizado mapea producto y fecha",
          dataset.records[0].product_id == 7 and dataset.records[0].date == date(2026, 2, 1))

    def series_of(values):
        s = data.aggregate_daily(1, [(d0 + timedelta(days=i), v) for i, v in enumerate(values)], data.GRANULARITY_DAILY)
        return s

    check("ELIGIBLE con historial suficiente",
          data.classify_eligibility(series_of([3] * 60)).status == data.ELIGIBLE)
    check("INSUFFICIENT_HISTORY con pocas observaciones",
          data.classify_eligibility(series_of([3] * 10)).status == data.INSUFFICIENT_HISTORY)
    check("INTERMITTENT con >= 80% de ceros",
          data.classify_eligibility(series_of(([5] + [0] * 9) * 10)).status == data.INTERMITTENT)
    check("NO_DEMAND sin períodos positivos", data.classify_eligibility(series_of([0] * 40)).status == data.NO_DEMAND)
    check("INACTIVE por producto inactivo",
          data.classify_eligibility(series_of([3] * 60), product_is_active=False).status == data.INACTIVE)
    check("INACTIVE por ceros finales prolongados",
          data.classify_eligibility(series_of([3] * 40 + [0] * 95)).status == data.INACTIVE)


def test_backtest_no_leakage() -> None:
    print("\n[backtest]")
    d0 = date(2026, 1, 1)
    values = [float(5 + (i % 7) + (i % 5)) for i in range(90)]  # no periódica en 7: residuos != 0
    series = data.aggregate_daily(1, [(d0 + timedelta(days=i), v) for i, v in enumerate(values)], data.GRANULARITY_DAILY)
    result = backtest_service.rolling_backtest(series, forecast_service.BASELINE_MODEL, horizon=7, min_train=30)
    check("hay pliegues y el último termina al final de la serie",
          result.folds and result.folds[-1].origin + 7 == len(values))
    check("orígenes crecientes (expanding window)",
          all(a.origin < b.origin for a, b in zip(result.folds, result.folds[1:])))
    leak_free = all(fold.predicted == forecast_service.predict(values[:fold.origin], 7, result.model, data.GRANULARITY_DAILY)
                    and fold.actual == values[fold.origin:fold.origin + 7] for fold in result.folds)
    check("cada pliegue predice solo con el pasado y evalúa solo el futuro", leak_free)
    # Cambiar el futuro no cambia la predicción del pliegue: prueba directa de ausencia de fuga.
    mutated = data.aggregate_daily(1, [(d0 + timedelta(days=i), v if i < result.folds[-1].origin else 999.0)
                                       for i, v in enumerate(values)], data.GRANULARITY_DAILY)
    again = backtest_service.rolling_backtest(mutated, forecast_service.BASELINE_MODEL, 7, 30)
    check("alterar valores futuros no altera la predicción del último pliegue",
          again.folds[-1].predicted == result.folds[-1].predicted)
    check("serie corta -> sin pliegues (sin métrica inventada)",
          not backtest_service.rolling_backtest(data.aggregate_daily(1, [(d0, 1)], data.GRANULARITY_DAILY), "naive", 7, 30).folds)
    evaluation = backtest_service.evaluate_models(series, 7, 30)
    check("comparación de modelos elige el mejor WAPE",
          evaluation is not None and evaluation.best == evaluation.ranking()[0][0])
    fc = forecast_service.forecast(series, 30, forecast_service.BASELINE_MODEL, residual_std=result.residual_std)
    check("pronóstico a 30 días: 30 períodos, no negativo, total coherente",
          len(fc.path) == 30 and all(v >= 0 for v in fc.path) and close(fc.total, sum(fc.path)))
    check("intervalo ± deriva de residuos del backtest; None sin residuos",
          fc.half_width is not None and forecast_service.forecast(series, 30).half_width is None)
    fc7 = forecast_service.forecast(series, 7, forecast_service.BASELINE_MODEL)
    check("el horizonte cambia el cálculo", len(fc7.path) == 7 and fc7.total < fc.total)
    descriptive = forecast_service.descriptive_rate(series, 30)
    check("regla descriptiva: tasa media, sin intervalo", descriptive.kind == "descriptive" and descriptive.half_width is None)
    zeros = data.aggregate_daily(1, [(d0 + timedelta(days=i), 0) for i in range(90)], data.GRANULARITY_DAILY)
    all_zero = backtest_service.evaluate_models(zeros, 7, 30)
    check("WAPE None no gana el auto-select (cae al baseline)",
          all_zero is not None and all(r.metrics.wape is None for r in all_zero.results.values())
          and all_zero.best == forecast_service.BASELINE_MODEL)


def test_trends_and_risk() -> None:
    print("\n[tendencias y riesgo]")
    cfg = trend_service.DEFAULT_CONFIG
    check("UP determinista", trend_service.classify(130, 100, cfg)[0] == trend_service.TREND_UP)
    check("DOWN determinista", trend_service.classify(70, 100, cfg)[0] == trend_service.TREND_DOWN)
    check("STABLE dentro de tolerancia", trend_service.classify(102, 100, cfg)[0] == trend_service.TREND_STABLE)
    check("sin período anterior -> insuficiente (sin % inventado)",
          trend_service.classify(10, 0, cfg) == (trend_service.TREND_INSUFFICIENT, None)
          or trend_service.classify(10, 0, cfg)[1] is None)
    d0 = date(2026, 1, 1)
    up = data.aggregate_daily(1, [(d0 + timedelta(days=i), 1 if i < 30 else 2) for i in range(60)], data.GRANULARITY_DAILY)
    result = trend_service.analyze(up, d0 + timedelta(days=59))
    check("analyze detecta alza 30 vs 30 días", result.status == trend_service.TREND_UP and close(result.variation_pct, 100.0))
    check("observaciones son patrones, no causas",
          result.observations and not any("temporada" in o.lower() or "promoci" in o.lower() for o in result.observations))

    check("días de cobertura sin división por cero", risk.days_of_cover(10, 0) is None and risk.days_of_cover(10, None) is None)
    check("días de cobertura = stock / demanda diaria", close(risk.days_of_cover(10, 2.5), 4.0))
    check("etiqueta 'Sin demanda prevista' con pronóstico 0", risk.cover_label(None, True) == risk.NO_DEMAND_LABEL)
    a = risk.assess(0, 10, None, has_forecast=False)
    check("sin stock -> CRÍTICO operativo con motivo 'Sin stock'",
          a.level == risk.RISK_CRITICAL and a.kind == risk.KIND_OPERATIONAL and "Sin stock" in a.reasons)
    b = risk.assess(5, 10, None, has_forecast=False)
    check("bajo mínimo sin pronóstico -> riesgo operativo explicado",
          b.kind == risk.KIND_OPERATIONAL and b.low_stock and b.level != risk.RISK_LOW and b.reasons)
    c = risk.assess(4, 2, 2.0, has_forecast=True, lead_time_days=7)
    check("cobertura 2 días -> CRÍTICO predictivo con motivo de cobertura",
          c.level == risk.RISK_CRITICAL and c.kind == risk.KIND_PREDICTIVE and any("2" in r for r in c.reasons))
    d = risk.assess(1000, 10, 1.0, has_forecast=True)
    check("cobertura amplia -> BAJO", d.level == risk.RISK_LOW)


def test_replenishment_rules() -> None:
    print("\n[reabastecimiento]")
    today = date(2026, 3, 1)
    base = replenishment_service.compute(current_stock=10, minimum_stock=20, expected_daily_demand=3.0,
                                         pending_supply=0, unit_cost=Decimal("2"), today=today, lead_time_days=7)
    check("reorder point = demanda lead time + umbral operacional", close(base.reorder_point, 21 + 20))
    check("cantidad sugerida = ceil(ROP - posición)", base.suggested_quantity == 31 and base.needed)
    check("costo estimado = costo × cantidad", base.estimated_cost == Decimal("62.00"))
    pending = replenishment_service.compute(10, 20, 3.0, pending_supply=25, unit_cost=Decimal("2"), today=today, lead_time_days=7)
    check("orden pendiente reduce la necesidad", pending.suggested_quantity == 6 and pending.pending_supply == 25)
    check("motivo menciona supply descontado", any("pendientes" in r for r in pending.reasons))
    covered = replenishment_service.compute(10, 20, 3.0, pending_supply=100, unit_cost=Decimal("2"), today=today)
    check("supply suficiente -> sin compra", covered.suggested_quantity == 0 and not covered.needed)
    zero = replenishment_service.compute(0, 5, 0.0, 0, Decimal("1"), today)
    check("demanda 0 no divide entre cero y sin stock es prioridad alta",
          zero.cover_days is None and zero.priority == replenishment_service.PRIORITY_HIGH and zero.recommended_date == today)
    check("prioridad alta cuando se agota antes del lead time",
          replenishment_service.compute(10, 0, 3.0, 0, Decimal("1"), today, 7).priority == replenishment_service.PRIORITY_HIGH)
    check("prioridad baja con cobertura amplia",
          replenishment_service.compute(1000, 0, 1.0, 0, Decimal("1"), today, 7).priority == replenishment_service.PRIORITY_LOW)
    assumed = replenishment_service.compute(10, 20, 3.0, 0, Decimal("2"), today)
    provided = replenishment_service.compute(10, 20, 3.0, 0, Decimal("2"), today, lead_time_days=10)
    check("lead time 7 está centralizado y se marca como supuesto",
          assumed.lead_time_days == replenishment_service.DEFAULT_LEAD_TIME_DAYS == 7
          and assumed.lead_time_source == replenishment_service.LEAD_TIME_ASSUMED
          and risk.DEFAULT_CONFIG.default_lead_time_days == replenishment_service.DEFAULT_LEAD_TIME_DAYS)
    check("lead time informado no se presenta como supuesto",
          provided.lead_time_days == 10 and provided.lead_time_source == replenishment_service.LEAD_TIME_PROVIDED)
    check("minimum_stock se copia como umbral, no como safety stock estadístico",
          assumed.safety_stock == 20)


def test_timezone() -> None:
    print("\n[zona horaria]")
    check("02:30 UTC pertenece al día comercial anterior (Caracas)",
          commercial_date(datetime(2026, 1, 2, 2, 30)) == date(2026, 1, 1))
    check("12:00 UTC es el mismo día", commercial_date(datetime(2026, 1, 2, 12, 0)) == date(2026, 1, 2))
    start = demand_src.utc_naive_start_of_commercial_day(date(2026, 1, 1))
    check("00:00 Caracas del 1-ene es 04:00 UTC", start == datetime(2026, 1, 1, 4, 0))


def test_channels_and_boundary(app, ids: dict, admin_id: int) -> None:
    print("\n[canales y frontera histórica]")
    # period_end interior a 2025: el CHECK de schema ya prohíbe event_date fuera de 2025;
    # aquí se prueba el recorte de aplicación (histórico el día 30, recorte el 31).
    period_end = date(2025, 12, 30)
    check("DeliveryNote no tiene sale_id (una venta no origina una nota)",
          "sale_id" not in DeliveryNote.__table__.c)
    with app.app_context():
        check("el seed de ventas no crea notas de entrega", DeliveryNote.query.count() == 0)
        cat = Category.query.filter_by(name="Construcción").first()
        product = Product(code="BND-050", name="Producto frontera", category_id=cat.id, unit="uds.",
                          current_stock=10, minimum_stock=2, purchase_price=Decimal("1"), sale_price=2)
        db.session.add(product)
        db.session.flush()
        hist = HistoricalImport(
            original_filename="boundary.csv", storage_key="bound-" + str(product.id),
            file_size_bytes=64, sha256="b" * 64, source_system="predictive-boundary",
            status=IMPORT_STATUS_CONFIRMED, period_start=date(2025, 1, 1), period_end=period_end,
            created_by_user_id=admin_id, confirmed_by_user_id=admin_id, confirmed_at=datetime.utcnow(),
        )
        db.session.add(hist)
        db.session.flush()

        def hist_row(day: date, qty: int, row: int):
            fp = f"bnd{product.id}{day}{qty}{row}"
            db.session.add(HistoricalDemandRecord(
                historical_import_id=hist.id, source_row_number=row,
                source_record_id_original=f"b-{row}", source_record_id_normalized=f"B-{row}",
                source_line_id_original=f"l-{row}", source_line_id_normalized=f"L-{row}",
                document_type="historical_demand", document_number_original=f"D-{row}",
                document_number_normalized=f"D-{row}", event_date=day,
                product_code_original="BND-050", product_code_normalized="BND-050",
                product_id=product.id, quantity=Decimal(qty), record_type=RECORD_TYPE_SALE,
                record_status=RECORD_STATUS_ISSUED, effective_status=RECORD_STATUS_ISSUED,
                fingerprint=fp, fingerprint_strength="strong", dedupe_key=fp,
                match_status="matched", include_in_demand=True,
                raw_row_json={"event_date": day.isoformat()},
            ))

        hist_row(period_end, 10, 1)
        hist_row(period_end + timedelta(days=1), 99, 2)
        customer = db.session.get(Customer, ids["customer"])
        product_id = product.id

        def sell_at(when: datetime, qty: int):
            sale = Sale(customer_id=customer.id, seller_id=admin_id, sold_at=when, payment_method="cash",
                        payment_condition="cash", status=STATUS_COMPLETED, subtotal=qty, total=qty)
            db.session.add(sale)
            db.session.flush()
            movement = StockMovement(product_id=product.id, movement_type="exit", quantity=qty,
                                     previous_stock=0, new_stock=0, user_id=admin_id, created_at=when)
            db.session.add(movement)
            db.session.flush()
            db.session.add(SaleLine(sale_id=sale.id, product_id=product.id, stock_movement_id=movement.id,
                                    product_code=product.code, product_name=product.name, quantity=qty,
                                    unit_price=1, line_subtotal=qty, line_total=qty))

        sell_at(demand_src.commercial_datetime_as_utc_naive(period_end, 22, 0), 7)
        sell_at(datetime(2025, 12, 31, 2, 0), 8)
        sell_at(demand_src.commercial_datetime_as_utc_naive(date(2025, 12, 31), 0, 30), 5)
        db.session.commit()
        cutoff = demand_src.get_temporal_cutoff()
        check("period_end inclusive y operativa al día siguiente Caracas",
              cutoff.historical_period_end == period_end and cutoff.operational_starts_on == date(2025, 12, 31))
        events = demand_src.load_demand_events(cutoff)
        historical = [e for e in events if e.source == demand_src.SOURCE_HISTORICAL and e.product_id == product_id]
        operational = [e for e in events if e.source == demand_src.SOURCE_OPERATIONAL]
        check("period_end entra en histórico (10 uds.) y no se recorta",
              any(e.event_date == period_end and e.quantity == Decimal("10") for e in historical))
        check("registro histórico posterior a period_end no entra (sin overlap)",
              not any(e.event_date == date(2025, 12, 31) for e in historical))
        check("ventas del period_end Caracas (22:00 local y 02:00 UTC) no son operativas",
              not any(e.event_date == period_end for e in operational))
        check("venta 00:30 Caracas del día siguiente es operativa (sin hueco)",
              any(e.event_date == date(2025, 12, 31) and e.quantity == Decimal("5") for e in operational))
        check("no hay overlap de fechas histórico/operativo en el corte",
              not ({e.event_date for e in historical} & {e.event_date for e in operational}))


# ---------------------------------------------------------------------------
# Pruebas HTTP sobre la app aislada
# ---------------------------------------------------------------------------
def _counts(app) -> dict:
    with app.app_context():
        return {m.__tablename__: db.session.query(m).count() for m in WRITE_TABLES}


def _raises(fn, exc) -> bool:
    try:
        fn()
    except exc:
        return True
    return False


def _csrf(client) -> str:
    page = client.get("/predictive/replenishment")
    match = re.search(rb'data-csrf="([^"]+)"', page.data)
    return match[1].decode() if match else ""


def test_http(app, ids: dict) -> None:
    print("\n[http live]")
    client = app.test_client()
    login(client, "core_admin", ADMIN_PASSWORD)
    # Exercise the real asynchronous boundary, then assert the original complete DTOs.
    from app.services.predictive_job_service import get_jobs
    for horizon in forecast_service.HORIZONS_DAYS:
        first = client.get(f"/predictive/demand?horizon={horizon}")
        check(f"cold demand {horizon} responds before completion", first.status_code == 200)
        check(f"operational forecast {horizon} completes", get_jobs(app).wait(120))
    for url in PAGES:
        r = client.get(url)
        check(f"{url} 200 y sin fixtures", r.status_code == 200 and not any(m in r.data for m in FIXTURE_MARKERS))
        check(f"{url} has no derived accuracy or gauge",
              b"exactitud" not in r.data.lower() and b"data-gauge=" not in r.data)
    demand = client.get("/predictive/demand").data
    check("demand LIVE muestra productos pronosticados reales",
          b"Cemento Portland 50 kg" in demand and b"Pintura Blanca 18 L" in demand)
    check("sin demanda observada no hay pronóstico; historial corto solo regla descriptiva",
          b"Tornillo Drywall" not in demand and b"Taladro Percutor 750W" in demand and b"descriptiv" in demand)
    check("KPI de error usa WAPE y no 'Precisión general (MAPE)'",
          b"Error del modelo (WAPE)" in demand and b"Precisi\xc3\xb3n general" not in demand)
    check("ventas canceladas no cuentan como demanda", b"503 uds." not in demand and b"1,500" not in demand)
    check("el pronóstico de Cemento (3/día) a 30 días es ~90 uds. (hoy parcial excluido)",
          re.search(rb"Cemento Portland 50 kg.{0,1500}?\b(8\d|9\d) sacos", demand, re.S) is not None)

    critical = client.get("/predictive/critical").data
    check("críticos LIVE: sin stock (Varilla) y bajo stock (Taladro) aparecen con riesgo operativo",
          b"Varilla Corrugada 12 mm" in critical and b"Taladro Percutor 750W" in critical and b"Sin stock" in critical)
    check("críticos LIVE excluye producto inactivo", b"Cinta M\xc3\xa9trica" not in critical)
    trends = client.get("/predictive/trends?trend=up").data
    check("tendencias LIVE detecta alza de Pintura y usa 'Patr\xc3\xb3n detectado'",
          b"Pintura Blanca 18 L" in trends and b"Patr\xc3\xb3n detectado" in trends and b"Posible causa" not in trends)
    accuracy = client.get("/predictive/accuracy").data
    check("precisión LIVE presenta MAPE como error y WAPE", b"WAPE" in accuracy and b"MAPE" in accuracy
          and b"Error porcentual" in accuracy)
    check("precisión LIVE sin causas inventadas", b"Promociones" not in accuracy and b"Estacionalidad at" not in accuracy)

    # Presentation-only regression: pooled WAPE is never converted or capped at 100%.
    from dataclasses import replace
    from app.controllers import predictive_presenters as presenters
    for wape in (0.0, 20.0, 113.2, None):
        measured = replace(metrics.compute([10.0], [12.0]), wape=wape)
        with patch.object(presenters, "pooled_metrics", return_value=measured):
            for url in ("/predictive/demand", "/predictive/accuracy",
                        "/predictive/accuracy?tab=category", "/predictive/accuracy?tab=product"):
                response = client.get(url)
                html = response.get_data(as_text=True)
                check(f"{url} preserves WAPE {wape} without derived accuracy",
                      response.status_code == 200 and presenters.pct(wape) in html
                      and "exactitud" not in html.lower() and "data-gauge=" not in html)
            html = client.get("/predictive/accuracy").get_data(as_text=True)
            check(f"real error metrics remain for WAPE {wape}",
                  all(name in html for name in ("WAPE", "MAE", "RMSE", "MAPE", "ME (Sesgo)", "APE")))
            exported = client.get("/api/predictions/export/model_accuracy")
            check(f"CSV preserves WAPE {wape} without derived accuracy",
                  exported.status_code == 200 and presenters.pct(wape) in exported.get_data(as_text=True)
                  and b"exactitud" not in exported.data.lower())

    # Filtros: categoría Pinturas deja solo Pintura; búsqueda por código.
    filtered = client.get(f"/predictive/demand?category={ids['category_pinturas']}").data
    check("filtro categoría afecta tabla y KPI", b"Pintura Blanca 18 L" in filtered and b"Cemento Portland" not in filtered)
    searched = client.get("/predictive/demand?search=CEM-050").data
    check("búsqueda por código", b"Cemento Portland" in searched and b"Pintura Blanca" not in searched)
    supplier = client.get(f"/predictive/demand?supplier={ids['supplier']}").data
    check("filtro proveedor (órdenes no canceladas)", b"Cemento Portland" in supplier and b"Varilla" not in supplier)
    h7, h90 = client.get("/predictive/demand?horizon=7").data, client.get("/predictive/demand?horizon=90").data
    check("horizonte cambia el título y el cálculo", b"pr\xc3\xb3ximos 7 d\xc3\xadas" in h7 and b"pr\xc3\xb3ximos 90 d\xc3\xadas" in h90 and h7 != h90)
    check("horizonte inválido cae al valor por defecto", b"pr\xc3\xb3ximos 30 d\xc3\xadas" in client.get("/predictive/demand?horizon=45").data)

    # Paginación real: per_page=10 con 3 filas -> una página; per_page fuera de opciones -> default.
    page = client.get("/predictive/critical?per_page=999").data
    check("per_page fuera de opciones usa el valor por defecto", b"10 por p\xc3\xa1gina" in page)
    check("resumen de paginación honesto", re.search(rb"Mostrando 1 a \d+ de \d+ productos", page) is not None)
    check("página fuera de rango se acota", client.get("/predictive/critical?page=99").status_code == 200)

    # Exportación CSV LIVE respeta filtros.
    export = client.get(f"/api/predictions/export/demand_forecast?category={ids['category_pinturas']}")
    check("export CSV 200 text/csv", export.status_code == 200 and export.mimetype == "text/csv", str(export.status_code))
    body = export.data.decode("utf-8-sig")
    check("export respeta filtros y no incluye fixtures", "Pintura Blanca 18 L" in body and "Cemento" not in body and "Prophet" not in body)
    for page in ("replenishment", "critical_products", "trends", "model_accuracy"):
        response = client.get(f"/api/predictions/export/{page}")
        check(f"export {page} 200 without derived accuracy",
              response.status_code == 200 and b"exactitud" not in response.data.lower())
    check("export vista desconocida -> 404", client.get("/api/predictions/export/nope").status_code == 404)
    empty = client.get("/api/predictions/export/replenishment?search=NOEXISTE")
    check("export sin datos responde CSV solo con cabecera", empty.status_code == 200 and len(empty.data.decode("utf-8-sig").strip().splitlines()) == 1)

    # Solicitud de reposición: nunca automática. No crea orden de compra ni mueve stock.
    before = _counts(app)
    check("el análisis no crea órdenes ni solicitudes",
          before["purchase_orders"] == 3 and before["replenishment_requests"] == 0)
    inv = app.test_client()
    login(inv, "core_inv", "inv123")
    token = _csrf(inv)
    check("inventario expone el token CSRF de la solicitud", bool(token))
    inv.environ_base.pop("HTTP_X_CSRFTOKEN", None)
    payload = {"items": [{"product_id": ids["PIN"], "quantity": 12}], "confirm": True}
    r = inv.post("/api/predictions/replenishment/orders", json=payload)
    check("sin CSRF -> 400", r.status_code == 400, str(r.status_code))
    headers = {"X-CSRFToken": token}
    denied = client.post("/api/predictions/replenishment/orders", json=payload,
                         headers={"X-CSRFToken": _csrf(client)})
    check("admin sin replenishment:create -> 403", denied.status_code == 403, denied.get_data(as_text=True)[:160])
    r = inv.post("/api/predictions/replenishment/orders", json={**payload, "confirm": False}, headers=headers)
    check("sin confirmación humana -> 400", r.status_code == 400, str(r.status_code))
    r = inv.post("/api/predictions/replenishment/orders", json={**payload, "items": []}, headers=headers)
    check("sin selección -> 400", r.status_code == 400)
    check("rechazos no crean SR ni OC", _counts(app) == before)
    r = inv.post("/api/predictions/replenishment/orders",
                 json={**payload, "notes": "Desde predictivo", "unit_cost": "999.99",
                       "supplier_id": ids["supplier"], "payment_condition": "Crédito 30 días"},
                 headers=headers)
    check("campos comerciales no permitidos -> 400 sin escrituras",
          r.status_code == 400 and _counts(app) == before, r.get_data(as_text=True)[:200])
    r = inv.post("/api/predictions/replenishment/orders",
                 json={**payload, "notes": "Desde predictivo"}, headers=headers)
    body = r.get_json() or {}
    check("solicitud confirmada -> 201 SR",
          r.status_code == 201 and bool(re.fullmatch(r"SR-\d{6}", body.get("request_number") or "")),
          r.get_data(as_text=True)[:200])
    check("la respuesta no expone costos ni condición de pago",
          "unit_cost" not in r.get_data(as_text=True) and "999.99" not in r.get_data(as_text=True)
          and "Crédito" not in r.get_data(as_text=True))
    after = _counts(app)
    check("una SR y una línea; OC y stock intactos",
          after["replenishment_requests"] == before["replenishment_requests"] + 1
          and after["replenishment_request_lines"] == before["replenishment_request_lines"] + 1
          and after["purchase_orders"] == before["purchase_orders"]
          and after["purchase_order_lines"] == before["purchase_order_lines"]
          and after["stock_movements"] == before["stock_movements"])
    with app.app_context():
        sr = ReplenishmentRequest.query.filter_by(request_number=body.get("request_number")).one()
        check("la SR queda abierta con la cantidad elegida y la nota",
              sr.status == "open" and len(sr.lines) == 1 and sr.lines[0].quantity == 12
              and sr.notes == "Desde predictivo" and sr.purchase_order_id is None)
    replenishment = inv.get("/predictive/replenishment").data
    with app.app_context():
        pending = replenishment_service.pending_supply_by_product()
        check("la SR no aumenta el supply de la OC aprobada", pending.get(ids["PIN"]) == 30, str(pending))
        check("la orden cancelada de Varilla no aporta supply", ids["VAR"] not in pending)
    check("reabastecimiento de inventario no muestra costos",
          b"Costo estimado" not in replenishment and b"Monto estimado" not in replenishment
          and b"data-unit-cost" not in replenishment)
    check("reabastecimiento LIVE no inventa supply de órdenes canceladas",
          b"Varilla Corrugada 12 mm" in replenishment)
    r = inv.post("/api/predictions/replenishment/orders",
                 json={"items": [{"product_id": ids["VAR"], "quantity": 12}], "confirm": True}, headers=headers)
    check("segunda SR (Varilla) -> 201 sin OC nueva",
          r.status_code == 201 and bool(re.fullmatch(r"SR-\d{6}", (r.get_json() or {}).get("request_number") or ""))
          and _counts(app)["purchase_orders"] == before["purchase_orders"],
          r.get_data(as_text=True)[:200])
    var_detail = inv.get(f"/api/predictions/products/{ids['VAR']}/detail").json
    rec = var_detail.get("recommendation") or {}
    check("la SR de Varilla no reduce la sugerencia ni muestra costos",
          var_detail["pending"] == 0 and rec.get("pending_supply") == 0
          and "unit_cost" not in rec and "purchase_price" not in (var_detail.get("preferred_supplier") or {}))



def test_reference_mode(app) -> None:
    print("\n[ref=1]")
    client = app.test_client()
    login(client, "core_admin", ADMIN_PASSWORD)
    before = _counts(app)
    app.debug = True
    try:
        with patch.object(db.session, "commit", wraps=db.session.commit) as commit, \
                patch.object(forecast_service, "predict", side_effect=AssertionError("ref no entrena")) as predict:
            for url in PAGES:
                r = client.get(url + "?ref=1")
                check(f"{url}?ref=1 200 con fixture", r.status_code == 200 and b"Ferreter" in r.data)
            check("ref=1 muestra fixtures deterministas", b"3,245" in client.get("/predictive/demand?ref=1").data)
            r = client.post("/api/predictions/replenishment/orders?ref=1", json={"confirm": True, "items": [{"product_id": 1, "quantity": 1}], "supplier_id": 1},
                            headers={"Referer": "http://localhost/predictive/replenishment?ref=1"})
            check("generar solicitud en ref=1 rechazado (guardas de referencia)", r.status_code in (403, 409), str(r.status_code))
            check("ref=1 nunca hace commit", commit.call_count == 0, str(commit.call_count))
            check("ref=1 nunca entrena ni pronostica modelos", predict.call_count == 0)
    finally:
        app.debug = False
    check("ref=1 produce 0 escrituras", _counts(app) == before)
    disabled = client.get("/predictive/demand?ref=1")
    check("ref=1 sin debug no entrega fixtures", disabled.status_code == 404 and b"3,245" not in disabled.data)


def test_permissions(app) -> None:
    print("\n[permisos]")
    anon = app.test_client()
    r = anon.get("/predictive/demand")
    check("anónimo redirigido al login", r.status_code in (302, 401))
    check("export anónimo rechazado", anon.get("/api/predictions/export/demand").status_code in (302, 401))
    inv = app.test_client()
    login(inv, "core_inv", "inv123")
    check("inventario puede leer predictivo", inv.get("/predictive/demand").status_code == 200)
    check("inventario puede exportar", inv.get("/api/predictions/export/critical_products").status_code == 200)
    page = inv.get("/predictive/replenishment").data
    check("inventario ve el botón de solicitud",
          b'data-order-url' in page and b'id="px-order-open"' in page)
    check("inventario no ve costos en reabastecimiento",
          b"Costo estimado" not in page and b"Monto estimado" not in page and b"data-unit-cost" not in page)
    r = inv.post("/api/predictions/replenishment/orders", json={"confirm": True, "items": []},
                 headers={"X-CSRFToken": _csrf(inv)})
    check("inventario con replenishment:create valida la selección vacía -> 400", r.status_code == 400, str(r.status_code))
    ven = app.test_client()
    login(ven, "core_ven", "ven123")
    r = ven.get("/predictive/demand")
    check("vendedor sin predictions:read recibe 403", r.status_code == 403 and b"Acceso denegado" in r.data)
    check("vendedor no exporta", ven.get("/api/predictions/export/demand_forecast").status_code == 403)


def test_no_data_states(admin_id: int) -> None:
    """App vacía (solo usuarios): cada vista declara honestamente que no puede calcular."""
    print("\n[sin datos]")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        app, _ = setup_app(Path(tmp))
        client = app.test_client()
        login(client, "core_admin", ADMIN_PASSWORD)
        expected = {
            "/predictive/demand": b"Historial insuficiente para generar un pron\xc3\xb3stico confiable.",
            "/predictive/replenishment": b"Las recomendaciones predictivas estar\xc3\xa1n disponibles cuando exista un pron\xc3\xb3stico v\xc3\xa1lido.",
            "/predictive/accuracy": b"No existen predicciones hist\xc3\xb3ricas evaluables.",
            "/predictive/trends": b"Sin historial suficiente.",
        }
        for url, message in expected.items():
            body = client.get(url).data
            check(f"{url} estado sin datos", message in body and b"N/D" in body and not any(m in body for m in FIXTURE_MARKERS))
        critical = client.get("/predictive/critical")
        check("críticos sin datos renderiza 200 sin fixtures",
              critical.status_code == 200 and not any(m in critical.data for m in FIXTURE_MARKERS))
        empty_export = client.get("/api/predictions/export/demand_forecast")
        check("export sin datos -> CSV con cabecera",
              empty_export.status_code == 200
              and empty_export.mimetype == "text/csv"
              and len(empty_export.data.decode("utf-8-sig").strip().splitlines()) == 1)
        with app.app_context():
            db.engine.dispose()


def run(app, ids: dict, admin_id: int) -> int:
    from test_forecast_advanced import run_tests

    global passed
    advanced_result = run_tests()
    passed += advanced_result.testsRun - len(advanced_result.failures) - len(advanced_result.errors)
    failures.extend(str(test) for test, _ in advanced_result.failures + advanced_result.errors)
    test_metrics()
    test_series_and_eligibility()
    test_backtest_no_leakage()
    test_trends_and_risk()
    test_replenishment_rules()
    test_timezone()
    test_channels_and_boundary(app, ids, admin_id)
    test_http(app, ids)
    test_reference_mode(app)
    test_permissions(app)
    test_no_data_states(admin_id)
    print(f"\n{passed} ok, {len(failures)} fallos")
    for name in failures:
        print(f"  - {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        app, users = setup_app(Path(root))
        ids = seed(app, users["admin"])
        try:
            if "--serve" in sys.argv:
                app.config.update(DEBUG=True, TEMPLATES_AUTO_RELOAD=True)
                app.debug = True
                print("Servidor desechable: http://127.0.0.1:5038  (core_admin / %s)" % ADMIN_PASSWORD)
                app.run(host="127.0.0.1", port=5038, use_reloader=False, use_debugger=False)
                status = 0
            else:
                status = run(app, ids, users["admin"])
        finally:
            with app.app_context():
                db.engine.dispose()
    sys.exit(status)
