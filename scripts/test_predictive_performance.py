"""Predictive performance regressions on disposable SQLite and pure model doubles.

No external database, real model training, empirical artifacts or network calls.
Run: .\\venv312\\Scripts\\python.exe scripts/test_predictive_performance.py
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from threading import Barrier, Event
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_predictive_module import ADMIN_PASSWORD, IsolatedConfig, login, seed, setup_app
from flask import Flask
from sqlalchemy import event
from app.extensions import db
from app.models import Product, SaleLine, HistoricalImport, HistoricalDemandRecord
from app.models.purchase import PurchaseOrderLine
from app.controllers import predictive_controller as controller
from app.services import predictive_analysis_service as analysis, predictive_cache_service as cache
from app.services import demand_data_service as demand, forecast_service as forecast
from app.services import predictive_data_service as data, settings_service
from app.services.predictive_read_service import materialized_read
from app.utils.schema import table_ready


def predict(history, periods, model, granularity="daily", starts=None, **kwargs):
    return [float(history[-1])] * periods


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.history = [1., 2., 3.]
        self.starts = [date(2026, 1, 1) + timedelta(days=i) for i in range(3)]

    def test_content_identity_parameters_and_mutable_result(self):
        with self.app.test_request_context("/predictive/demand"), patch.object(forecast, "predict", side_effect=predict) as spy:
            fn = cache.predictor(1)
            args = (self.history, 7, "mean", "daily")
            first = fn(*args, starts=self.starts)
            first[0] = 999
            self.assertEqual(fn(*args, starts=self.starts), [3.] * 7)
            self.assertEqual(spy.call_count, 1)
            fn([1., 2., 4.], 7, "mean", starts=self.starts)
            fn(*args, starts=[d + timedelta(days=1) for d in self.starts])
            fn(self.history, 30, "mean", starts=self.starts)
            fn(self.history, 7, "naive", starts=self.starts)
            fn(self.history, 7, "mean", "weekly", starts=self.starts)
            cache.predictor(2)(*args, starts=self.starts)
            with patch.dict(forecast.WINDOW, daily=8):
                fn(*args, starts=self.starts)
            self.assertEqual(spy.call_count, 8)
            with Flask("separate-application").test_request_context("/predictive/demand"):
                cache.predictor(1)(*args, starts=self.starts)
            self.assertEqual(spy.call_count, 9)

    def test_ref_and_nonrequest_bypass(self):
        self.assertIsNone(cache.predictor(1))
        with self.app.test_request_context("/predictive/demand?ref=1"):
            self.assertIsNone(cache.predictor(1))
            self.assertNotIn(cache._EXTENSION, self.app.extensions)

    def test_failure_invalid_output_and_operational_retry(self):
        with self.app.test_request_context("/predictive/demand"):
            fn = cache.predictor(1)
            with patch.object(forecast, "predict", side_effect=[forecast.ModelUnavailable("test"), [2.] * 7]) as spy:
                with self.assertRaises(forecast.ModelUnavailable):
                    fn(self.history, 7, "mean", starts=self.starts)
                self.assertEqual(fn(self.history, 7, "mean", starts=self.starts), [2.] * 7)
                self.assertEqual(spy.call_count, 2)
            with patch.object(forecast, "predict", return_value=[float("nan")]) as spy:
                for _ in range(2):
                    fn(self.history, 7, "mean", starts=self.starts)
                self.assertEqual(spy.call_count, 2)
            with patch.object(forecast, "predict", side_effect=predict) as spy:
                for _ in range(2):
                    fn(self.history, 7, "mean", starts=self.starts, operational=True,
                       future_starts=[self.starts[-1] + timedelta(days=i + 1) for i in range(7)])
                self.assertEqual(spy.call_count, 2)

    def test_single_flight_and_bounded_entries(self):
        state = cache._Predictions()
        started, finish = Event(), Event()
        calls = []

        def compute():
            calls.append(1)
            started.set()
            self.assertTrue(finish.wait(5))
            return [3.]

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(state.get, (1,), compute, 1)
            self.assertTrue(started.wait(5))
            b = pool.submit(state.get, (1,), compute, 1)
            finish.set()
            self.assertEqual(a.result(5), b.result(5))
        self.assertEqual(len(calls), 1)
        self.assertFalse(state.pending)
        with patch.object(cache, "_MAX_ENTRIES", 2):
            for key in (2, 3, 4):
                state.get((key,), lambda: [1.], 1)
        self.assertEqual(len(state.values), 2)


class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        with patch.dict(IsolatedConfig.SQLALCHEMY_ENGINE_OPTIONS, pool_size=1, max_overflow=0, pool_timeout=2):
            cls.app, cls.users = setup_app(Path(cls.temp.name))
        # Use the real permission catalog in the disposable authoritative schema.
        from app.services import rbac_service
        with cls.app.app_context():
            rbac_service.ensure_seeded()
        cls.ids = seed(cls.app, cls.users["admin"])
        with cls.app.app_context():
            cls.engine = db.engine
            assert cls.engine.url.drivername == "sqlite" and Path(cls.engine.url.database).parent == Path(cls.temp.name)
            # Optional-table reflection uses another connection; prime it without a transaction.
            for name in db.metadata.tables:
                table_ready(name)
            settings_service.is_available()
        cls.clients = [cls.app.test_client(), cls.app.test_client()]
        for client in cls.clients:
            login(client, "core_admin", ADMIN_PASSWORD)

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.engine.dispose()
        cls.temp.cleanup()

    def tearDown(self):
        self.assertEqual(self.engine.pool.checkedout(), 0)

    def test_request_release_auth_and_pool_one_concurrency(self):
        barrier = Barrier(2, action=lambda: self.assertEqual(self.engine.pool.checkedout(), 0))
        original = analysis.analyze_product
        from threading import local
        state = local()

        def checked(*args, **kwargs):
            if not getattr(state, "entered", False):
                state.entered = True
                barrier.wait(5)
            return original(*args, **kwargs)

        with patch.object(analysis, "analyze_product", side_effect=checked), patch.object(forecast, "predict", side_effect=predict):
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = [pool.submit(client.get, "/predictive/demand?method=mean") for client in self.clients]
                for future in responses:
                    response = future.result(15)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(b"core_admin", response.data)

    def test_dirty_flushed_and_explicit_transactions_remain_owned(self):
        with self.app.app_context():
            product = db.session.get(Product, self.ids["CEM"])
            old = product.current_stock
            product.current_stock = old + 1
            session = db.session()
            with materialized_read(release_session=True):
                Product.query.count()  # Autoflush must not erase the entry-time ownership decision.
            self.assertIs(db.session(), session)
            self.assertEqual(self.engine.pool.checkedout(), 1)
            db.session.rollback()
            self.assertEqual(product.current_stock, old)
            db.session.remove()
            with db.session.begin():
                with materialized_read(release_session=True):
                    Product.query.count()
                self.assertTrue(db.session().in_transaction())
            with materialized_read():
                Product.query.count()
            self.assertEqual(self.engine.pool.checkedout(), 1)
            db.session.remove()

    def test_trends_do_not_forecast_and_preserve_all_view_results(self):
        original = analysis.analyze
        with self.app.test_request_context("/predictive/trends"), patch.object(forecast, "predict", side_effect=predict):
            for period in (30, 60, 90):
                args = {"period": str(period), "trend": "all", "per_page": "50"}
                actual = controller.trends_view(args)

                def full(*a, **kw):
                    kw["observed_only"] = False
                    return original(*a, **kw)

                with patch.object(analysis, "analyze", side_effect=full):
                    expected = controller.trends_view(args)
                actual.pop("generated_at"); expected.pop("generated_at")
                self.assertEqual(actual, expected)
        with patch.object(forecast, "predict", side_effect=AssertionError("trends must not forecast")):
            self.assertEqual(self.clients[0].get("/predictive/trends").status_code, 200)

    def test_scoped_canonical_history_and_readiness(self):
        with self.app.app_context():
            all_events = demand.load_demand_events()
            all_series = data.load_series()
            for key in ("CEM", "PIN", "TOR"):
                pid = self.ids[key]
                self.assertEqual(demand.load_demand_events(product_id=pid), [e for e in all_events if e.product_id == pid])
                self.assertEqual(data.load_series(product_id=pid),
                                 {pid: all_series[pid]} if pid in all_series else {})
            db.session.remove()

    def test_product_count_does_not_multiply_queries(self):
        with self.app.app_context():
            category = db.session.get(Product, self.ids["CEM"]).category_id
            counts = []
            current = []

            def count(*args):
                current.append(1)

            event.listen(self.engine, "before_cursor_execute", count)
            try:
                analysis.analyze(observed_only=True)
                counts.append(len(current))
                for i in range(30):
                    db.session.add(Product(code=f"PERF-{i}", name=f"Synthetic {i}", category_id=category))
                db.session.flush()
                current.clear()
                result = analysis.analyze(observed_only=True)
                counts.append(len(current))
                self.assertEqual(len(result.items), 36)
                self.assertEqual(counts[0], counts[1])
            finally:
                event.remove(self.engine, "before_cursor_execute", count)
                db.session.rollback(); db.session.remove()

    def test_global_cutoff_related_return_and_left_boundary(self):
        with self.app.app_context():
            end = date(2025, 12, 31)
            batch = HistoricalImport(original_filename="synthetic.csv", storage_key="performance-test", file_size_bytes=1,
                sha256="a" * 64, source_system="performance-test", status="confirmed", period_start=date(2025, 1, 1),
                period_end=end, created_by_user_id=self.users["admin"])
            db.session.add(batch); db.session.flush()

            def row(number, pid, kind, related=None):
                result = HistoricalDemandRecord(historical_import_id=batch.id, source_row_number=number,
                    event_date=end, product_code_original="SYN", product_code_normalized="SYN", product_id=pid,
                    quantity=2, record_type=kind, record_status="issued", effective_status="issued",
                    fingerprint=str(number), fingerprint_strength="strong", match_status="matched",
                    include_in_demand=True, raw_row_json={}, related_record_id=related)
                db.session.add(result); db.session.flush()
                return result

            original = row(1, self.ids["CEM"], "sale")
            row(2, self.ids["CEM"], "return", original.id)
            row(3, self.ids["PIN"], "return", original.id)
            full = demand.load_demand_events()
            selected = demand.load_demand_events(product_id=self.ids["PIN"])
            self.assertEqual(selected, [e for e in full if e.product_id == self.ids["PIN"]])
            self.assertEqual(selected[0].inconsistency_code, demand.INCONSISTENCY_RELATED_PRODUCT_MISMATCH)
            self.assertEqual(demand.get_temporal_cutoff().operational_starts_on, date(2026, 1, 1))
            self.assertEqual(data.load_series(product_id=self.ids["CEM"])[self.ids["CEM"]],
                             data.load_series()[self.ids["CEM"]])
            db.session.rollback(); db.session.remove()

    def test_full_output_equality_cache_reuse_and_fresh_stock_supply(self):
        def domain(result):
            return [asdict(item) for item in result.items]

        with self.app.test_request_context("/predictive/demand"), patch.object(forecast, "predict", side_effect=predict) as spy:
            uncached = analysis.analyze()
            cached = analysis.analyze(cache_predictions=True, release_session=True)
            count = spy.call_count
            again = analysis.analyze(cache_predictions=True, release_session=True)
            self.assertEqual(spy.call_count, count)
            self.assertEqual(domain(uncached), domain(cached))
            self.assertEqual(domain(cached), domain(again))
            product = db.session.get(Product, self.ids["CEM"])
            product.current_stock += 5
            supply = PurchaseOrderLine.query.filter_by(product_id=product.id).first()
            supply.quantity_received += 1
            changed = analysis.analyze(cache_predictions=True)
            self.assertEqual(spy.call_count, count)
            item = next(i for i in changed.items if i.product.id == product.id)
            self.assertEqual(item.pending_supply, 99)
            self.assertEqual(item.recommendation.inventory_position, product.current_stock + item.pending_supply)
            line = SaleLine.query.filter_by(product_id=product.id).order_by(SaleLine.id).first()
            line.quantity += 1
            analysis.analyze(cache_predictions=True)
            self.assertGreater(spy.call_count, count)
            db.session.rollback(); db.session.remove()


if __name__ == "__main__":
    unittest.main(verbosity=2)
