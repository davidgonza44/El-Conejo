"""Synthetic tests loading the pure metrics module without importing app."""

import importlib.util
import json
import math
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "app/services/scaled_error_service.py"
SPEC = importlib.util.spec_from_file_location("scaled_error_service_under_test", MODULE_PATH)
METRICS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(METRICS)


class ScaledErrorTests(unittest.TestCase):
    def assert_unavailable(self, result, code):
        for name in ("mase", "rmsse"):
            self.assertFalse(result[name]["available"])
            self.assertIsNone(result[name]["value"])
            self.assertEqual(result[name]["reason"]["code"], code)
        json.dumps(result, allow_nan=False)

    def test_normal_series_matches_hand_calculation(self):
        result = METRICS.compute([1, 2, 4, 7], [8, 11], [7, 13])
        self.assertAlmostEqual(result["mase"]["denominator"], 2)
        self.assertAlmostEqual(result["rmsse"]["denominator"], 14 / 3)
        self.assertAlmostEqual(result["mase"]["value"], 0.75)
        self.assertAlmostEqual(result["rmsse"]["value"], math.sqrt(15 / 28))
        self.assertTrue(result["mase"]["available"])
        self.assertIsNone(result["rmsse"]["reason"])

    def test_intermittent_series_matches_hand_calculation(self):
        result = METRICS.compute([0, 0, 4, 0, 0, 2], [0, 3, 0], [1, 2, 0])
        self.assertAlmostEqual(result["mase"]["denominator"], 2)
        self.assertAlmostEqual(result["rmsse"]["denominator"], 36 / 5)
        self.assertAlmostEqual(result["mase"]["value"], 1 / 3)
        self.assertAlmostEqual(result["rmsse"]["value"], math.sqrt(5 / 54))

    def test_all_zero_is_unavailable_even_with_perfect_forecast(self):
        result = METRICS.compute([0, 0, 0], [0, 0], [0, 0])
        self.assert_unavailable(result, "zero_training_scale")
        self.assertEqual(result["mase"]["denominator"], 0)

    def test_constant_training_is_unavailable(self):
        self.assert_unavailable(
            METRICS.compute([5, 5, 5], [7], [6]), "zero_training_scale"
        )

    def test_explicit_lag_can_have_zero_denominator(self):
        result = METRICS.compute([2, 4, 2, 4], [3], [2], m=2)
        self.assert_unavailable(result, "zero_training_scale")
        self.assertEqual(result["m"], 2)
        self.assertTrue(METRICS.compute([2, 4, 2, 4], [3], [2])["mase"]["available"])

    def test_explicit_lag_without_inference(self):
        result = METRICS.compute([1, 2, 4, 7], [8, 11], [7, 13], m=2)
        self.assertEqual(result["benchmark"]["lag"], 2)
        self.assertAlmostEqual(result["mase"]["denominator"], 4)
        self.assertAlmostEqual(result["rmsse"]["denominator"], 17)
        self.assertAlmostEqual(result["mase"]["value"], 0.375)

    def test_default_is_nonseasonal_lag_one(self):
        result = METRICS.compute([1, 2], [3], [2])
        self.assertEqual(result["m"], 1)
        self.assertEqual(result["benchmark"]["scale_source"], "training_only")

    def test_test_observations_never_change_training_scale(self):
        first = METRICS.compute([1, 2, 4, 7], [8, 11], [7, 13])
        second = METRICS.compute([1, 2, 4, 7], [999, -500], [0, 100])
        for name in ("mase", "rmsse"):
            self.assertEqual(first[name]["denominator"], second[name]["denominator"])
            self.assertNotEqual(first[name]["value"], second[name]["value"])

    def test_perfect_forecast_with_nonzero_training_scale(self):
        result = METRICS.compute([0, 2, 0], [0, 3], [0, 3])
        for name in ("mase", "rmsse"):
            self.assertTrue(result[name]["available"])
            self.assertEqual(result[name]["value"], 0)

    def test_empty_or_short_training(self):
        for train, m in (([], 1), ([1], 1), ([1, 2], 2)):
            with self.subTest(train=train, m=m):
                self.assert_unavailable(
                    METRICS.compute(train, [1], [1], m=m), "insufficient_training"
                )

    def test_empty_evaluation(self):
        self.assert_unavailable(METRICS.compute([1, 2], [], []), "empty_evaluation")

    def test_mismatched_lengths(self):
        self.assert_unavailable(METRICS.compute([1, 2], [3], []), "length_mismatch")

    def test_invalid_lags(self):
        for m in (0, -1, 1.0, True, None, "1", float("nan"), float("inf")):
            with self.subTest(m=m):
                self.assert_unavailable(METRICS.compute([1, 2], [3], [2], m=m), "invalid_m")

    def test_missing_non_numeric_and_boolean_are_not_imputed(self):
        for value in (None, "2", True):
            with self.subTest(value=value):
                result = METRICS.compute([1, value], [3], [2])
                self.assert_unavailable(result, "invalid_observation")
                self.assertEqual(result["mase"]["reason"]["field"], "train")

    def test_nonfinite_inputs_in_each_series_are_unavailable_and_json_safe(self):
        for field in ("train", "actual", "predicted"):
            for value in (float("nan"), float("inf"), -float("inf"), 10**1000):
                with self.subTest(field=field):
                    inputs = {"train": [1, 2], "actual": [3], "predicted": [2]}
                    inputs[field][-1] = value
                    self.assert_unavailable(METRICS.compute(**inputs), "nonfinite_observation")

    def test_invalid_series(self):
        for value in (None, 1, "12", {0: 1}, {1, 2}, frozenset((1, 2))):
            with self.subTest(value=value):
                self.assert_unavailable(METRICS.compute(value, [1], [1]), "invalid_series")

    def test_training_difference_overflow(self):
        self.assert_unavailable(
            METRICS.compute([-1e308, 1e308], [1], [0]), "numeric_overflow"
        )

    def test_squared_error_overflow_does_not_hide_available_mase(self):
        result = METRICS.compute([0, 1], [1e200], [0])
        self.assertTrue(result["mase"]["available"])
        self.assertEqual(result["mase"]["value"], 1e200)
        self.assertFalse(result["rmsse"]["available"])
        self.assertEqual(result["rmsse"]["reason"]["code"], "numeric_overflow")
        json.dumps(result, allow_nan=False)

    def test_squared_error_underflow_is_not_treated_as_perfect_forecast(self):
        result = METRICS.compute([0, 1], [1e-200], [0])
        self.assertEqual(result["mase"]["value"], 1e-200)
        self.assertFalse(result["rmsse"]["available"])
        self.assertEqual(result["rmsse"]["reason"]["code"], "numeric_underflow")

    def test_repeated_values_do_not_overflow_a_representable_mean(self):
        result = METRICS.compute([0, 1], [1e308, 1e308], [0, 0])
        self.assertEqual(result["mase"]["value"], 1e308)
        json.dumps(result, allow_nan=False)

    def test_metric_ratio_overflow_is_unavailable(self):
        result = METRICS.compute([0, 1e-200], [1e200], [0])
        self.assertFalse(result["mase"]["available"])
        self.assertEqual(result["mase"]["reason"]["code"], "numeric_overflow")
        json.dumps(result, allow_nan=False)

    def test_json_has_no_nan_or_infinity(self):
        scenarios = (
            ([0, 0], [0], [0]),
            ([1, 2], [3], [2]),
            ([0, 1e200], [1], [0]),
            ([0, 1], [float("nan")], [0]),
        )
        for train, actual, predicted in scenarios:
            encoded = json.dumps(METRICS.compute(train, actual, predicted), allow_nan=False)
            self.assertNotIn("NaN", encoded)
            self.assertNotIn("Infinity", encoded)
            self.assertIsInstance(json.loads(encoded), dict)

    def test_inputs_are_not_mutated_and_generators_are_supported(self):
        train, actual, predicted = [1, 2, 4], [5], [4]
        expected = METRICS.compute(train, actual, predicted)
        self.assertEqual((train, actual, predicted), ([1, 2, 4], [5], [4]))
        generated = METRICS.compute(iter(train), iter(actual), iter(predicted))
        self.assertEqual(expected, generated)


if __name__ == "__main__":
    unittest.main()
