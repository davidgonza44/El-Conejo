"""Fixtures DEMO calculables a mano; carga pura sin iniciar Flask ni una DB."""

import importlib.util
import json
from copy import deepcopy
from datetime import date, datetime, timedelta
from math import fsum
from pathlib import Path
import unittest


SERVICE_PATH = Path(__file__).resolve().parents[1] / "app" / "services" / "experimental_adida_service.py"
SPEC = importlib.util.spec_from_file_location("experimental_adida_fixture", SERVICE_PATH)
SERVICE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVICE)
adida = SERVICE.adida


def daily_dates(count, first=date(2024, 1, 1)):
    return [first + timedelta(days=index) for index in range(count)]


def fixture(history, aggregation_size=7, horizon=7):
    """Valores DEMO explícitos; 7 comprueba mecánica y no expresa un óptimo."""
    starts = daily_dates(len(history))
    return adida(history, aggregation_size=aggregation_size, horizon=horizon,
                 starts=starts, forecast_origin=date(2024, 1, 1) + timedelta(days=len(history)))


class ExperimentalAdidaTests(unittest.TestCase):
    def assert_finite_json(self, result):
        encoded = json.dumps(result, allow_nan=False)
        self.assertEqual(json.loads(encoded), result)

    def test_exact_blocks(self):
        result = fixture([1, 2, 3, 4, 5, 6], aggregation_size=3, horizon=3)
        self.assertTrue(result["available"])
        self.assertEqual(result["metadata"]["training_block_totals"], [6, 15])
        self.assertEqual(result["aggregated_observations"], 2)
        self.assertEqual(result["forecast"], [5, 5, 5])
        self.assertEqual(result["forecast_total"], 15)
        self.assert_finite_json(result)

    def test_initial_incomplete_block_is_discarded_and_disclosed(self):
        result = fixture([900, 800, 1, 2, 3, 4, 5, 6], aggregation_size=3, horizon=3)
        self.assertTrue(result["available"])
        self.assertEqual(result["metadata"]["discarded_initial_observations"], 2)
        self.assertEqual(result["metadata"]["training_block_totals"], [6, 15])
        self.assertEqual(result["forecast"], [5, 5, 5])

    def test_recent_training_is_complete_by_origin_alignment(self):
        result = fixture([99, 1, 2, 3, 4], aggregation_size=2, horizon=2)
        self.assertEqual(result["metadata"]["training_block_totals"], [3, 7])
        self.assertEqual(result["forecast"], [3.5, 3.5])
        self.assertEqual(result["partial_block_policy"], "origin_aligned_drop_oldest")

    def test_final_incomplete_forecast_block_is_proportional(self):
        result = fixture([1, 2, 3, 4, 5, 6], aggregation_size=3, horizon=5)
        self.assertEqual(result["metadata"]["forecast_block_periods"], [3, 2])
        self.assertEqual(result["metadata"]["forecast_block_totals"], [15, 10])
        self.assertEqual(result["forecast"], [5, 5, 5, 5, 5])
        self.assertEqual(result["forecast_total"], 25)

    def test_7_calendar_days(self):
        result = fixture([2] * 14, horizon=7)
        self.assertEqual(result["forecast"], [2] * 7)
        self.assertEqual(result["forecast_total"], 14)
        self.assertEqual(result["forecast_dates"][0], "2024-01-15")
        self.assertEqual(result["forecast_dates"][-1], "2024-01-21")
        self.assertEqual(result["horizon_unit"], "calendar_day")

    def test_30_calendar_days(self):
        result = fixture([2] * 14, horizon=30)
        self.assertEqual(result["forecast"], [2] * 30)
        self.assertEqual(result["forecast_total"], 60)
        self.assertEqual(result["metadata"]["forecast_block_periods"], [7, 7, 7, 7, 2])
        self.assertEqual(result["metadata"]["forecast_block_totals"], [14, 14, 14, 14, 4])

    def test_observed_all_zero_is_valid(self):
        result = fixture([0] * 14, horizon=30)
        self.assertTrue(result["available"])
        self.assertEqual(result["forecast"], [0] * 30)
        self.assertEqual(result["forecast_total"], 0)
        self.assert_finite_json(result)

    def test_intermittent_history(self):
        result = fixture([0, 0, 0, 8, 0, 0, 0] * 2)
        self.assertEqual(result["metadata"]["training_block_totals"], [8, 8])
        self.assertAlmostEqual(fsum(result["forecast"]), 8)
        self.assertAlmostEqual(result["forecast"][0], 8 / 7)

    def test_disaggregation_conserves_each_block_and_total(self):
        result = fixture([0, 0, 0, 1, 0, 0, 0] * 2, horizon=30)
        offset = 0
        for periods, total in zip(result["metadata"]["forecast_block_periods"],
                                  result["metadata"]["forecast_block_totals"]):
            self.assertAlmostEqual(fsum(result["forecast"][offset:offset + periods]), total)
            offset += periods
        self.assertAlmostEqual(result["forecast_total"], 30 / 7)
        self.assertAlmostEqual(fsum(result["metadata"]["forecast_block_totals"]), result["forecast_total"])
        self.assertEqual(offset, 30)

    def test_origin_observation_is_rejected_for_no_leakage(self):
        starts = daily_dates(7)
        result = adida([1] * 6 + [1000000], aggregation_size=7, horizon=7,
                       starts=starts, forecast_origin=starts[-1])
        self.assertFalse(result["available"])
        self.assertEqual(result["forecast"], [])
        self.assertEqual(result["unavailable_reason"]["code"], "future_history")

    def test_future_observation_is_rejected_for_no_leakage(self):
        result = adida([1, 999], aggregation_size=1, horizon=7,
                       starts=["2024-01-01", "2026-01-01"], forecast_origin="2024-01-02")
        self.assertEqual(result["unavailable_reason"]["code"], "future_history")

    def test_2025_unobserved_gap_is_not_zero_filled(self):
        result = adida([4, 6], aggregation_size=2, horizon=7,
                       starts=["2024-12-31", "2026-01-01"], forecast_origin="2026-01-02")
        self.assertFalse(result["available"])
        self.assertEqual(result["unavailable_reason"]["code"], "unobserved_gap")
        self.assertEqual(result["forecast"], [])

    def test_gap_inside_history_is_unavailable(self):
        result = adida([4, 6], aggregation_size=2, horizon=7,
                       starts=["2024-01-01", "2024-01-03"], forecast_origin="2024-01-04")
        self.assertEqual(result["unavailable_reason"]["code"], "unobserved_gap")

    def test_unobserved_final_training_day_is_unavailable(self):
        result = adida([4, 6], aggregation_size=2, horizon=7,
                       starts=["2024-01-01", "2024-01-02"], forecast_origin="2024-01-04")
        self.assertEqual(result["unavailable_reason"]["code"], "unobserved_trailing_period")

    def test_no_complete_block(self):
        result = fixture([1, 2, 3], aggregation_size=7)
        self.assertFalse(result["available"])
        self.assertEqual(result["unavailable_reason"]["code"], "insufficient_history")

    def test_empty_history(self):
        result = fixture([])
        self.assertEqual(result["unavailable_reason"]["code"], "insufficient_history")
        self.assert_finite_json(result)

    def test_aggregation_size_one_is_explicit_naive(self):
        result = fixture([1, 2, 5], aggregation_size=1, horizon=3)
        self.assertEqual(result["forecast"], [5, 5, 5])

    def test_invalid_aggregation_sizes(self):
        for size in [0, -1, 1.5, True, None, "7", float("nan"), float("inf")]:
            with self.subTest(size=size):
                result = fixture([1] * 7, aggregation_size=size)
                self.assertFalse(result["available"])
                self.assertEqual(result["unavailable_reason"]["code"], "invalid_configuration")
                self.assert_finite_json(result)

    def test_invalid_horizons(self):
        for horizon in [0, -1, 1.5, True, None, "7", float("nan"), float("inf")]:
            with self.subTest(horizon=horizon):
                result = fixture([1] * 7, horizon=horizon)
                self.assertFalse(result["available"])
                self.assert_finite_json(result)

    def test_missing_negative_non_numeric_or_non_finite_observation(self):
        for value in [None, -1, "3", True, float("nan"), float("inf")]:
            with self.subTest(value=value):
                result = fixture([0, value], aggregation_size=2)
                self.assertFalse(result["available"])
                self.assertEqual(result["unavailable_reason"]["code"], "invalid_observation")
                self.assert_finite_json(result)

    def test_invalid_history_container(self):
        result = adida("123", aggregation_size=1, horizon=1,
                       starts=["2024-01-01"], forecast_origin="2024-01-02")
        self.assertEqual(result["unavailable_reason"]["code"], "invalid_history")

    def test_invalid_date_formats_and_datetime(self):
        for bad_day in ["20240101", "2024-13-01", "2024-01-01T00:00:00", datetime(2024, 1, 1), None]:
            with self.subTest(day=bad_day):
                result = adida([1], aggregation_size=1, horizon=1,
                               starts=[bad_day], forecast_origin="2024-01-02")
                self.assertEqual(result["unavailable_reason"]["code"], "invalid_dates")

    def test_dates_count_must_match(self):
        result = adida([1, 2], aggregation_size=1, horizon=1,
                       starts=["2024-01-01"], forecast_origin="2024-01-02")
        self.assertEqual(result["unavailable_reason"]["code"], "invalid_dates")

    def test_duplicates_and_unsorted_dates_are_not_silently_sorted(self):
        for starts in [["2024-01-02", "2024-01-01"], ["2024-01-02", "2024-01-02"]]:
            with self.subTest(starts=starts):
                result = adida([1, 2], aggregation_size=2, horizon=1,
                               starts=starts, forecast_origin="2024-01-03")
                self.assertEqual(result["unavailable_reason"]["code"], "unordered_history")

    def test_numeric_overflow_returns_finite_json(self):
        result = fixture([1e308, 1e308], aggregation_size=2)
        self.assertFalse(result["available"])
        self.assertEqual(result["unavailable_reason"]["code"], "numeric_or_date_range")
        self.assert_finite_json(result)

    def test_forecast_date_overflow_returns_finite_json(self):
        result = adida([1], aggregation_size=1, horizon=2,
                       starts=["9999-12-30"], forecast_origin="9999-12-31")
        self.assertFalse(result["available"])
        self.assert_finite_json(result)

    def test_reproducible_and_does_not_mutate_inputs(self):
        history = [0, 0, 0, 8, 0, 0, 0] * 2
        starts = daily_dates(len(history))
        original_history, original_starts = deepcopy(history), deepcopy(starts)
        kwargs = {"aggregation_size": 7, "horizon": 30,
                  "starts": starts, "forecast_origin": "2024-01-15"}
        self.assertEqual(adida(history, **kwargs), adida(history, **kwargs))
        self.assertEqual(history, original_history)
        self.assertEqual(starts, original_starts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
