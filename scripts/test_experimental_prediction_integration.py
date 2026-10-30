"""Additive pure DTO integration on synthetic fixtures only."""
from datetime import date, timedelta
import json
import copy
from types import SimpleNamespace
import unittest

from experimental_prediction_tools import demo, load_service
from run_experimental_predictive_regressions import offline_guard


def dates(start, count):
    return [start + timedelta(days=i) for i in range(count)]


class ExperimentalIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.evaluation = load_service("experimental_evaluation_service")
        self.training_dates = dates(date(2024, 1, 1), 4)
        self.target_dates = dates(date(2024, 1, 5), 2)

    def test_legacy_wape_preserved_and_train_only_scales_added(self):
        result = self.evaluation.evaluate_forecast([1, 2, 1, 2], [2, 2], [1, 2],
            training_dates=self.training_dates, target_dates=self.target_dates)
        self.assertEqual(result["legacy_metrics"]["wape"], 25)
        self.assertEqual(result["scaled_metrics"]["mase"]["value"], .5)
        self.assertAlmostEqual(result["scaled_metrics"]["rmsse"]["value"], .5 ** .5)
        self.assertEqual(result["scientific_status"], "infrastructure_only")
        json.dumps(result, allow_nan=False)

    def test_zero_scale_unavailable_does_not_replace_wape(self):
        result = self.evaluation.evaluate_forecast([1] * 4, [2, 2], [1, 1],
            training_dates=self.training_dates, target_dates=self.target_dates)
        self.assertEqual(result["legacy_metrics"]["wape"], 50)
        self.assertFalse(result["scaled_metrics"]["mase"]["available"])
        self.assertIsNone(result["scaled_metrics"]["mase"]["value"])
        json.dumps(result, allow_nan=False)

    def test_gap_or_post_origin_training_rejected(self):
        for targets in (dates(date(2026, 1, 1), 2), self.training_dates[-2:]):
            with self.assertRaises(ValueError):
                self.evaluation.evaluate_forecast([1, 2, 1, 2], [2, 2], [1, 2],
                    training_dates=self.training_dates, target_dates=targets)
        with self.assertRaises(ValueError):
            self.evaluation.evaluate_forecast([1, 2, 1, 2], [2, 2], [1, 2],
                training_dates=self.training_dates, target_dates=[self.target_dates[0], self.target_dates[1] + timedelta(days=1)])

    def test_adida_bucket_required_and_targets_cannot_change_forecast(self):
        starts = dates(date(2024, 1, 1), 14)
        targets = dates(date(2024, 1, 15), 7)
        one = self.evaluation.evaluate_adida([1] * 14, [1] * 7, training_dates=starts,
                                           target_dates=targets, aggregation_size=7)
        two = self.evaluation.evaluate_adida([1] * 14, [99] * 7, training_dates=starts,
                                           target_dates=targets, aggregation_size=7)
        self.assertEqual(one["adida"], two["adida"])
        self.assertFalse(one["selection_performed"])
        self.assertNotEqual(one["metrics"]["legacy_metrics"], two["metrics"]["legacy_metrics"])
        with self.assertRaises(TypeError):
            self.evaluation.evaluate_adida([1] * 14, [1] * 7, training_dates=starts, target_dates=targets)

    def test_unavailable_adida_retained_without_fake_metrics(self):
        result = self.evaluation.evaluate_adida([1] * 4, [1] * 2,
            training_dates=self.training_dates, target_dates=self.target_dates, aggregation_size=7)
        self.assertFalse(result["adida"]["available"])
        self.assertIsNone(result["metrics"])

    def test_demo_reproducible_and_marked_fixture(self):
        one, two = demo(), demo()
        self.assertEqual(one, two)
        self.assertEqual(one["purpose"], "DEMO_fixture_only")
        self.assertFalse(one["scientific_evidence"])
        json.dumps(one, allow_nan=False)

    def test_empirical_fold_adapter_is_additive_and_never_reads_later_demand(self):
        legacy = load_service("predictive_metrics_service").compute([2, 2], [1, 2])
        days = [date(2024, 1, day) for day in (1, 2, 4, 5, 8, 9, 10)]
        series = SimpleNamespace(product_id=1, starts=days, values=[1, 2, 1, 2, 2, 2, 9],
                                 time_unit="operational_day", granularity="daily")
        row = SimpleNamespace(product_id=1, model="naive", origin=4, origin_date=days[3], train_size=4,
                              horizon=2, target_dates=days[4:6], actual=[2, 2], predicted=[1, 2],
                              status="SUCCESS", metrics=legacy)
        before = copy.deepcopy(row.__dict__)
        one = self.evaluation.supplement_empirical_fold(series, row, operational_calendar=days)
        series.values[6] = float("nan")
        two = self.evaluation.supplement_empirical_fold(series, row, operational_calendar=days)
        self.assertEqual(one, two)
        self.assertEqual(one["legacy_metrics"], legacy.to_dict())
        self.assertEqual(one["scaled_metrics"]["mase"]["value"], .5)
        self.assertEqual(row.__dict__, before)
        json.dumps(one, allow_nan=False)

    def test_empirical_fold_gap_2025_cannot_enter_scale(self):
        starts = [date(2024, 12, 30), date(2024, 12, 31)]
        targets = [date(2026, 1, 1)]
        row = SimpleNamespace(product_id=1, model="naive", origin=2, origin_date=starts[-1], train_size=2,
                              horizon=1, target_dates=targets, actual=[2], predicted=[1],
                              status="SUCCESS", metrics=None)
        series = SimpleNamespace(product_id=1, starts=starts + targets, values=[1, 2, 2],
                                 time_unit="operational_day", granularity="daily")
        with self.assertRaises(ValueError):
            self.evaluation.supplement_empirical_fold(series, row, operational_calendar=starts + targets)
        row.status = "MODEL_UNAVAILABLE"
        result = self.evaluation.supplement_empirical_fold(series, row, operational_calendar=[])
        self.assertIsNone(result["scaled_metrics"])
        self.assertEqual(result["unavailable_reason"]["code"], "source_fold_unavailable")

    def test_empirical_product_identity_and_lengths_cannot_mix_scales(self):
        row = SimpleNamespace(product_id=1, model="naive", origin=1, status="SUCCESS", metrics=None)
        for product, values in ((2, [1]), (1, [])):
            source = SimpleNamespace(product_id=product, starts=[date(2024, 1, 1)], values=values)
            with self.assertRaisesRegex(ValueError, "identity"):
                self.evaluation.supplement_empirical_fold(source, row, operational_calendar=[])

    def test_regression_guard_blocks_connections_and_private_paths(self):
        for event in ("socket.connect", "socket.getaddrinfo", "sqlite3.connect"):
            with self.assertRaises(RuntimeError):
                offline_guard(event, ())
        for path in (".env", ".ENV", ".env.local", "instance/example.sqlite", "uploads/example.csv", "private_imports/example.csv"):
            with self.assertRaises(RuntimeError):
                offline_guard("open", (path, "r", 0))
        offline_guard("open", ("scripts/test_scaled_errors.py", "r", 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
