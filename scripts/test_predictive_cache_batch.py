"""Numeric cache batching on temporary SQLite, synthetic values and no app factory.

Never reads .env, user files, business databases or experimental artifacts.
Run: .\\venv312\\Scripts\\python.exe scripts/test_predictive_cache_batch.py
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, timedelta
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import forecast_service as forecast
from app.services import predictive_cache_service as cache


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = cache._Predictions(self.root)

    def test_twenty_paths_use_one_transaction_and_hot_reads_do_not_write(self):
        statements = []
        connect = self.state.disk.connect

        def traced_connect():
            connection = connect()
            connection.set_trace_callback(statements.append)
            return connection

        with patch.object(self.state.disk, "connect", side_effect=traced_connect):
            for i in range(20):
                self.state.get(f"unbatched-{i}", lambda: [2.0], 1)
            self.assertEqual(sum(sql == "COMMIT" for sql in statements), 20)
            statements.clear()
            with self.state.batch():
                for i in range(20):
                    self.state.get(f"batched-{i}", lambda: [3.0], 1)
            self.assertEqual(sum(sql == "BEGIN " for sql in statements), 1)
            self.assertEqual(sum(sql == "COMMIT" for sql in statements), 1)
            statements.clear()
            for i in range(20):
                self.assertEqual(self.state.disk.read(f"batched-{i}", 1), (3.0,))
            self.assertFalse(any(sql.startswith(("UPDATE", "INSERT", "DELETE", "BEGIN", "COMMIT"))
                                 for sql in statements))

    def test_nested_batches_flush_once_and_restart_recovers_paths(self):
        with patch.object(self.state.disk, "write_many", wraps=self.state.disk.write_many) as writes:
            with self.state.batch():
                self.state.get("first", lambda: [1.0], 1)
                with self.state.batch():
                    self.state.get("second", lambda: [2.0], 1)
                self.assertEqual(writes.call_count, 0)
                self.assertIsNone(self.state.disk.read("first", 1))
            self.assertEqual(writes.call_count, 1)
        restart = cache._Predictions(self.root)
        for key, value in (("first", 1.0), ("second", 2.0)):
            self.assertEqual(restart.get(key, lambda: self.fail("Unexpected recomputation"), 1), [value])
        self.assertEqual(restart.computed, 0)
        self.assertEqual(restart.hits, 2)

    def test_successes_flush_when_later_model_raises(self):
        def unavailable():
            raise forecast.ModelUnavailable("Controlled failure")

        with self.assertRaises(forecast.ModelUnavailable):
            with self.state.batch():
                self.state.get("success", lambda: [4.0], 1)
                self.state.get("failure", unavailable, 1)
        self.assertEqual(self.state.disk.read("success", 1), (4.0,))
        self.assertIsNone(self.state.disk.read("failure", 1))
        self.assertFalse(self.state.pending)

    def test_flush_errors_propagate_and_next_batch_is_independent(self):
        with patch.object(self.state.disk, "write_many", side_effect=sqlite3.OperationalError("synthetic")):
            with self.assertRaises(sqlite3.OperationalError):
                with self.state.batch():
                    self.state.get("failed-flush", lambda: [5.0], 1)
        with self.state.batch():
            self.state.get("next-batch", lambda: [6.0], 1)
        self.assertIsNone(self.state.disk.read("failed-flush", 1))
        self.assertEqual(self.state.disk.read("next-batch", 1), (6.0,))

    def test_buffer_is_bounded_and_only_keeps_numeric_successes(self):
        sizes = []
        write_many = self.state.disk.write_many

        def record(values):
            sizes.append(len(values))
            write_many(values)

        with patch.object(cache, "_MAX_ENTRIES", 2), patch.object(self.state.disk, "write_many", side_effect=record):
            with self.state.batch():
                for i in range(5):
                    self.state.get(str(i), lambda: [float(i)], 1)
                self.state.get("invalid", lambda: [float("nan")], 1)
                self.state.get("nonpersistent", lambda: [7.0], 1, persistent=False)
        self.assertEqual(sizes, [2, 2, 1])
        self.assertIsNone(self.state.disk.read("invalid", 1))
        self.assertIsNone(self.state.disk.read("nonpersistent", 1))
        with closing(self.state.disk.connect()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM predictions").fetchone()[0], 2)

    def test_batch_buffers_are_isolated_between_threads(self):
        buffered, release = Event(), Event()

        def background_batch():
            with self.state.batch():
                self.state.get("background", lambda: [8.0], 1)
                buffered.set()
                if not release.wait(5):
                    raise TimeoutError("Test did not release batch")

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(background_batch)
            try:
                self.assertTrue(buffered.wait(5))
                self.state.get("foreground", lambda: [9.0], 1)
                self.assertEqual(self.state.disk.read("foreground", 1), (9.0,))
                self.assertIsNone(self.state.disk.read("background", 1))
            finally:
                release.set()
            future.result(5)
        self.assertEqual(self.state.disk.read("background", 1), (8.0,))

    def test_batched_predictor_preserves_content_invalidation_after_restart(self):
        history = [2.0, 3.0, 4.0]
        starts = [date(2026, 1, 1) + timedelta(days=i) for i in range(3)]
        predictor = cache.cached_predictor(self.state, 1)
        with self.state.batch():
            expected = {h: predictor(history, h, "mean", starts=starts) for h in (7, 30, 90)}
        restart = cache._Predictions(self.root)
        predictor = cache.cached_predictor(restart, 1)
        for horizon, path in expected.items():
            self.assertEqual(predictor(history, horizon, "mean", starts=starts), path)
        self.assertEqual(restart.computed, 0)
        with restart.batch():
            self.assertEqual(predictor([3.0, 3.0, 4.0], 7, "mean", starts=starts), [10 / 3] * 7)
            cache.cached_predictor(restart, 2)(history, 7, "mean", starts=starts)
            predictor(history, 7, "mean", starts=[d + timedelta(days=1) for d in starts])
            predictor(history, 7, "naive", starts=starts)
            with patch.dict(forecast.WINDOW, daily=8):
                predictor(history, 7, "mean", starts=starts)
        self.assertEqual(restart.computed, 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
