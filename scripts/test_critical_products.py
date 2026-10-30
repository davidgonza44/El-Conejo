r"""Regresiones de demanda, cobertura y riesgo: SQLite temporal, sin demo ni MySQL.

Uso: .\venv312\Scripts\python.exe scripts/test_critical_products.py
"""
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_predictive_module import setup_app, login, ADMIN_PASSWORD
from app.extensions import db
from app.models import Category, Product
from app.services import critical_product_service as risk, forecast_service as fc
from app.services import predictive_analysis_service as analysis, predictive_data_service as data
from app.services import predictive_product_detail_service as detail
from app.controllers import predictive_controller as controller, predictive_presenters as fmt

TODAY = date(2026, 10, 7)


def product(**changes):
    fields = dict(id=1, name="Producto aislado", code="TEST-001", category_id=1,
                  category=None, unit="metro", current_stock=10, minimum_stock=0,
                  purchase_price=Decimal("1"), image_url=None, is_active=True,
                  approval_status="approved", created_at=datetime(2026, 1, 1))
    return SimpleNamespace(**(fields | changes))


def series(values=None, granularity="daily"):
    values = [2.5] * 60 if values is None else values
    step = data.PERIOD_DAYS[granularity]
    return data.TimeSeries(1, granularity,
                          [TODAY - timedelta(days=(len(values) - n) * step) for n in range(len(values))],
                          values, sum(v > 0 for v in values))


def item(p=None, s=None, horizon=30, method="mean", **kwargs):
    return analysis.analyze_product(p or product(), s or series(), horizon, method, TODAY, **kwargs)


class RiskTests(unittest.TestCase):
    def test_zero_stock_requires_demand_or_positive_minimum(self):
        for daily, available, minimum, expected in (
            (2.5, True, 0, "critical"), (0, True, 0, "low"),
            (None, False, 0, "low"), (None, False, 10, "critical"),
            (0, True, 10, "critical"),
        ):
            with self.subTest(daily=daily, minimum=minimum):
                result = risk.assess(0, minimum, daily, available)
                self.assertEqual(result.level, expected)
                self.assertTrue(result.out_of_stock)
                self.assertEqual(result.days_of_cover, 0 if daily and available else None)

    def test_coverage_decimal_and_invalid_inputs(self):
        self.assertEqual(risk.days_of_cover(Decimal("2.5"), Decimal("0.2")), 12.5)
        self.assertEqual(risk.cover_label(12.5, True), "12,5 días")
        self.assertEqual(risk.cover_label(0, True), "0 días")
        self.assertEqual(risk.cover_label(.001, True), "< 0,01 días")
        for stock, daily in ((1, 0), (1, None), (-1, 2), (1, -2),
                             (float("nan"), 2), (1, float("inf")), (1e308, 1e-308)):
            with self.subTest(stock=stock, daily=daily):
                self.assertIsNone(risk.days_of_cover(stock, daily))
        self.assertEqual(risk.assess(-1, 0, 2, True).level, "critical")
        self.assertEqual(risk.assess(0, 0, -2, True).kind, "operational")

    def test_minimum_reorder_and_cover_thresholds(self):
        self.assertEqual(risk.assess(4, 0, 2, True).level, "critical")
        self.assertEqual(risk.assess(12, 0, 2, True).level, "high")
        self.assertEqual(risk.assess(20, 0, 2, True).level, "medium")
        self.assertEqual(risk.assess(100, 0, 2, True).level, "low")
        result = risk.assess(20, 19, 1, True)
        self.assertEqual(result.reorder_point, 26)
        self.assertEqual(result.level, "medium")
        self.assertIn("Stock en o por debajo del punto de reorden", result.reasons)
        self.assertIn("En el stock mínimo", risk.assess(10, 10, 0, True).motive)


class AnalysisTests(unittest.TestCase):
    def test_real_forecast_and_actual_horizon(self):
        for horizon in (7, 30, 90):
            with self.subTest(horizon=horizon):
                result = item(product(current_stock=80), horizon=horizon)
                self.assertEqual(result.forecast.total, 2.5 * horizon)
                self.assertEqual(result.risk.days_of_cover, 32)
                self.assertEqual(result.risk.high_rotation, horizon == 90)
                self.assertEqual(result.forecast_status, "available")
                self.assertIsNone(result.forecast_reason)
                weekly = item(s=series([17.5] * 20, "weekly"), horizon=horizon)
                self.assertEqual(weekly.forecast.daily_rate, 2.5)
                self.assertEqual(weekly.risk.days_of_cover, 4)

    def test_zero_forecast_and_missing_are_distinct(self):
        zero = item(s=series([2.5] * 59 + [0]), method="naive")
        missing = item(s=series([0]))
        self.assertEqual(fmt.critical_row(zero)["demand"], "0 m")
        self.assertEqual(fmt.cover_text(zero), "Sin demanda prevista")
        self.assertEqual(fmt.critical_row(missing)["demand"], "Sin pronóstico disponible")
        self.assertEqual(fmt.cover_text(missing), "No calculable")
        self.assertEqual(missing.forecast_status, "no_demand")
        self.assertIn("No hay períodos", missing.forecast_reason)
        small = item(prediction=(fc.Forecast("mean", 30, .001, .001 / 30, [.001 / 30] * 30, None, "model"), None))
        self.assertEqual(fmt.critical_row(small)["demand"], "< 0,01 m")

    def test_verified_causes(self):
        new = item(product(created_at=datetime(2026, 10, 6)), series([0]))
        self.assertEqual(new.forecast_status, "new_without_history")
        self.assertIn("hace 1 día", new.forecast_reason)
        limited = item(s=series([2.5]))
        self.assertEqual(limited.forecast_status, "insufficient_history")
        self.assertIn("1 períodos", limited.forecast_reason)
        inactive = item(s=series([1] + [0] * 100))
        self.assertEqual(inactive.forecast_status, "inactive")
        self.assertIn("100 períodos", inactive.forecast_reason)
        for state, reason in (("pending", "cola"), ("running", "calculando"),
                              ("error", "no se pudo"), ("busy", "ocupado"),
                              ("unavailable", "no produjo"), ("expired", "venció")):
            with self.subTest(state=state):
                result = item(observed_only=True, computation_status=state)
                self.assertEqual(result.forecast_status, state)
                self.assertIn(reason, result.forecast_reason)
        self.assertEqual(item(observed_only=True).forecast_status, "not_generated")
        self.assertEqual(item(prediction=(None, None)).forecast_status, "model_unavailable")

    def test_catalog_inactive_or_pending_never_forecast(self):
        for p in (product(is_active=False), product(approval_status="pending"), product(approval_status="rejected")):
            with self.subTest(product=p):
                result = item(p)
                self.assertIsNone(result.forecast)
                self.assertIsNone(result.recommendation)
                self.assertEqual(result.forecast_status, "inactive")
                self.assertTrue(result.forecast_reason)

    def test_invalid_forecast_never_becomes_zero(self):
        valid = fc.Forecast("mean", 30, 75, 2.5, [2.5] * 30, None, "model")
        for invalid in (replace(valid, total=-1), replace(valid, daily_rate=float("nan")),
                        replace(valid, path=[]), replace(valid, horizon_days=7),
                        replace(valid, rate_unit="operational_day")):
            with self.subTest(forecast=invalid):
                result = item(prediction=(invalid, None))
                self.assertIsNone(result.forecast)
                self.assertEqual(result.forecast_status, "unavailable")
                self.assertEqual(fmt.cover_text(result), "No calculable")


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.app, _ = setup_app(Path(cls.temp.name))
        cls.client = cls.app.test_client()
        assert login(cls.client, "core_admin", ADMIN_PASSWORD).status_code == 200

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.engine.dispose()
        cls.temp.cleanup()

    def test_risk_kpis_unique_total_overlap_filters_and_pagination(self):
        for horizon in (7, 30, 90):
            products = [item(product(id=n, current_stock=0, minimum_stock=10), series([0]),
                             horizon=horizon) for n in range(1, 5)]
            # Crítico con stock positivo: no confundir nivel crítico con "Sin stock".
            # Su rotación alta ya está incluida en los 13, no añade otro producto.
            products.append(item(product(id=5, current_stock=1), horizon=horizon))
            products.extend(item(product(id=n, current_stock=5, minimum_stock=10), series([0]),
                                 horizon=horizon) for n in range(6, 14))
            products.append(item(product(id=14, current_stock=1000), series([0]), horizon=horizon))
            view_analysis = analysis.Analysis(horizon, "mean", "daily", TODAY, datetime.now(), items=products)
            cases = (
                ("per_page=50", (13, 5, 8, 1), [5, 0, 8, 0], 13, "1 a 13"),
                ("", (13, 5, 8, 1), [5, 0, 8, 0], 10, "1 a 10"),
                ("page=2", (13, 5, 8, 1), [5, 0, 8, 0], 3, "11 a 13"),
                ("risk=critical", (5, 5, 0, 1), [5, 0, 0, 0], 5, "1 a 5"),
                ("status=low_stock", (8, 0, 8, 0), [0, 0, 8, 0], 8, "1 a 8"),
                ("status=high_rotation", (1, 1, 0, 1), [1, 0, 0, 0], 1, "1 a 1"),
                ("risk=high", (0, 0, 0, 0), [0, 0, 0, 0], 0, "0 a 0"),
            )
            for query, values, distribution, row_count, page_range in cases:
                with self.subTest(horizon=horizon, query=query), \
                        patch.object(analysis, "analyze", return_value=view_analysis):
                    response = self.client.get(f"/predictive/critical?horizon={horizon}&{query}")
                    self.assertEqual(response.status_code, 200)
                    html = response.get_data(as_text=True)
                    kpis = re.findall(r'class="px-kpi-label">([^<]+)</p>\s*'
                                      r'<p class="px-kpi-value[^"]*">([^<]+)</p>', html)
                    self.assertEqual(kpis, list(zip(
                        ("Productos en riesgo", "Críticos", "Bajo stock", "Rotación alta"), map(str, values))))
                    self.assertEqual(json.loads(re.search("data-donut='([^']+)'", html)[1]), distribution)
                    self.assertEqual(sum(distribution), values[0])
                    ids = re.findall(r'data-product-detail="(\d+)"', html)
                    self.assertEqual(len(ids), row_count)
                    self.assertEqual(len(set(ids)), row_count)
                    self.assertIn(f"Mostrando {page_range} de {values[0]} productos", html)

    def test_table_counts_chart_filters_and_pending_no_500(self):
        for state in ("ready", "pending", "error", "unavailable"):
            extra = {"observed_only": state != "ready", "computation_status": state}
            products = [item(product(id=1, current_stock=0), **extra),
                    item(product(id=2, current_stock=10, minimum_stock=20), series([0])),
                    item(product(id=3, current_stock=0, minimum_stock=10), series([2.5] * 59 + [0]), method="naive", **extra),
                    item(product(id=4, current_stock=0), series([0])),
                    item(product(id=5, current_stock=0, minimum_stock=10, approval_status="pending")),
                    item(product(id=6, current_stock=0, minimum_stock=10, is_active=False))]
            with self.subTest(state=state):
                view_analysis = analysis.Analysis(30, "mean", "daily", TODAY, datetime.now(),
                                                  items=products, computation_status=state)
                with patch.object(analysis, "analyze", return_value=view_analysis):
                    response = self.client.get("/predictive/critical?per_page=50")
                    self.assertEqual(response.status_code, 200)
                    html = response.get_data(as_text=True)
                    self.assertIn("Sin pronóstico disponible", html)
                    self.assertIn("No calculable", html)
                    if state == "ready":
                        self.assertIn("Sin demanda prevista", html)
                    counts = json.loads(re.search("data-donut='([^']+)'", html)[1])
                    critical_count = 2 if state == "ready" else 1
                    self.assertEqual(counts, [critical_count, 0, 1, 0])
                    self.assertEqual(len(re.findall('data-product-detail="', html)), sum(counts))
                    with self.app.test_request_context("/predictive/critical"):
                        v = controller.critical_view({"per_page": "50", "risk": "critical"})
                        self.assertEqual(len(v["rows"]), critical_count)
                        self.assertEqual(v["pagination"]["total"], critical_count)
                        self.assertEqual(sum(part[3] for part in v["donut"]["items"]), critical_count)
                        self.assertEqual(v["kpis"][0]["value"], str(critical_count))

    def test_real_detail_missing_new_inactive_and_pending_no_500(self):
        with self.app.app_context():
            category = Category(name="Categoría aislada")
            db.session.add(category)
            db.session.flush()
            category_id = category.id
            db.session.commit()
            for n, overrides in enumerate(({}, {"is_active": False}, {"approval_status": "pending"}), 1):
                p = Product(code=f"NEW-{n}", name="Nuevo aislado", category_id=category_id,
                            current_stock=0, minimum_stock=0, **overrides)
                db.session.add(p)
                db.session.commit()
                product_id = p.id
                result = detail.build(p.id)
                self.assertEqual(result["risk"]["level"], "low")
                self.assertEqual(result["risk"]["cover_label"], "No calculable")
                self.assertTrue(all(period["forecast_reason"] for period in result["periods"]))
                response = self.client.get(f"/api/predictions/products/{product_id}/detail")
                self.assertEqual(response.status_code, 200)
            db.session.rollback()


if __name__ == "__main__":
    unittest.main(verbosity=2)
