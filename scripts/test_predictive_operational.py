"""Synthetic protocol contracts; no app creation, database, CSV or network access."""
import os
import sys
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import demand_data_service as demand, predictive_data_service as data
from app.services import forecast_advanced_service as advanced, forecast_service as forecast
from app.services import empirical_backtest_service as empirical, predictive_metrics_service as metrics
from app.services import replenishment_service as replenishment, predictive_analysis_service as analysis
from app.services import purchase_service
from app.services.exceptions import ValidationError


def calendar(size=170):
    start = date(2026, 1, 1)
    # Fixture policy only: Sundays and one extraordinary midweek closure are excluded.
    days = [start + timedelta(days=i) for i in range(250)]
    return [d for d in days if d.weekday() != 6 and d != date(2026, 1, 14)][:size]


def series(size=151, offset=0, is_service=False):
    days = calendar(size)
    return data.aggregate_daily(1, [(d, 1.0 + i % 5) for i, d in enumerate(days[offset:])],
                                "daily", operational_calendar=days, is_service=is_service)


def spy_predict(train, horizon, model, granularity, starts=None, **kwargs):
    return [float(train[-1])] * horizon


class CalendarTests(unittest.TestCase):
    def test_closures_and_monday_zero_and_left_censoring(self):
        days = [date(2026, 1, d) for d in (2, 3, 5, 7, 8)]
        s = data.aggregate_daily(1, [(days[1], 2), (days[3], 4), (days[3], 1)],
                                 "daily", operational_calendar=days)
        self.assertEqual(s.starts, days[1:])
        self.assertEqual(s.values, [2, 0, 5, 0])
        self.assertEqual(s.operational_index, [1, 2, 3, 4])
        self.assertEqual(s.time_unit, "operational_day")

    def test_empty_and_zero_only_remain_unobserved(self):
        for points in ([], [(calendar()[0], 0)]):
            self.assertEqual(data.aggregate_daily(1, points, "daily", operational_calendar=calendar()).values, [])

    def test_cutoff_does_not_extend_or_observe_future(self):
        days = calendar(5)
        s = data.aggregate_daily(1, [(days[1], 2), (days[4], 4)], "daily",
                                 end=days[2], operational_calendar=days)
        self.assertEqual(s.starts, days[1:3])
        self.assertEqual(s.values, [2, 0])

    def test_bad_calendars_and_closed_event_rejected(self):
        days = calendar(5)
        for bad in (days[::-1], days + [days[-1]], ["2026-01-01"]):
            with self.assertRaises(ValueError):
                data.aggregate_daily(1, [], "daily", operational_calendar=bad)
        with self.assertRaises(ValueError):
            data.aggregate_daily(1, [(date(2026, 1, 4), 1)], "daily", operational_calendar=days)

    def test_event_builder_uses_same_pure_calendar_without_database(self):
        days = calendar(6)
        event = demand.DemandEvent(1, days[2], Decimal(3), Decimal(3), "operational", "sale", True)
        with patch.object(demand, "get_temporal_cutoff", side_effect=AssertionError("database access")):
            result = demand.build_daily_series_by_product([event], operational_calendar=days)[1]
        self.assertEqual([p.day for p in result.points], days[2:])
        self.assertEqual([p.demand for p in result.points], [3, 0, 0, 0])

    def test_legacy_daily_defaults_unchanged(self):
        s = data.aggregate_daily(1, [(date(2026, 1, 3), 2), (date(2026, 1, 5), 4)], "daily")
        self.assertEqual(s.values, [2, 0, 4])
        self.assertEqual(s.time_unit, "calendar_day")


class ModelTests(unittest.TestCase):
    def test_rf_operational_dates_and_recursive_history(self):
        days = calendar(75)
        s = series(60)
        estimator = Mock()
        estimator.predict.side_effect = [[101], [102], [103]]
        future = days[60:63]
        with patch.object(advanced, "RandomForestRegressor", return_value=estimator) as factory:
            path = forecast.predict(s.values, 3, "random_forest", starts=s.starts,
                                    operational=True, future_starts=future)
        self.assertEqual(path, [101, 102, 103])
        factory.assert_called_once_with(n_estimators=50, max_depth=6, random_state=42, n_jobs=1)
        rows = [call.args[0][0] for call in estimator.predict.call_args_list]
        self.assertEqual([r[0] for r in rows], [s.values[-1], 101, 102])
        self.assertEqual([r[-2:] for r in rows], [[d.weekday(), d.month] for d in future])
        self.assertEqual(estimator.fit.call_args.args[1], s.values[28:])

    def test_saturday_monday_and_extraordinary_closure_accepted(self):
        days = calendar(70)
        self.assertIn(date(2026, 1, 3), days)
        self.assertIn(date(2026, 1, 5), days)
        self.assertNotIn(date(2026, 1, 14), days)
        self.assertTrue(advanced.valid_starts(days, 70, "daily", operational=True))
        self.assertFalse(advanced.valid_starts(days, 70, "daily"))

    def test_rf_requires_explicit_valid_future_dates(self):
        s = series(60)
        for future in (None, [], s.starts[-3:], calendar(70)[60:62], calendar(70)[60:63][::-1]):
            with self.assertRaises(advanced.ModelUnavailable):
                forecast.predict(s.values, 3, "random_forest", starts=s.starts,
                                 operational=True, future_starts=future)

    def test_historical_features_invariant_to_target_and_future(self):
        s = series(70)
        changed = s.values[:40] + [999] * 30
        one = advanced.supervised_rows(s.values, s.starts, "daily")[0]
        two = advanced.supervised_rows(changed, s.starts, "daily")[0]
        self.assertEqual(one[:13], two[:13])  # Includes row whose target is position 40.

    def test_arima_preserves_orders_train_only_and_no_seasonal_argument(self):
        s = series(60)
        fit = Mock(aic=1, mle_retvals={"converged": True})
        fit.forecast.return_value = [2] * 7
        with patch.object(advanced, "ARIMA", return_value=Mock(fit=Mock(return_value=fit))) as factory:
            self.assertEqual(forecast.predict(s.values, 7, "arima", starts=s.starts,
                                             operational=True, future_starts=calendar(67)[60:]), [2] * 7)
        self.assertEqual([c.kwargs for c in factory.call_args_list],
                         [{"order": order} for order in advanced.ARIMA_ORDERS])
        self.assertTrue(all(c.args[0] == s.values for c in factory.call_args_list))

    def test_actual_synthetic_arima_and_rf_on_operational_sequence(self):
        s = series(67)
        for model in ("arima", "random_forest"):
            result = empirical.evaluate_operational(s, operational_calendar=calendar(67),
                                                   explicit_origins=[60], candidates=(model,))
            self.assertEqual(result.rows[0].status, empirical.SUCCESS)
            self.assertEqual(len(result.rows[0].predicted), 7)

    def test_forecast_rate_unit_requires_operational_future(self):
        s = series(60)
        with self.assertRaises(advanced.ModelUnavailable):
            forecast.forecast(s, 7, "naive")
        fc = forecast.forecast(s, 7, "naive", future_starts=calendar(67)[60:])
        self.assertEqual(fc.rate_unit, "operational_day")


class PanelTests(unittest.TestCase):
    def test_default_and_secondary_nonoverlapping_grids(self):
        self.assertEqual(empirical.operational_origins(calendar(90)), [60, 67, 74, 81])
        self.assertEqual(empirical.operational_origins(calendar(150), 30), [60, 90, 120])
        with self.assertRaises(ValueError):
            empirical.operational_origins(calendar(), 7, 1)

    def test_six_models_same_train_dates_and_three_common_folds(self):
        s = series(81)
        with patch.object(forecast, "predict", side_effect=spy_predict) as spy:
            result = empirical.evaluate_operational(s, operational_calendar=calendar(81), product_code="SYNTHETIC")
        self.assertEqual(len(result.rows), 18)
        self.assertEqual(result.valid_common_folds, 3)
        self.assertTrue(result.individual_comparison_publishable)
        for origin in result.origins:
            calls = [c for c in spy.call_args_list if len(c.args[0]) == origin]
            self.assertEqual(len(calls), 6)
            self.assertTrue(all(c.args[0] == s.values[:origin] for c in calls))
            self.assertTrue(all(c.kwargs["future_starts"] == s.starts[origin:origin + 7] for c in calls))
        self.assertTrue(all(row.product_code == "SYNTHETIC" for row in result.rows))

    def test_future_changes_do_not_change_fold_eligibility_or_prediction(self):
        original, changed = series(90), series(90)
        changed.values[60:] = [999] * 21 + [float("nan")] * 9
        for model in forecast.MODELS:
            # ARIMA/RF are covered by their pure train calls above; test orchestration with a spy.
            with patch.object(forecast, "predict", side_effect=spy_predict):
                a = empirical.evaluate_operational(original, operational_calendar=calendar(90),
                                                   explicit_origins=[60], candidates=(model,)).rows[0]
                b = empirical.evaluate_operational(changed, operational_calendar=calendar(90),
                                                   explicit_origins=[60], candidates=(model,)).rows[0]
            self.assertEqual((a.status, a.train_size, a.positives_train, a.predicted),
                             (b.status, b.train_size, b.positives_train, b.predicted))

    def test_global_origins_preserved_for_left_censored_products(self):
        early, late = series(90), series(90, offset=10)
        with patch.object(forecast, "predict", side_effect=spy_predict):
            a = empirical.evaluate_operational(early, operational_calendar=calendar(90), explicit_origins=[60, 74])
            b = empirical.evaluate_operational(late, operational_calendar=calendar(90), explicit_origins=[60, 74])
        self.assertEqual(a.rows[0].target_dates, b.rows[0].target_dates)
        self.assertEqual(b.rows[0].train_size, 50)
        self.assertEqual(b.rows[0].status, empirical.INELIGIBLE)
        self.assertEqual(b.rows[-1].train_size, 64)
        self.assertEqual(b.valid_common_folds, 1)
        self.assertFalse(b.individual_comparison_publishable)

    def test_secondary_thirty_day_panel(self):
        with patch.object(forecast, "predict", side_effect=spy_predict):
            result = empirical.evaluate_operational(series(150), operational_calendar=calendar(150),
                                                   horizon=30, step=30)
        self.assertEqual(result.origins, [60, 90, 120])
        self.assertEqual(result.valid_common_folds, 3)
        self.assertTrue(all(len(row.actual) == len(row.predicted) == 30 for row in result.rows))

    def test_train_positive_and_constant_constraints(self):
        self.assertEqual(empirical.train_only_eligibility([0] * 49 + [1] * 11),
                         "insufficient_train_positive_observations")
        self.assertIsNone(empirical.train_only_eligibility([0] * 48 + [1] * 12))
        s = series(67)
        s.values = [2] * 67
        with patch.object(forecast, "predict", side_effect=spy_predict):
            result = empirical.evaluate_operational(s, operational_calendar=calendar(67))
        arima = next(row for row in result.rows if row.model == "arima")
        self.assertEqual(arima.reason, "model_train_requirements")
        self.assertEqual(result.valid_common_folds, 0)
        self.assertEqual(len(result.rows), 6)

    def test_failures_keep_ledger_and_common_support(self):
        def fail(train, horizon, model, *args, **kwargs):
            if model == "arima" and len(train) == 60:
                raise advanced.ModelUnavailable("internal private detail")
            if model == "random_forest" and len(train) == 67:
                raise RuntimeError("internal private detail")
            return [2] * horizon
        with patch.object(forecast, "predict", side_effect=fail):
            result = empirical.evaluate_operational(series(81), operational_calendar=calendar(81))
        self.assertEqual(result.common_origins, [74])
        self.assertEqual(len(result.rows), 18)
        self.assertEqual(result.common_metrics("naive").n, 7)
        self.assertEqual(result.coverage("arima")["counts"][empirical.MODEL_UNAVAILABLE], 1)
        self.assertEqual(result.coverage("random_forest")["failure_rate"], 1 / 3)
        self.assertFalse(any("private" in (r.reason or "") for r in result.rows))
        self.assertTrue(all(not r.predicted for r in result.rows if r.status != empirical.SUCCESS))

    def test_zero_tests_retained_and_aggregated_from_pairs(self):
        s = series(74)
        s.values[60:67] = [0] * 7
        with patch.object(forecast, "predict", return_value=[2] * 7):
            result = empirical.evaluate_operational(s, operational_calendar=calendar(74))
        row = result.rows[0]
        self.assertEqual((row.metrics.mae, row.metrics.rmse, row.metrics.bias), (2, 2, 2))
        self.assertIsNone(row.metrics.wape)
        self.assertIsNone(row.metrics.mape)
        self.assertEqual(result.valid_common_folds, 2)
        self.assertEqual(empirical.pooled_common_metrics([result], "naive").n, 14)
        self.assertIsNotNone(result.common_metrics("naive").wape)

    def test_nonfinite_input_and_prediction_are_recorded_not_zero(self):
        for bad in (float("nan"), float("inf")):
            for position in (3, 63):
                s = series(67)
                s.values[position] = bad
                result = empirical.evaluate_operational(s, operational_calendar=calendar(67), candidates=("naive",))
                self.assertEqual(result.rows[0].status, empirical.ERROR)
                self.assertEqual(result.coverage("naive")["eligible_folds"], int(position >= 60))
            with patch.object(forecast, "predict", return_value=[bad] * 7):
                result = empirical.evaluate_operational(series(67), operational_calendar=calendar(67))
            self.assertTrue(all(r.status == empirical.ERROR and r.predicted == [] for r in result.rows))
            with self.assertRaises(ValueError):
                metrics.compute([1], [bad])

    def test_explicit_invalid_grids_fail_before_any_model(self):
        with patch.object(forecast, "predict", side_effect=AssertionError("not allowed")):
            for origins in ([60, 61], [60, 60], [80], [True], [67, 60]):
                with self.assertRaises(ValueError):
                    empirical.evaluate_operational(series(81), operational_calendar=calendar(81), explicit_origins=origins)


class ServiceAndUnitsTests(unittest.TestCase):
    def test_service_has_forecast_without_recommendation(self):
        s = data.aggregate_daily(1, [(date(2026, 1, 1) + timedelta(days=i), 2 + i % 3)
                                    for i in range(65)], "daily", is_service=True)
        product = SimpleNamespace(is_active=True, current_stock=0, minimum_stock=2, purchase_price=1)
        with patch.object(forecast, "predict", side_effect=spy_predict):
            result = analysis.analyze_product(product, s, 7, "naive", date(2026, 4, 1))
        self.assertIsNotNone(result.forecast)
        self.assertIsNone(result.recommendation)
        self.assertIsNone(replenishment.compute(0, 2, 3, 0, Decimal(1), date.today(), is_service=True))

    def test_operational_rates_cannot_be_multiplied_by_civil_lead_time(self):
        with self.assertRaises(ValueError):
            replenishment.compute(0, 2, 3, 0, Decimal(1), date.today(), rate_unit="operational_day")
        rec = replenishment.compute(0, 2, 3, 0, Decimal(1), date.today())
        self.assertEqual(rec.lead_time_demand, 21)
        self.assertEqual(rec.lead_time_source, "assumed")

    def test_descriptive_rate_preserves_unit_and_live_rejects_operational_policy(self):
        s = series(60)
        self.assertEqual(forecast.descriptive_rate(s, 7).rate_unit, "operational_day")
        with self.assertRaises(ValueError):
            analysis.analyze_product(SimpleNamespace(), s, 7, "naive", date.today())

    def test_order_payload_rejects_input_and_authoritative_service(self):
        product = SimpleNamespace(id=1, is_active=True, is_service=False, name="Synthetic", purchase_price=1)
        query = Mock()
        query.filter.return_value.all.return_value = [product]
        fake_model = Mock(query=query)
        for marker in ("item", "trusted_ids", "product"):
            product.is_service = marker == "product"
            item = {"product_id": 1, "quantity": 1, "is_service": marker == "item"}
            with patch.object(replenishment, "Product", fake_model):
                with self.assertRaises(ValidationError):
                    replenishment.order_payload([item], 1, service_product_ids={1} if marker == "trusted_ids" else ())

    def test_purchase_stock_boundary_rejects_marked_server_product(self):
        product = SimpleNamespace(id=1, is_active=True, is_service=True)
        session = Mock()
        session.execute.return_value.scalars.return_value = [product]
        with patch.object(purchase_service, "db", SimpleNamespace(session=session)):
            with self.assertRaises(ValidationError):
                purchase_service._items([{"product_id": 1, "quantity": 1, "unit_cost": "1.00"}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
