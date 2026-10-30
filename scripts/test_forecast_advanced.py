"""Contratos avanzados deterministas, sin aplicación, BD, archivos de usuario ni red."""
from __future__ import annotations

import math
import os
import random
import sys
import unittest
import warnings
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import forecast_advanced_service as advanced
from app.services import forecast_service as forecast, backtest_service as backtest
from app.services import predictive_analysis_service as analysis
from app.services.predictive_data_service import TimeSeries


def series(values, granularity="daily"):
    step = 1 if granularity == "daily" else 7
    return TimeSeries(1, granularity,
                      [date(2025, 1, 6) + timedelta(days=i * step) for i in range(len(values))],
                      list(values))


def autoregressive_values(size=100):
    rng = random.Random(17)
    values = [20.0]
    for _ in range(size - 1):
        values.append(20 + 0.8 * (values[-1] - 20) + rng.gauss(0, 1))
    return values


class EligibilityTests(unittest.TestCase):
    def test_baselines_preserved_without_dates(self):
        for name in ("naive", "mean", "moving_average", "linear_trend"):
            self.assertEqual(len(forecast.predict([2.0] * 35, 7, name)), 7)

    def test_short_advanced_unavailable(self):
        for name in advanced.MODEL_REQUIREMENTS:
            with self.assertRaises(advanced.ModelUnavailable):
                forecast.predict([2.0] * 35, 7, name)

    def test_daily_boundary(self):
        s = series([0.0] * 48 + [2.0] * 12)
        for name in advanced.MODEL_REQUIREMENTS:
            self.assertTrue(forecast.model_eligible(s.values, name, "daily", s.starts))
            self.assertFalse(forecast.model_eligible(s.values[:-1], name, "daily", s.starts[:-1]))
            self.assertFalse(forecast.model_eligible([0.0] * 49 + [2.0] * 11, name, "daily", s.starts))

    def test_weekly_boundary(self):
        for name, size in (("arima", 16), ("random_forest", 20)):
            s = series([2.0, 3.0] * (size // 2), "weekly")
            self.assertTrue(forecast.model_eligible(s.values, name, "weekly", s.starts))
            self.assertFalse(forecast.model_eligible(s.values[:-1], name, "weekly", s.starts[:-1]))

    def test_dates_required_and_consistent(self):
        s = series([2.0] * 60)
        invalid = (None, [], s.starts[:-1], list(reversed(s.starts)), [s.starts[0]] * 60,
                   [d.isoformat() for d in s.starts])
        for starts in invalid:
            self.assertFalse(forecast.model_eligible(s.values, "random_forest", "daily", starts))
        with self.assertRaises(advanced.ModelUnavailable):
            forecast.predict(s.values, 7, "random_forest")

    def test_weekly_monday_required(self):
        s = series([2.0] * 20, "weekly")
        self.assertFalse(advanced.valid_starts([d + timedelta(days=1) for d in s.starts], 20, "weekly"))

    def test_degenerate_and_nonfinite(self):
        self.assertFalse(advanced.eligible([3.0] * 60, "arima", "daily"))
        for value in (math.nan, math.inf, -1.0):
            s = series([2.0] * 59 + [value])
            for name in advanced.MODEL_REQUIREMENTS:
                self.assertFalse(forecast.model_eligible(s.values, name, "daily", s.starts))


class ArimaTests(unittest.TestCase):
    def test_real_forecast_shape_and_nonnegative(self):
        values = autoregressive_values(70)
        for granularity in ("daily", "weekly"):
            path = forecast.predict(values, 7, "arima", granularity)
            self.assertIsInstance(path, list)
            self.assertEqual(len(path), 7)
            self.assertTrue(all(math.isfinite(v) and v >= 0 for v in path))

    def test_aic_selects_on_train_only(self):
        fits = [Mock(aic=float(6 - i), mle_retvals={"converged": True}) for i in range(6)]
        for fit in fits:
            fit.forecast.return_value = [2.0] * 7
        factory = Mock(side_effect=[Mock(fit=Mock(return_value=fit)) for fit in fits])
        values = autoregressive_values(60)
        with patch.object(advanced, "ARIMA", factory):
            self.assertEqual(forecast.predict(values, 7, "arima"), [2.0] * 7)
        self.assertEqual([c.kwargs["order"] for c in factory.call_args_list], list(advanced.ARIMA_ORDERS))
        self.assertTrue(all(c.args[0] == values for c in factory.call_args_list))
        self.assertTrue(all(f.forecast.call_count == 0 for f in fits[:-1]))
        fits[-1].forecast.assert_called_once_with(steps=7)

    def test_controlled_fit_errors(self):
        for error in (ValueError, FloatingPointError, OverflowError):
            with patch.object(advanced, "ARIMA", side_effect=error("synthetic")):
                with self.assertRaises(advanced.ModelUnavailable):
                    forecast.predict(autoregressive_values(60), 7, "arima")

    def test_convergence_warning_is_unavailable(self):
        def fail(*args, **kwargs):
            warnings.warn("synthetic", advanced.ConvergenceWarning)
        with patch.object(advanced, "ARIMA", side_effect=fail):
            with self.assertRaises(advanced.ModelUnavailable):
                forecast.predict(autoregressive_values(60), 7, "arima")

    def test_nonconverged_or_nonfinite_aic(self):
        for aic, converged in ((1.0, False), (math.nan, True), (math.inf, True)):
            fit = Mock(aic=aic, mle_retvals={"converged": converged})
            with patch.object(advanced, "ARIMA", return_value=Mock(fit=Mock(return_value=fit))):
                with self.assertRaises(advanced.ModelUnavailable):
                    forecast.predict(autoregressive_values(60), 7, "arima")

    def test_invalid_forecast_and_clipping(self):
        fit = Mock(aic=1.0, mle_retvals={"converged": True})
        with patch.object(advanced, "ARIMA", return_value=Mock(fit=Mock(return_value=fit))):
            for path in ([math.nan] * 7, [math.inf] * 7, [1.0]):
                fit.forecast.return_value = path
                with self.assertRaises(advanced.ModelUnavailable):
                    forecast.predict(autoregressive_values(60), 7, "arima")
            fit.forecast.return_value = [-2.0] * 7
            self.assertEqual(forecast.predict(autoregressive_values(60), 7, "arima"), [0.0] * 7)

    def test_other_warnings_remain_visible(self):
        fit = Mock(aic=1.0, mle_retvals={"converged": True})
        fit.forecast.return_value = [2.0] * 7
        def build(*args, **kwargs):
            warnings.warn("unrelated warning", UserWarning)
            return Mock(fit=Mock(return_value=fit))
        with warnings.catch_warnings(record=True) as seen, patch.object(advanced, "ARIMA", side_effect=build):
            warnings.simplefilter("always")
            forecast.predict(autoregressive_values(60), 7, "arima")
        self.assertEqual(len(seen), 6)


class RandomForestTests(unittest.TestCase):
    def test_daily_lags_shift_and_calendar(self):
        s = series(list(range(60)))
        x, y = advanced.supervised_rows(s.values, s.starts, "daily")
        self.assertEqual(x[0], [27.0, 21.0, 14.0, 0.0, 24.0, s.starts[28].weekday(), s.starts[28].month])
        self.assertEqual(y, list(range(28, 60)))
        changed = s.values[:28] + [99999] * 32
        self.assertEqual(x[0], advanced.supervised_rows(changed, s.starts, "daily")[0][0])

    def test_weekly_features(self):
        s = series(list(range(20)), "weekly")
        x, y = advanced.supervised_rows(s.values, s.starts, "weekly")
        self.assertEqual(x[0], [7.0, 4.0, 0.0, 5.5, s.starts[8].month])
        self.assertEqual(len(y), 12)

    def test_deterministic_and_horizons(self):
        s = series(autoregressive_values(65))
        for days in forecast.HORIZONS_DAYS:
            one = forecast.forecast(s, days, "random_forest")
            two = forecast.forecast(s, days, "random_forest")
            self.assertEqual(one.path, two.path)
            self.assertEqual(len(one.path), days)
            self.assertTrue(all(v >= 0 and math.isfinite(v) for v in one.path))

    def test_recursive_features_and_fixed_configuration(self):
        s = series(list(range(1, 61)))
        estimator = Mock()
        estimator.predict.side_effect = [[101.0], [102.0], [103.0]]
        with patch.object(advanced, "RandomForestRegressor", return_value=estimator) as factory:
            self.assertEqual(forecast.predict(s.values, 3, "random_forest", starts=s.starts), [101, 102, 103])
        factory.assert_called_once_with(n_estimators=50, max_depth=6, random_state=42, n_jobs=1)
        rows = [c.args[0][0] for c in estimator.predict.call_args_list]
        self.assertEqual([r[0] for r in rows], [60, 101, 102])
        self.assertEqual(rows[1][4], sum(s.values[-6:] + [101]) / 7)
        self.assertEqual(rows[2][-2:], [(s.starts[-1] + timedelta(days=3)).weekday(),
                                       (s.starts[-1] + timedelta(days=3)).month])
        self.assertEqual(s.values, list(range(1, 61)))

    def test_weekly_future_dates_and_fraction(self):
        s = series([2.0] * 20, "weekly")
        result = forecast.forecast(s, 30, "random_forest")
        self.assertEqual(len(result.path), 5)
        self.assertAlmostEqual(result.total, 2 * 30 / 7)
        estimator = Mock()
        estimator.predict.return_value = [2.0]
        with patch.object(advanced, "RandomForestRegressor", return_value=estimator):
            forecast.forecast(s, 30, "random_forest")
        months = [c.args[0][0][-1] for c in estimator.predict.call_args_list]
        self.assertEqual(months, [(s.starts[-1] + timedelta(weeks=i)).month for i in range(1, 6)])

    def test_fit_and_nonfinite_prediction_failure(self):
        s = series([2.0] * 60)
        estimator = Mock()
        with patch.object(advanced, "RandomForestRegressor", return_value=estimator):
            estimator.fit.side_effect = ValueError("synthetic")
            with self.assertRaises(advanced.ModelUnavailable):
                forecast.predict(s.values, 7, "random_forest", starts=s.starts)
            estimator.fit.side_effect = None
            estimator.predict.return_value = [math.nan]
            with self.assertRaises(advanced.ModelUnavailable):
                forecast.predict(s.values, 7, "random_forest", starts=s.starts)


class BacktestTests(unittest.TestCase):
    def test_weekly_common_folds_and_maximum(self):
        s = series(autoregressive_values(38), "weekly")
        with patch.object(forecast, "predict", return_value=[2.0] * 2):
            result = backtest.evaluate_models(s, 2, 8)
        expected = backtest.origins(38, 2, 20)
        self.assertLessEqual(len(expected), backtest.MAX_FOLDS)
        for candidate in result.results.values():
            self.assertEqual([fold.origin for fold in candidate.folds], expected)

    def test_positive_training_minimum_controls_common_origins(self):
        s = series([0.0] * 55 + [2.0, 3.0] * 15)
        with patch.object(forecast, "predict", return_value=[2.0] * 7):
            result = backtest.evaluate_models(s, 7, 30)
        expected = backtest.origins(85, 7, 67)  # primer TRAIN con 12 positivos
        for candidate in result.results.values():
            self.assertEqual([fold.origin for fold in candidate.folds], expected)

    def test_explicit_forecast_without_room_for_holdout(self):
        s = series([2.0, 3.0] * 30)
        product = SimpleNamespace(is_active=True, current_stock=10, minimum_stock=2, purchase_price=1)
        result = analysis.analyze_product(product, s, 90, "random_forest", date(2026, 1, 1))
        self.assertIsNone(result.evaluation)
        self.assertEqual(len(result.forecast.path), 90)
        self.assertIsNone(result.forecast.half_width)

    def test_common_folds_with_all_candidates(self):
        s = series(autoregressive_values(80))
        calls = []
        def spy(history, horizon, model, granularity, starts=None):
            calls.append((model, len(history), list(history), list(starts)))
            return [history[-1]] * horizon
        with patch.object(forecast, "predict", side_effect=spy):
            evaluation = backtest.evaluate_models(s, 7, 30)
        self.assertEqual(set(evaluation.results), set(forecast.MODELS))
        expected = backtest.origins(80, 7, 60)
        for result in evaluation.results.values():
            self.assertEqual([f.origin for f in result.folds], expected)
            self.assertEqual(result.actual, evaluation.best_result.actual)
        self.assertTrue(all(h == s.values[:n] and d == s.starts[:n] for _, n, h, d in calls))

    def test_short_series_does_not_delay_baselines(self):
        for size in (35, 65):
            s = series(autoregressive_values(size))
            result = backtest.evaluate_models(s, 7 if size == 65 else 3, 30)
            self.assertEqual(set(result.results), {"naive", "mean", "moving_average", "linear_trend"})
            self.assertEqual([f.origin for f in result.best_result.folds],
                             backtest.origins(size, 7 if size == 65 else 3, 30))

    def test_partial_failure_excludes_entire_candidate(self):
        s = series(autoregressive_values(80))
        def predict(history, horizon, model, granularity, starts=None):
            if model in advanced.MODEL_REQUIREMENTS and len(history) > 65:
                raise advanced.ModelUnavailable("synthetic fold failure")
            return [history[-1]] * horizon
        with patch.object(forecast, "predict", side_effect=predict):
            result = backtest.evaluate_models(s, 7, 30)
        self.assertEqual(set(result.results), {"naive", "mean", "moving_average", "linear_trend"})
        self.assertTrue(all(len(r.folds) == len(result.best_result.folds) for r in result.results.values()))

    def test_real_holdout_mutation_never_changes_forecast(self):
        s = series(autoregressive_values(67))
        mutated = series(s.values[:60] + [999999.0] * 7)
        for model in advanced.MODEL_REQUIREMENTS:
            a = backtest.rolling_backtest(s, model, 7, 60)
            b = backtest.rolling_backtest(mutated, model, 7, 60)
            self.assertEqual(len(a.folds), 1)
            self.assertEqual(a.folds[0].predicted, b.folds[0].predicted)
            self.assertNotEqual(a.folds[0].actual, b.folds[0].actual)

    def test_explicit_only_requested_model(self):
        s = series(autoregressive_values(70))
        product = SimpleNamespace(is_active=True, current_stock=10, minimum_stock=2, purchase_price=1)
        for model in ("naive", "random_forest", "arima"):
            with patch.object(forecast, "predict", return_value=[2.0] * 7) as predict:
                result = analysis.analyze_product(product, s, 7, model, date(2026, 1, 1))
            self.assertEqual(set(result.evaluation.results), {model})
            self.assertTrue(all(c.args[2] == model for c in predict.call_args_list))

    def test_explicit_unavailable_no_invented_forecast(self):
        s = series([2.0] * 35)
        product = SimpleNamespace(is_active=True, current_stock=10, minimum_stock=2, purchase_price=1)
        for model in advanced.MODEL_REQUIREMENTS:
            result = analysis.analyze_product(product, s, 7, model, date(2026, 1, 1))
            self.assertIsNone(result.forecast)
            self.assertIsNone(result.recommendation)

    def test_final_forecast_failure_uses_next_valid_candidate(self):
        s = series(autoregressive_values(70))
        evaluation = backtest.ModelEvaluation("arima", {
            "arima": backtest.BacktestResult("arima", 7, [backtest.Fold(60, [2], [2])]),
            "naive": backtest.BacktestResult("naive", 7, [backtest.Fold(60, [2], [3])])})
        real = forecast.forecast
        def fail(series, horizon, model, **kwargs):
            if model == "arima":
                raise advanced.ModelUnavailable("synthetic final failure")
            return real(series, horizon, model, **kwargs)
        with patch.object(forecast, "forecast", side_effect=fail):
            result = analysis._forecast_available(s, 7, "auto", evaluation)
        self.assertEqual(result.model, "naive")

    def test_wape_none_falls_back_honestly(self):
        result = backtest.evaluate_models(series([0.0] * 70), 7, 30)
        self.assertEqual(result.best, forecast.BASELINE_MODEL)
        self.assertTrue(all(r.metrics.wape is None for r in result.results.values()))

    def test_baseline_can_win(self):
        result = backtest.evaluate_models(series([4.0] * 70), 7, 30)
        self.assertEqual(result.best, "naive")
        self.assertIn("random_forest", result.results)

    def test_rf_can_win_weekday_demand_pattern(self):
        # Demanda estable por día de semana, patrón plausible de apertura/actividad comercial.
        s = series([12.0, 13.0, 14.0, 12.0, 20.0, 6.0, 2.0] * 14)
        result = backtest.evaluate_models(s, 7, 30)
        self.assertEqual(result.best, "random_forest", result.ranking())
        self.assertEqual(result.ranking()[0][1], min(r.metrics.wape for r in result.results.values()))

    def test_auto_respects_measured_wape_on_noisy_demand(self):
        s = series(autoregressive_values())
        result = backtest.evaluate_models(s, 1, 30)
        # Un proceso AR no garantiza que ARIMA gane en una muestra finita.
        self.assertEqual(result.best, "random_forest", result.ranking())
        self.assertLess(result.best_result.metrics.wape, result.results["arima"].metrics.wape)

    def test_arima_can_win_smooth_growth(self):
        # Tendencia creciente suave con ruido: prueba numérica, no histórico empresarial.
        # Se conserva la misma semilla; no se ajustan modelos ni órdenes contra el holdout.
        rng = random.Random(17)
        s = series([10 + .003 * i * i + rng.gauss(0, .2) for i in range(120)])
        result = backtest.evaluate_models(s, 7, 30)
        self.assertEqual(result.best, "arima", result.ranking())


def run_tests():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    return unittest.TextTestRunner(verbosity=2).run(suite)


if __name__ == "__main__":
    result = run_tests()
    sys.exit(not result.wasSuccessful())
