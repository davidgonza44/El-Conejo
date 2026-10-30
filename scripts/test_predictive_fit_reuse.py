"""LIVE fit reuse contracts on synthetic series, without app/DB/network access.

No application factory, user files, experimental datasets or persisted caches are
used. ARIMA uses deterministic doubles; Random Forest also has a real equivalence
check. Run: .\\venv312\\Scripts\\python.exe scripts/test_predictive_fit_reuse.py
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import os
from pathlib import Path
import random
import sys
from threading import Event
import unittest
from unittest.mock import patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import backtest_service as backtest
from app.services import forecast_advanced_service as advanced
from app.services import forecast_service as forecast
from app.services import predictive_cache_service as cache
from app.services.predictive_data_service import TimeSeries


def sample_series(size=180, granularity="daily"):
    rng = random.Random(71)
    days = 1 if granularity == "daily" else 7
    return TimeSeries(1, granularity,
                      [date(2026, 1, 5) + timedelta(days=days * i) for i in range(size)],
                      [float(2 + i % 7) + rng.random() for i in range(size)])


class ArimaDouble:
    calls = []
    fail = False

    def __init__(self, history, order):
        self.history = list(history)
        self.order = order

    def fit(self, **kwargs):
        type(self).calls.append((tuple(self.history), self.order))
        if self.fail:
            raise ValueError("Controlled numerical failure")
        self.aic = float(advanced.ARIMA_ORDERS.index(self.order))
        self.mle_retvals = {"converged": True}
        return self

    def forecast(self, steps):
        return [self.history[-1] + i / 10 for i in range(steps)]


class FitReuseTests(unittest.TestCase):
    def setUp(self):
        self.series = sample_series(75)
        self.state = cache._Predictions()
        self.predict = cache.cached_predictor(self.state, self.series.product_id)
        ArimaDouble.calls = []
        ArimaDouble.fail = False

    def test_arima_six_orders_once_for_three_horizons(self):
        with patch.object(advanced, "ARIMA", ArimaDouble):
            paths = [self.predict(self.series.values, h, "arima", starts=self.series.starts)
                     for h in forecast.HORIZONS_DAYS]
        self.assertEqual([order for _, order in ArimaDouble.calls], list(advanced.ARIMA_ORDERS))
        self.assertEqual(paths[0], paths[2][:7])
        self.assertEqual(paths[1], paths[2][:30])
        self.assertEqual(len(self.state.fits), 1)
        self.assertEqual(self.state.computed, 3)

    def test_real_forest_reuses_fit_with_exact_forecasts(self):
        expected = {h: forecast.predict(self.series.values, h, "random_forest", starts=self.series.starts)
                    for h in forecast.HORIZONS_DAYS}
        with patch.object(advanced, "_fit_random_forest", wraps=advanced._fit_random_forest) as fitted:
            actual = {h: self.predict(self.series.values, h, "random_forest", starts=self.series.starts)
                      for h in forecast.HORIZONS_DAYS}
        self.assertEqual(actual, expected)  # Exact equality, not a relaxed tolerance.
        self.assertEqual(fitted.call_count, 1)
        self.assertEqual(len(self.state.fits), 1)
        estimator = next(iter(self.state.fits.values()))
        self.assertEqual((estimator.n_estimators, estimator.max_depth, estimator.random_state), (50, 6, 42))

    def test_fit_identity_invalidates_all_training_inputs(self):
        def predict(product=1, values=None, starts=None, granularity="daily"):
            return cache.cached_predictor(self.state, product)(
                self.series.values if values is None else values, 7, "arima", granularity,
                starts=self.series.starts if starts is None else starts)

        with patch.object(advanced, "ARIMA", ArimaDouble):
            predict()
            predict()
            self.assertEqual(len(ArimaDouble.calls), 6)
            predict(product=2)
            predict(values=[self.series.values[0] + 1] + self.series.values[1:])
            predict(starts=[d + timedelta(days=1) for d in self.series.starts])
            predict(granularity="weekly")
            with patch.object(advanced, "ARIMA_ORDERS", tuple(reversed(advanced.ARIMA_ORDERS))):
                predict()
            sources, packages = cache._implementation()
            with patch.object(cache, "_implementation", return_value=(sources + ["new-source"], packages)):
                predict()
            with patch.object(cache, "_implementation", return_value=(sources, packages + [("model-version", "next")])):
                predict()
            self.assertEqual(len(ArimaDouble.calls), 8 * 6)
            predict()
            self.assertEqual(len(ArimaDouble.calls), 8 * 6)

    def test_models_do_not_share_estimators(self):
        with patch.object(advanced, "ARIMA", ArimaDouble):
            self.predict(self.series.values, 7, "arima", starts=self.series.starts)
            self.predict(self.series.values, 7, "random_forest", starts=self.series.starts)
        self.assertEqual(len(self.state.fits), 2)
        self.assertEqual({type(value) for value in self.state.fits.values()},
                         {ArimaDouble, advanced.RandomForestRegressor})

    def test_failed_fit_is_not_retained_and_read_only_never_fits(self):
        with patch.object(advanced, "ARIMA", ArimaDouble):
            readonly = cache.cached_predictor(self.state, 1, read_only=True)
            with self.assertRaises(cache.PendingPrediction):
                readonly(self.series.values, 7, "arima", starts=self.series.starts)
            self.assertEqual(ArimaDouble.calls, [])
            ArimaDouble.fail = True
            with self.assertRaises(advanced.ModelUnavailable):
                self.predict(self.series.values, 7, "arima", starts=self.series.starts)
            self.assertFalse(self.state.fits)
            ArimaDouble.fail = False
            self.assertEqual(len(self.predict(self.series.values, 7, "arima", starts=self.series.starts)), 7)
            self.assertEqual(len(ArimaDouble.calls), 12)

    def test_concurrent_fit_single_flight_and_bound(self):
        started, release = Event(), Event()
        calls, fitted = [], object()

        def compute():
            calls.append(1)
            started.set()
            if not release.wait(5):
                raise TimeoutError("Test failed to release fit")
            return fitted

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.state.fitted, "same", compute)
            self.assertTrue(started.wait(5))
            second = pool.submit(self.state.fitted, "same", compute)
            release.set()
            self.assertIs(first.result(5), fitted)
            self.assertIs(second.result(5), fitted)
        self.assertEqual(calls, [1])
        with patch.object(cache, "_MAX_FITS", 2):
            for key in range(3):
                self.state.fitted(key, object)
        self.assertEqual(list(self.state.fits), [1, 2])

    def test_empirical_operational_calendar_bypasses_live_fit_cache(self):
        future = [self.series.starts[-1] + timedelta(days=i + 1) for i in range(7)]
        with patch.object(advanced, "_fit_random_forest", wraps=advanced._fit_random_forest) as fitted:
            paths = [self.predict(self.series.values, 7, "random_forest", starts=self.series.starts,
                                  operational=True, future_starts=future) for _ in range(2)]
        self.assertEqual(paths[0], paths[1])
        self.assertEqual(fitted.call_count, 2)
        self.assertFalse(self.state.fits)
        self.assertFalse(self.state.values)


class ScientificContractsTests(unittest.TestCase):
    def test_bounded_rf_features_equal_full_prefix_features_exactly(self):
        for granularity in ("daily", "weekly"):
            with self.subTest(granularity=granularity):
                series = sample_series(230, granularity)
                first = max(advanced.RF_LAGS[granularity])
                before = ([advanced.rf_features(series.values[:i], series.starts[i], granularity)
                           for i in range(first, len(series.values))], list(series.values[first:]))
                self.assertEqual(advanced.supervised_rows(series.values, series.starts, granularity), before)

    def test_each_horizon_keeps_its_folds_metrics_and_winner(self):
        # A variance shift is deliberately constructed so H7 and H90 have different
        # winners. The test would fail if a winner/evaluation were reused blindly.
        series = sample_series(180)
        series.values = [5.0] * 90 + [0.0, 20.0] * 45
        candidates = ("naive", "mean", "moving_average", "linear_trend")
        state = cache._Predictions()
        predictor = cache.cached_predictor(state, series.product_id)
        winners, origins = {}, {}
        for horizon in forecast.HORIZONS_DAYS:
            before = backtest.evaluate_models(series, horizon, 30, candidates=candidates)
            after = backtest.evaluate_models(series, horizon, 30, candidates=candidates, predictor=predictor)
            self.assertEqual(after, before)
            winners[horizon] = after.best
            origins[horizon] = [fold.origin for fold in after.best_result.folds]
            self.assertEqual(origins[horizon], backtest.origins(series.periods, horizon, 30))
            for name in candidates:
                self.assertEqual(after.results[name].metrics, before.results[name].metrics)
        self.assertGreater(len(set(winners.values())), 1, winners)
        self.assertNotEqual(origins[7], origins[30])
        self.assertNotEqual(origins[30], origins[90])


if __name__ == "__main__":
    unittest.main(verbosity=2)
