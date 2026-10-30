"""Cold/pending/persistent LIVE regressions; disposable SQLite, no external DB/models.

Run with the project Python. Small pure baselines are real; advanced fits are doubles.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict
from datetime import date, timedelta
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event, current_thread
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_predictive_module import setup_app, seed, login, ADMIN_PASSWORD
from flask import Flask, has_app_context, has_request_context
from sqlalchemy import event
from app.extensions import db
from app.models import Product
from app.models.purchase import PurchaseOrderLine
from app.services import forecast_service as forecast, predictive_analysis_service as analysis
from app.services import predictive_cache_service as cache, predictive_job_service as jobs
from app.services import predictive_data_service as data


def sample_series():
    start = date(2026, 1, 1)
    return data.TimeSeries(1, "daily", [start + timedelta(days=n) for n in range(120)],
                           [float(2 + n % 3) for n in range(120)], 120)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.managers = []

    def tearDown(self):
        for manager in self.managers:
            self.assertTrue(manager.wait(15))
            manager.shutdown(5)
        self.temp.cleanup()

    def manager(self):
        manager = jobs.Jobs(cache._Predictions(self.root / "cache"))
        self.managers.append(manager)
        return manager

    def test_cold_pending_hot_restart_and_content_invalidation(self):
        manager = self.manager()
        series = sample_series()
        specs = [(series, 7, "mean")]
        expected = analysis.prediction_block(series, 7, "mean")
        self.assertEqual(manager.request(specs)[0], "pending")
        self.assertTrue(manager.wait(10))
        status, blocks = manager.request(specs)
        self.assertEqual(status, "ready")
        self.assertEqual(blocks[1, 7], expected)
        self.assertGreater(manager.cache.computed, 0)
        restart = self.manager()
        self.assertEqual(restart.request(specs), ("ready", blocks))
        self.assertEqual(restart.cache.computed, 0)
        self.assertIsNone(restart.thread)
        series.values[0] += 1
        self.assertEqual(restart.request(specs)[0], "pending")
        self.assertTrue(restart.wait(10))
        self.assertGreater(restart.cache.computed, 0)
        self.assertEqual(restart.request(specs)[1][1, 7], analysis.prediction_block(series, 7, "mean"))

    def test_actual_fresh_process_reuses_persistent_values(self):
        manager = self.manager()
        manager.request([(sample_series(), 7, "mean")])
        self.assertTrue(manager.wait(10))
        code = '''
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "scripts"))
from test_predictive_async import sample_series
from app.services import predictive_cache_service as cache, predictive_job_service as jobs
manager = jobs.Jobs(cache._Predictions(Path(sys.argv[1])))
status, blocks = manager.request([(sample_series(), 7, "mean")])
print(json.dumps({"status": status, "computed": manager.cache.computed, "worker": manager.thread is not None}))
'''
        result = subprocess.run([sys.executable, "-c", code, str(self.root / "cache")],
                                capture_output=True, text=True, timeout=30, check=True)
        self.assertEqual(json.loads(result.stdout), {"status": "ready", "computed": 0, "worker": False})

    def test_failures_are_not_persisted_and_original_fallback_completes(self):
        manager = self.manager()
        series = sample_series()
        original = forecast.predict

        def failed_candidate(history, periods, model, *args, **kwargs):
            if model == "arima":
                raise forecast.ModelUnavailable("synthetic nonconvergence")
            return original(history, periods, "mean", *args, **kwargs)

        with patch.object(forecast, "predict", side_effect=failed_candidate):
            expected = analysis.prediction_block(series, 7, "auto")
            self.assertEqual(manager.request([(series, 7, "auto")])[0], "pending")
            self.assertTrue(manager.wait(10))
            status, blocks = manager.request([(series, 7, "auto")])
            self.assertEqual(status, "ready")
            self.assertEqual(blocks[1, 7], expected)
            self.assertNotIn("arima", blocks[1, 7][1].results)
        with closing(manager.cache.disk.connect()) as connection:
            # Runtime doubles are memory-only; in particular no failure is serialized.
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM predictions").fetchone()[0], 0)

    def test_worker_failure_is_visible_and_retryable(self):
        manager = self.manager()
        specs = [(sample_series(), 7, "mean")]
        real_compute = jobs._compute

        def failing(*args, **kwargs):
            if not kwargs.get("read_only"):
                raise RuntimeError("private synthetic detail must not be exposed")
            return real_compute(*args, **kwargs)

        with patch.object(jobs, "_compute", side_effect=failing):
            self.assertEqual(manager.request(specs)[0], "pending")
            self.assertTrue(manager.wait(10))
            self.assertEqual(manager.request(specs), ("error", {}))
            self.assertFalse(manager.completed)
        with patch.object(jobs, "_RETRY_SECONDS", 0):
            self.assertEqual(manager.request(specs)[0], "pending")
            self.assertTrue(manager.wait(10))
        self.assertEqual(manager.request(specs)[0], "ready")

    def test_bounded_validated_disk_and_memory(self):
        state = cache._Predictions(self.root / "bounded")
        with patch.object(cache, "_MAX_ENTRIES", 2):
            for n in range(4):
                state.get(str(n), lambda: [1.0], 1)
            self.assertEqual(len(state.values), 2)
            with closing(state.disk.connect()) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM predictions").fetchone()[0], 2)
                connection.execute("UPDATE predictions SET value = '" + '["invalid"]' + "' WHERE key = '3'")
                connection.commit()
            self.assertIsNone(state.disk.read("3", 1))
        state.get("invalid", lambda: [float("nan")], 1)
        self.assertIsNone(state.disk.read("invalid", 1))

    def test_reference_does_not_initialize_store_or_worker(self):
        app = Flask("reference-test")
        app.config["PREDICTIVE_CACHE_DIR"] = str(self.root / "must-not-exist")
        with app.test_request_context("/?ref=1"):
            self.assertIsNone(cache.predictor(1))
            with self.assertRaises(ValueError):
                jobs.request_predictions([(sample_series(), 7, "mean")])
        self.assertFalse(app.extensions)
        self.assertFalse((self.root / "must-not-exist").exists())

    def test_process_lock_serializes_and_crash_releases_it(self):
        directory = self.root / "lock"
        directory.mkdir()
        marker = directory / "acquired"
        code = '''
import sys, time
from pathlib import Path
from threading import Event
from app.services.predictive_job_service import process_lock
with process_lock(Path(sys.argv[1]), Event()):
    Path(sys.argv[2]).write_text("yes")
    time.sleep(30)
'''
        process = subprocess.Popen([sys.executable, "-c", code, str(directory), str(marker)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            end = time.monotonic() + 20
            while not marker.exists() and time.monotonic() < end:
                time.sleep(0.05)
            self.assertTrue(marker.exists())
            entered = Event()
            def acquire():
                with jobs.process_lock(directory, Event()):
                    entered.set()
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(acquire)
                self.assertFalse(entered.wait(0.2))
                process.terminate()
                process.wait(5)
                future.result(5)
                self.assertTrue(entered.is_set())
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(5)

    def test_broken_lock_is_error_not_infinite_contention(self):
        directory = self.root / "broken-lock"
        directory.mkdir()
        target = "msvcrt.locking" if os.name == "nt" else "fcntl.flock"
        with patch(target, side_effect=OSError(errno.EINVAL, "synthetic lock failure")):
            with self.assertRaises(OSError):
                with jobs.process_lock(directory, Event()):
                    self.fail("broken lock must not acquire")

    def test_two_managers_share_store_without_duplicate_fits(self):
        specs = [(sample_series(), 7, "mean")]
        expected = cache._Predictions()
        jobs._compute(specs, expected)
        first, second = self.manager(), self.manager()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda manager: manager.request(specs), (first, second)))
        self.assertTrue(first.wait(10))
        self.assertTrue(second.wait(10))
        self.assertEqual(first.request(specs)[0], "ready")
        self.assertEqual(second.request(specs)[0], "ready")
        self.assertEqual(first.cache.computed + second.cache.computed, expected.computed)
        self.assertEqual(first.started + second.started, 1)

    def test_another_manager_never_enqueues_the_owned_token(self):
        owner, observer = self.manager(), self.manager()
        specs = [(sample_series(), 7, "mean")]
        entered, release = Event(), Event()
        original = jobs._compute

        def blocked(*args, **kwargs):
            if not kwargs.get("read_only"):
                entered.set()
                self.assertTrue(release.wait(10))
            return original(*args, **kwargs)

        with patch.object(jobs, "_compute", side_effect=blocked):
            try:
                self.assertEqual(owner.request(specs)[0], "pending")
                self.assertTrue(entered.wait(5))
                for _ in range(3):
                    self.assertEqual(observer.request(specs), ("running", {}))
                self.assertIsNone(observer.thread)
                self.assertFalse(observer.pending)
                self.assertTrue(observer.queue.empty())
            finally:
                release.set()
                self.assertTrue(owner.wait(10))
        self.assertEqual(observer.request(specs)[0], "ready")
        self.assertEqual(owner.started + observer.started, 1)

    def test_job_lease_is_exclusive_across_processes_and_crash_releases_it(self):
        manager = self.manager()
        specs = [(sample_series(), 7, "mean")]
        token = jobs.prediction_token(specs)
        marker = self.root / "lease-acquired"
        code = '''
import sys, time
from pathlib import Path
from unittest.mock import patch
with patch("dotenv.load_dotenv"):
    from app.services.predictive_job_service import _try_job_lease
lease = _try_job_lease(Path(sys.argv[1]), sys.argv[2])
assert lease is not None
Path(sys.argv[3]).write_text("yes")
time.sleep(30)
'''
        process = subprocess.Popen([sys.executable, "-c", code, str(manager.cache.disk.directory), token, str(marker)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            end = time.monotonic() + 20
            while not marker.exists() and time.monotonic() < end:
                time.sleep(0.05)
            self.assertTrue(marker.exists())
            manager.cache.disk.write_job(token, "running")
            self.assertEqual(manager.request(specs), ("running", {}))
            self.assertIsNone(manager.thread)
            process.terminate()
            process.wait(5)
            self.assertEqual(manager.request(specs)[0], "pending")
            self.assertTrue(manager.wait(10))
            self.assertEqual(manager.request(specs)[0], "ready")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(5)

    def test_lock_failure_clears_pending_and_can_retry(self):
        manager = self.manager()
        specs = [(sample_series(), 7, "mean")]
        with patch.object(jobs, "process_lock", side_effect=OSError(errno.EINVAL, "synthetic lock failure")):
            self.assertEqual(manager.request(specs)[0], "pending")
            self.assertTrue(manager.wait(10))
            self.assertFalse(manager.pending)
            self.assertEqual(manager.request(specs), ("error", {}))
            self.assertFalse(manager.completed)
            self.assertEqual(manager.cache.computed, 0)
        with patch.object(jobs, "_RETRY_SECONDS", 0):
            self.assertEqual(manager.request(specs)[0], "pending")
            self.assertTrue(manager.wait(10))
        self.assertEqual(manager.request(specs)[0], "ready")

    def test_unavailable_explicit_model_finishes_without_persisting_failure(self):
        manager = self.manager()
        specs = [(sample_series(), 7, "arima")]
        with patch.object(forecast, "predict", side_effect=forecast.ModelUnavailable("synthetic")):
            self.assertEqual(manager.request(specs)[0], "pending")
            self.assertTrue(manager.wait(10))
            status, blocks = manager.request(specs)
            self.assertEqual(status, "unavailable")
            self.assertIsNone(blocks[1, 7][0])
            self.assertFalse(manager.pending)
            self.assertFalse(manager.cache.values)
            self.assertEqual(manager.status(jobs.prediction_token(specs))["status"], "unavailable")
        with closing(manager.cache.disk.connect()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM predictions").fetchone()[0], 0)

    def test_status_cross_worker_running_ready_without_scheduling(self):
        manager, observer = self.manager(), self.manager()
        specs = [(sample_series(), 7, "mean")]
        token = jobs.prediction_token(specs)
        entered, release = Event(), Event()
        original = jobs._compute

        def blocked(*args, **kwargs):
            if not kwargs.get("read_only"):
                entered.set()
                self.assertTrue(release.wait(10))
            return original(*args, **kwargs)

        with patch.object(jobs, "_compute", side_effect=blocked):
            try:
                self.assertEqual(manager.request(specs, token=token)[0], "pending")
                self.assertTrue(entered.wait(5))
                self.assertEqual(observer.status(token)["status"], "running")
                self.assertFalse(observer.status(token)["terminal"])
                self.assertIsNone(observer.thread)
                self.assertEqual(manager.request(specs)[0], "running")
            finally:
                release.set()
                self.assertTrue(manager.wait(10))
        self.assertEqual(observer.status(token)["status"], "ready")
        self.assertTrue(observer.status(token)["terminal"])
        self.assertEqual(observer.cache.computed, 0)

    def test_status_deadline_does_not_duplicate_or_cancel_running_work(self):
        manager = self.manager()
        token = jobs.prediction_token([(sample_series(), 7, "mean")])
        manager.cache.disk.write_job(token, "running")
        manager.pending.add(token)
        with patch.object(jobs.time, "time", return_value=time.time() + manager.timeout + 1):
            state = manager.status(token)
            self.assertEqual(state["status"], "error")
            self.assertTrue(state["timed_out"])
            self.assertTrue(state["terminal"])
            self.assertEqual(manager.request([(sample_series(), 7, "mean")]), ("error", {}))
            self.assertIn(token, manager.pending)
            self.assertIsNone(manager.thread)
            self.assertEqual(manager.cache.computed, 0)
        manager.pending.remove(token)
        manager.cache.disk.write_job(token, "ready")
        self.assertEqual(manager.status(token)["status"], "ready")

    def test_ready_disk_handoff_never_returns_ready_without_blocks(self):
        manager = self.manager()
        specs = [(sample_series(), 7, "mean")]
        token = jobs.prediction_token(specs)
        manager.pending.add(token)
        manager.cache.disk.write_job(token, "ready")
        try:
            self.assertEqual(manager.request(specs), ("running", {}))
        finally:
            manager.pending.remove(token)

    def test_unknown_and_failed_statuses_are_terminal(self):
        manager = self.manager()
        token = "a" * 64
        self.assertEqual(manager.status(token)["status"], "expired")
        manager.cache.disk.write_job(token, "error")
        self.assertEqual(manager.status(token)["status"], "error")
        self.assertTrue(manager.status(token)["terminal"])

    def test_queued_retry_resets_terminal_status_but_preserves_running(self):
        manager = self.manager()
        token = "b" * 64
        manager.cache.disk.write_job(token, "error")
        manager.cache.disk.write_job(token, "pending", preserve_active=True)
        self.assertEqual(manager.status(token)["status"], "pending")
        manager.cache.disk.write_job(token, "running")
        manager.cache.disk.write_job(token, "pending", preserve_active=True)
        self.assertEqual(manager.status(token)["status"], "running")

    def test_lazy_daemon_does_not_delay_process_exit(self):
        app = Flask("reloader-parent")
        app.config["PREDICTIVE_CACHE_DIR"] = str(self.root / "parent")
        manager = jobs.get_jobs(app)
        self.managers.append(manager)
        self.assertIsNone(manager.thread)
        code = '''
import sys
from pathlib import Path
from threading import Event
sys.path.insert(0, str(Path.cwd() / "scripts"))
from test_predictive_async import sample_series
from app.services import predictive_cache_service as cache, predictive_job_service as jobs
entered = Event()
original = jobs._compute
def blocked(*args, **kwargs):
    if kwargs.get("read_only"):
        return original(*args, **kwargs)
    entered.set()
    Event().wait(60)
jobs._compute = blocked
manager = jobs.Jobs(cache._Predictions(Path(sys.argv[1])))
assert manager.request([(sample_series(), 7, "mean")])[0] == "pending"
assert entered.wait(5)
assert manager.thread.daemon
print("daemon process can exit during calculation")
'''
        result = subprocess.run([sys.executable, "-c", code, str(self.root / "daemon")],
                                capture_output=True, text=True, timeout=20, check=True)
        self.assertIn("daemon process can exit", result.stdout)

    def test_queue_and_completed_handoffs_are_bounded(self):
        entered, release = Event(), Event()
        original = jobs._compute
        def blocked(*args, **kwargs):
            if not kwargs.get("read_only"):
                entered.set()
                self.assertTrue(release.wait(10))
            return original(*args, **kwargs)
        with patch.object(jobs, "_MAX_JOBS", 2), patch.object(jobs, "_MAX_COMPLETED", 2):
            manager = self.manager()
            with patch.object(jobs, "_compute", side_effect=blocked):
                try:
                    first = sample_series()
                    self.assertEqual(manager.request([(first, 7, "mean")])[0], "pending")
                    self.assertTrue(entered.wait(5))
                    for pid in (2, 3):
                        series = sample_series()
                        series.product_id = pid
                        self.assertEqual(manager.request([(series, 7, "mean")])[0], "pending")
                    series = sample_series()
                    series.product_id = 4
                    self.assertEqual(manager.request([(series, 7, "mean")])[0], "busy")
                finally:
                    release.set()
                    self.assertTrue(manager.wait(10))
            self.assertEqual(len(manager.completed), 2)


class StatusEndpointTests(unittest.TestCase):
    """No business database: poll an isolated operational scratch store only."""

    def setUp(self):
        from app.routes.predictions import predictions_bp

        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.manager = jobs.Jobs(cache._Predictions(Path(self.temp.name)))
        self.app = Flask("predictive-status-test")
        self.app.extensions[jobs._EXTENSION] = self.manager
        self.app.register_blueprint(predictions_bp)
        self.client = self.app.test_client()
        self.token = "a" * 64

    def tearDown(self):
        self.manager.shutdown(5)
        self.temp.cleanup()

    def authorized(self):
        return patch("app.utils.auth_decorators.current_user",
                     SimpleNamespace(is_authenticated=True, role="admin"))

    def test_status_never_loads_demand_or_enqueues(self):
        self.manager.cache.disk.write_job(self.token, "running")
        with self.authorized(), patch("app.utils.auth_decorators.role_has_permission", return_value=True), \
             patch.object(data, "load_series", side_effect=AssertionError("poll loaded demand")), \
             patch.object(jobs.Jobs, "request", side_effect=AssertionError("poll scheduled work")):
            response = self.client.get(f"/api/predictions/status/{self.token}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["status"], "running")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.manager.cache.disk.write_job(self.token, "ready")
            self.assertEqual(self.client.get(f"/api/predictions/status/{self.token}").json["status"], "ready")
        self.assertIsNone(self.manager.thread)
        self.assertEqual(self.manager.cache.computed, 0)

    def test_status_keeps_authentication_and_permission(self):
        with patch("app.utils.auth_decorators.current_user", SimpleNamespace(is_authenticated=False)):
            self.assertEqual(self.client.get(f"/api/predictions/status/{self.token}").status_code, 401)
        with self.authorized(), patch("app.utils.auth_decorators.role_has_permission", return_value=False):
            self.assertEqual(self.client.get(f"/api/predictions/status/{self.token}").status_code, 403)

    def test_reference_and_invalid_tokens_never_initialize_jobs(self):
        with self.authorized(), patch("app.utils.auth_decorators.role_has_permission", return_value=True), \
             patch.object(jobs, "get_jobs", side_effect=AssertionError("invalid poll initialized jobs")):
            for path in ("invalid", self.token + "?ref=1"):
                self.assertEqual(self.client.get(f"/api/predictions/status/{path}").status_code, 404)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.app, users = setup_app(Path(cls.temp.name))
        # The disposable schema includes authoritative RBAC tables. Seed the
        # actual permissions instead of bypassing authorization in HTTP tests.
        from app.services import rbac_service
        with cls.app.app_context():
            rbac_service.ensure_seeded()
        cls.ids = seed(cls.app, users["admin"])
        cls.clients = [cls.app.test_client(), cls.app.test_client()]
        for client in cls.clients:
            login(client, "core_admin", ADMIN_PASSWORD)
        with cls.app.app_context():
            cls.engine = db.engine

    @classmethod
    def tearDownClass(cls):
        manager = cls.app.extensions.get(jobs._EXTENSION)
        if manager:
            manager.wait(15)
            manager.shutdown(5)
        with cls.app.app_context():
            db.session.remove()
            db.engine.dispose()
        cls.temp.cleanup()

    def test_cold_pending_duplicate_requests_pool_and_ready_equal(self):
        entered, release = Event(), Event()
        worker_checkouts = []
        seen = []

        def checkout(*args):
            if current_thread().name == "predictive-live":
                worker_checkouts.append(1)

        def slow(history, periods, model, granularity="daily", starts=None, **kwargs):
            seen.append((has_app_context(), has_request_context(), current_thread().name))
            entered.set()
            self.assertTrue(release.wait(10))
            return [float(history[-1])] * periods

        event.listen(self.engine, "checkout", checkout)
        try:
            with patch.object(forecast, "predict", side_effect=slow):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    responses = list(pool.map(lambda client: client.get("/predictive/demand?method=mean"), self.clients))
                self.assertTrue(entered.wait(5))
                self.assertTrue(all(r.status_code == 200 and any(
                    f'data-predictive-status="{state}"'.encode() in r.data for state in ("pending", "running"))
                    for r in responses))
                manager = jobs.get_jobs(self.app)
                self.assertEqual(manager.started, 1)
                self.assertTrue(manager.thread.daemon)
                for url in ("/predictive/replenishment?method=mean", "/predictive/critical?method=mean",
                            "/predictive/accuracy?method=mean", "/predictive/trends"):
                    page = self.clients[0].get(url)
                    self.assertEqual(page.status_code, 200)
                    self.assertEqual(page.data.count(b'data-predictive-status-url='), 0 if "trends" in url else 1)
                    self.assertEqual(self.engine.pool.checkedout(), 0)
                export = self.clients[0].get("/api/predictions/export/demand_forecast?method=mean")
                self.assertEqual(export.status_code, 202)
                self.assertEqual(export.mimetype, "application/json")
                self.assertIn("Retry-After", export.headers)
                self.assertEqual(manager.started, 1)
                release.set()
                self.assertTrue(manager.wait(15))
                self.assertTrue(all(not app and not request and thread == "predictive-live" for app, request, thread in seen))
                with self.app.test_request_context("/predictive/demand?method=mean"):
                    actual = analysis.analyze(model="mean", release_session=True, defer_predictions=True)
                    # Outside the background assertion/double, compare exact original domain behavior.
                with patch.object(forecast, "predict", side_effect=lambda h, n, *a, **kw: [float(h[-1])] * n):
                    with self.app.test_request_context("/predictive/demand?method=mean"):
                        expected = analysis.analyze(model="mean", release_session=True)
                self.assertEqual([asdict(i) for i in actual.items], [asdict(i) for i in expected.items])
                self.assertEqual(self.clients[0].get("/api/predictions/export/demand_forecast?method=mean").status_code, 200)
        finally:
            release.set()
            event.remove(self.engine, "checkout", checkout)
        self.assertFalse(worker_checkouts)
        self.assertEqual(self.engine.pool.checkedout(), 0)

    def test_detail_pending_keeps_observations_and_null_forecast(self):
        entered, release = Event(), Event()
        def slow(history, periods, *args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(10))
            return [float(history[-1])] * periods
        with patch.object(forecast, "predict", side_effect=slow):
            try:
                response = self.clients[0].get(f'/api/predictions/products/{self.ids["CEM"]}/detail')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json["computation_status"], "pending")
                self.assertEqual(response.json["history"]["total"], 270)
                self.assertTrue(all(period["total"] is None for period in response.json["periods"]))
                self.assertIsNone(response.json["recommendation"])
                self.assertFalse(response.json["can_order"])
                status_url = response.json["computation_status_url"]
                self.assertTrue(status_url.startswith("/api/predictions/status/"))
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.clients[0].get(status_url).json["status"], "running")
                self.assertEqual(self.engine.pool.checkedout(), 0)
            finally:
                release.set()
                self.assertTrue(jobs.get_jobs(self.app).wait(15))
            self.assertEqual(self.clients[0].get(status_url).json["status"], "ready")
            completed = self.clients[0].get(f'/api/predictions/products/{self.ids["CEM"]}/detail').json
            self.assertEqual(completed["computation_status"], "ready")
            self.assertTrue(all(period["total"] is not None for period in completed["periods"]))

    def test_ready_handoff_keeps_current_stock_and_supply(self):
        with self.app.app_context():
            first = analysis.analyze(model="mean", release_session=True, defer_predictions=True)
        self.assertIn(first.computation_status, ("ready", "pending"))
        manager = jobs.get_jobs(self.app)
        self.assertTrue(manager.wait(15))
        with self.app.app_context():
            before = analysis.analyze(model="mean", release_session=True, defer_predictions=True)
            computed = manager.cache.computed
            original = next(i for i in before.items if i.product.id == self.ids["CEM"])
            product = db.session.get(Product, self.ids["CEM"])
            line = PurchaseOrderLine.query.filter_by(product_id=product.id).first()
            old_stock, old_received = product.current_stock, line.quantity_received
            product.current_stock += 5
            line.quantity_received += 1
            db.session.commit()
            try:
                after = analysis.analyze(model="mean", release_session=True, defer_predictions=True)
                changed = next(i for i in after.items if i.product.id == self.ids["CEM"])
                self.assertEqual(after.computation_status, "ready")
                self.assertEqual(changed.forecast, original.forecast)
                self.assertEqual(changed.evaluation, original.evaluation)
                self.assertEqual(changed.product.current_stock, old_stock + 5)
                self.assertEqual(changed.pending_supply, original.pending_supply - 1)
                self.assertEqual(changed.recommendation.inventory_position,
                                 changed.product.current_stock + changed.pending_supply)
                self.assertEqual(manager.cache.computed, computed)
            finally:
                db.session.get(Product, self.ids["CEM"]).current_stock = old_stock
                PurchaseOrderLine.query.filter_by(product_id=self.ids["CEM"]).first().quantity_received = old_received
                db.session.commit()
                db.session.remove()

    def test_reference_routes_do_not_schedule_live_work(self):
        self.app.debug = True
        try:
            with patch.object(jobs, "request_predictions", side_effect=AssertionError("reference scheduled LIVE work")):
                self.assertEqual(self.clients[0].get("/predictive/demand?ref=1").status_code, 200)
                self.assertEqual(self.clients[0].get("/api/predictions/export/demand_forecast?ref=1").status_code, 404)
        finally:
            self.app.debug = False
        with patch.object(analysis, "analyze", side_effect=AssertionError("disabled reference read LIVE data")):
            self.assertEqual(self.clients[0].get("/predictive/demand?ref=1").status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
