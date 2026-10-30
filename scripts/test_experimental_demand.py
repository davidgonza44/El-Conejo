"""Synthetic experimental contracts: no database, files, network or real demand."""
import json
import math
import os
from pathlib import Path
import sys
import unittest
from datetime import date, timedelta
from types import SimpleNamespace

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import experimental_demand_service as experimental
from app.services.predictive_data_service import TimeSeries, classify_eligibility


def series(values):
    return TimeSeries(1, "daily", [date(2024, 1, 1) + timedelta(days=i)
                                  for i in range(len(values))], list(values))


def analyze(values, horizon=7, active=True):
    source = series(values)
    return experimental.analyze(source, horizon, classify_eligibility(source, active))


class ExperimentalDemandTests(unittest.TestCase):
    def test_empty_zero_single_positive_finite_json(self):
        for values in ([], [0] * 140, [0] * 139 + [3]):
            result = analyze(values)
            self.assertEqual(result["prediction_policy"]["status"], "abstain")
            self.assertIsNone(result["demand_profile"]["cv2"])
            if not any(values):
                self.assertIsNone(result["demand_profile"]["adi"])
            self.assertFalse(result["two_part"]["available"])
            json.dumps(result, allow_nan=False)

    def test_descriptive_boundaries_distinct_from_adi(self):
        for zero_count, segment in ((49, "regular"), (50, "intermittent"),
                                    (94, "intermittent"), (95, "extremely_intermittent")):
            p = experimental.demand_profile(series([0] * zero_count + [1] * (100 - zero_count)))
            self.assertEqual(p["zero_rate_segment"], segment)
        classify = experimental.adi_cv2_classification
        self.assertEqual(classify(1.31, .48), "smooth")
        self.assertEqual(classify(1.32, .48), "intermittent")
        self.assertEqual(classify(1.31, .49), "erratic")
        self.assertEqual(classify(1.32, .49), "lumpy")
        self.assertEqual(classify(None, None), "unavailable")

    def test_profile_sample_cv2(self):
        p = experimental.demand_profile(series([0, 1, 0, 3]))
        self.assertEqual(p["adi"], 2)
        self.assertEqual(p["cv2"], .5)
        self.assertEqual(p["observation_unit"], "calendar_day")

    def test_same_horizon_identity_and_uncertainty(self):
        for h in (7, 30):
            result = analyze(([1] * h + [0] * h) * 10, h)
            two = result["two_part"]
            self.assertEqual(two["horizon"], h)
            self.assertEqual(two["occurrence_probability"], .5)
            self.assertEqual(two["positive_demand_expectation"], h)
            self.assertEqual(two["unconditional_expected_quantity"], h / 2)
            self.assertEqual(two["unconditional_expected_quantity"],
                             two["occurrence_probability"] * two["positive_demand_expectation"])
            interval = result["uncertainty"]
            self.assertTrue(interval["available"])
            self.assertEqual(interval["calibration_count"], 10)
            self.assertEqual(interval["lower_bound"], 0)
            self.assertEqual(interval["upper_bound"], h)
            self.assertEqual(result["prediction_policy"]["status"], "available")

    def test_calibration_insufficient_and_minimum_boundary(self):
        result = analyze([1] * (7 * 12))
        self.assertTrue(result["two_part"]["available"])
        self.assertFalse(result["uncertainty"]["available"])
        self.assertIsNone(result["uncertainty"]["lower_bound"])
        self.assertEqual(result["prediction_policy"]["status"], "limited")
        for blocks, available in ((16, False), (17, True), (18, True)):
            self.assertEqual(analyze([1] * (7 * blocks))["uncertainty"]["available"], available)

    def test_chronological_blocks_no_shared_targets_no_refit(self):
        blocks = experimental.complete_blocks([1] * 143, 7)
        self.assertEqual(blocks[0][:2], (3, 10))
        self.assertEqual(blocks[-1][1], 143)
        self.assertTrue(all(a[1] == b[0] for a, b in zip(blocks, blocks[1:])))
        before = analyze([1] * 70 + [2] * 70)
        after = analyze([1] * 70 + [10] * 70)
        self.assertEqual(before["two_part"], after["two_part"])
        self.assertNotEqual(before["uncertainty"]["upper_bound"], after["uncertainty"]["upper_bound"])

    def test_finite_sample_rank(self):
        values = [1] * 56 + [value for n in range(2, 11) for value in [n] * 7]
        result = analyze(values)
        self.assertEqual(result["uncertainty"]["calibration_count"], 9)
        # Eight order statistic of errors 7,14,...,63 is 56.
        self.assertEqual(result["uncertainty"]["upper_bound"], 63)

    def test_abstention_policy(self):
        for values, active, code in (([1] * 140, False, "inactive_product"),
                                     ([0] * 380 + [1] * 20, True, "extreme_intermittency"),
                                     ([1] * 20, True, "insufficient_history")):
            r = analyze(values, active=active)
            self.assertIn(code, r["prediction_policy"]["reason"])
            self.assertFalse(r["two_part"]["available"])

    def test_invalid_values_and_calendar(self):
        for invalid in (-1, math.nan, math.inf, "bad", True):
            source = series([1] * 139 + [invalid])
            result = experimental.analyze(source, 7, SimpleNamespace(status="ELIGIBLE"))
            self.assertIn("invalid_history", result["prediction_policy"]["reason"])
            json.dumps(result, allow_nan=False)
        source = series([1] * 140)
        source.starts[-1] += timedelta(days=1)
        result = experimental.analyze(source, 7, classify_eligibility(source))
        self.assertIn("incomplete_calendar", result["prediction_policy"]["reason"])

    def test_finite_extreme_numeric_ranges(self):
        for value in (1e308, 5e-324, 1e-250):
            result = analyze([value] * 140)
            json.dumps(result, allow_nan=False)
            if value == 1e308:
                self.assertFalse(result["two_part"]["available"])

    def test_unsupported_granularity_and_horizon(self):
        source = series([1] * 140)
        for horizon, unit in ((90, "calendar_day"), (7, "operational_day")):
            source.time_unit = unit
            r = experimental.analyze(source, horizon, classify_eligibility(source))
            self.assertFalse(r["two_part"]["available"])
        source.granularity = "weekly"
        self.assertEqual(experimental.demand_profile(source)["observation_unit"], "calendar_week")


if __name__ == "__main__":
    unittest.main(verbosity=2)
