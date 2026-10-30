"""Offline synthetic tests of the 2025 experiment engine; never start Flask/DB."""
from copy import deepcopy
from datetime import date, timedelta
import json
import sys
import unittest

from experimental_prediction_tools import load_service
from run_experimental_predictive_regressions import offline_guard

sys.addaudithook(offline_guard)
models = load_service("experimental_2025_models")
statistics = load_service("experimental_2025_statistics")


def days(count, start=date(2025, 1, 1), step=1):
    return [start + timedelta(days=index * step) for index in range(count)]


def candidate(model, values, horizon=7, **kwargs):
    return models.forecast_candidate(model, values, days(len(values)), horizon, **kwargs)


def fold(code="0001", actual=None, predicted=None, **extra):
    actual = [2.0, 1.0] if actual is None else actual
    predicted = [1.0, 1.0] if predicted is None else predicted
    return {"product_code": code, "origin": "2025-07-01",
            "target_dates": ["2025-07-01", "2025-07-02"],
            "actual": actual, "predicted": predicted, "status": "SUCCESS",
            "metrics": models.score_forecast([1, 2, 1, 2], actual, predicted), **extra}


class CandidateTests(unittest.TestCase):
    def test_every_model_has_finite_reproducible_output(self):
        history = [1, 0, 2, 0, 3, 0, 2] * 80
        for model in models.MODELS:
            first, second = candidate(model, history), candidate(model, history)
            self.assertEqual(first, second)
            json.dumps(first, allow_nan=False)
            self.assertTrue(first["available"], model)

    def test_baseline_arithmetic_matches_existing_daily_contract(self):
        values = list(range(1, 15))
        self.assertEqual(candidate("naive", values)["forecast"], [14.0] * 7)
        self.assertEqual(candidate("mean", values)["forecast"], [7.5] * 7)
        self.assertEqual(candidate("moving_average", values)["forecast"], [11.0] * 7)
        self.assertEqual(candidate("linear_trend", values)["forecast"], list(range(15, 22)))

    def test_declining_trend_clipped_to_zero(self):
        result = candidate("linear_trend", list(range(14, 0, -1)))
        self.assertEqual(result["forecast"], [0.0] * 7)

    def test_sba_bias_adjustment_and_tsb_decay(self):
        history = [2, 0] * 8
        croston = candidate("croston", history)["forecast_total"]
        sba = candidate("sba", history)["forecast_total"]
        self.assertAlmostEqual(sba, croston * 0.95)
        self.assertGreater(candidate("tsb", history)["forecast_total"],
                           candidate("tsb", history + [0] * 40)["forecast_total"])

    def test_croston_initial_interval_includes_first_event_wait(self):
        # First demand at period 3: z=6, p=3; second at period 6 gives p=3.
        history = [0, 0, 6, 0, 0, 6] + [0] * 8
        self.assertEqual(candidate("croston", history)["forecast"], [2.0] * 7)

    def test_sparse_candidates_abstain(self):
        for model in ("croston", "sba", "tsb", "two_part"):
            result = candidate(model, [0] * 60)
            self.assertFalse(result["available"])
            self.assertEqual(result["policy"]["status"], "abstain")
            self.assertIn("insufficient_positive_events", result["policy"]["reason"])

    def test_short_history_and_invalid_quantity_abstain_finitely(self):
        for values in ([1] * 13, [1] * 13 + [float("nan")], [1] * 13 + [-1], [True] * 14):
            result = candidate("mean", values)
            self.assertFalse(result["available"])
            json.dumps(result, allow_nan=False)

    def test_dates_are_contract_no_sorting_or_zero_fill(self):
        history = [1, 2] * 10
        gaps = days(len(history), step=2)
        result = models.forecast_candidate("adida", history, gaps, 7)
        self.assertEqual(result["unavailable_reason"], "unobserved_calendar_gap")
        unordered = list(reversed(days(len(history))))
        self.assertEqual(models.forecast_candidate("mean", history, unordered, 7)["unavailable_reason"],
                         "invalid_history")

    def test_two_part_identity_is_horizon_total(self):
        for horizon in (7, 30):
            result = candidate("two_part", [2, 0] * 280, horizon)
            part = result["two_part"]
            self.assertTrue(part["available"])
            self.assertAlmostEqual(part["unconditional_expected_quantity"],
                                   part["occurrence_probability"] * part["positive_demand_expectation"])
            self.assertAlmostEqual(sum(result["forecast"]), part["unconditional_expected_quantity"])

    def test_two_part_extreme_intermittency_explicit_reason(self):
        result = candidate("two_part", ([5] + [0] * 99) * 3)
        self.assertEqual(result["policy"]["reason"], ["extreme_intermittency"])

    def test_two_part_interval_chronological_and_limited_if_insufficient(self):
        early = candidate("two_part", [1] * 182, 30)
        enough = candidate("two_part", [1] * 560, 30)
        self.assertFalse(early["uncertainty"]["available"])
        self.assertEqual(early["policy"]["status"], "limited")
        self.assertTrue(enough["uncertainty"]["available"])
        self.assertEqual(enough["uncertainty"]["lower_bound"], 30)
        self.assertEqual(enough["uncertainty"]["upper_bound"], 30)

    def test_generic_interval_uses_only_preorigin_training(self):
        values = [1, 2, 3, 0] * 90
        result = candidate("tsb", values)
        self.assertTrue(result["uncertainty"]["available"])
        self.assertGreaterEqual(result["uncertainty"]["calibration_count"], 9)
        self.assertEqual(result["uncertainty"]["coverage_claim"],
                         "nominal_only_until_external_observed_coverage")
        self.assertEqual(result, candidate("tsb", values))

    def test_adida_fixed_explicit_bucket_and_partial_horizon(self):
        history = list(range(1, 15))
        result = candidate("adida", history, 30, aggregation_size=7)
        self.assertEqual(result["adida"]["metadata"]["training_block_totals"], [28.0, 77.0])
        self.assertEqual(result["forecast_total"], 330)
        self.assertFalse(result["configuration"]["configuration_selected_on_targets"])

    def test_operational_adida_and_two_part_preserve_calendar_dates(self):
        history, starts = [1, 2, 0, 1] * 90, days(360, step=2)
        before = list(starts)
        for model in ("two_part", "adida"):
            result = models.forecast_candidate(model, history, starts, 7, time_unit="operational_day")
            self.assertTrue(result["available"])
            self.assertEqual(result["time_unit"], "operational_day")
            self.assertIn("explicit_operational", result["configuration"]["cadence_policy"])
        self.assertEqual(starts, before)

    def test_configuration_errors_do_not_guess_models_or_cadence(self):
        for kwargs in ({"horizon": 0}, {"horizon": 7, "alpha": 0},
                       {"horizon": 7, "time_unit": "weekly"}):
            with self.assertRaises(ValueError):
                models.forecast_candidate("mean", [1] * 20, days(20), **kwargs)
        with self.assertRaises(ValueError):
            candidate("unknown", [1] * 20)

    def test_disabled_uncertainty_truthfully_limited(self):
        result = candidate("mean", [1, 2] * 100, with_uncertainty=False)
        self.assertEqual(result["uncertainty"]["unavailable_reason"], "not_requested")
        self.assertEqual(result["policy"]["status"], "limited")


class ScoringTests(unittest.TestCase):
    def test_mase_rmsse_training_only_and_legacy_errors(self):
        result = models.score_forecast([1, 2, 4, 7], [8, 11], [7, 13])
        self.assertEqual(result["mase"], 0.75)
        self.assertAlmostEqual(result["rmsse"], (15 / 28) ** 0.5)
        self.assertEqual(result["mae"], 1.5)
        self.assertEqual(result["bias"], 0.5)
        self.assertEqual(result["scale_source"], "training_only")

    def test_zero_benchmark_wape_exactly_100_when_demand_positive(self):
        result = models.score_forecast([0, 2, 1], [0, 2, 3], [0, 0, 0])
        self.assertEqual(result["wape"], 100)
        decimal = models.score_forecast([1, 2, 1], [1.1, 2.2, 3.3], [0, 0, 0])
        self.assertEqual(decimal["wape"], 100)

    def test_zero_scale_and_zero_actual_have_reasons_no_nan(self):
        result = models.score_forecast([1] * 30, [0, 0], [0, 0])
        self.assertIsNone(result["mase"])
        self.assertIsNone(result["rmsse"])
        self.assertIsNone(result["wape"])
        self.assertEqual(result["unavailable_reasons"]["mase"], "zero_training_scale")
        json.dumps(result, allow_nan=False)

    def test_scales_exclude_cross_year_and_calendar_gaps(self):
        starts = [date(2025, 12, 30), date(2025, 12, 31), date(2026, 1, 1), date(2026, 1, 3)]
        history = [1, 2, 1000, 1002]
        calendar = models.score_forecast(history, [3], [2], training_dates=starts)
        operational = models.score_forecast(history, [3], [2], training_dates=starts,
                                             time_unit="operational_day")
        self.assertEqual(calendar["scale_pair_count"], 1)
        self.assertEqual(calendar["mase_denominator"], 1)
        self.assertEqual(operational["scale_pair_count"], 2)
        self.assertEqual(operational["mase_denominator"], 1.5)

    def test_score_rejects_nonfinite_mismatches_and_boolean_lag(self):
        for actual, predicted in (([1], []), ([float("inf")], [1])):
            with self.assertRaises(ValueError):
                models.score_forecast([1, 2], actual, predicted)
        with self.assertRaises(ValueError):
            models.score_forecast([1, 2], [1], [1], m=True)


class StatisticsTests(unittest.TestCase):
    def test_inventory_comparison_reports_exact_pairs_and_discarded_cohorts(self):
        def inventory_row(code, fulfilled, **extra):
            metrics = {name: 0.0 for name in statistics.INVENTORY_METRICS}
            metrics.update(total_demand=10.0, fulfilled_units=float(fulfilled),
                           unmet_units=10.0 - fulfilled, fill_rate=fulfilled / 10)
            return {"product_code": code, "model": "tsb", "scenario_id": "zero_initial_stock",
                    "start": "2026-03-01", "end": "2026-06-01", "time_unit": "operational_day",
                    "horizon": 7, "metrics": metrics, **extra}
        first = [inventory_row("0001", 8), inventory_row("0002", 5)]
        second = [inventory_row("0001", 4), inventory_row("0003", 3)]
        result = statistics.paired_inventory_comparison(first, second)["zero_initial_stock:tsb"]
        self.assertEqual(result["paired_sku_count"], 1)
        self.assertEqual(result["discarded_candidate_skus"], 1)
        self.assertEqual(result["discarded_reference_skus"], 1)
        self.assertEqual(result["difference_candidate_minus_reference"]["fulfilled_units"], 4)
        self.assertFalse(result["scenario_selected"])
        json.dumps(result, allow_nan=False)
        self.assertEqual(statistics.paired_inventory_comparison(first, second),
                         statistics.paired_inventory_comparison(first, second))
        bad = deepcopy(second)
        bad[0]["metrics"]["total_demand"] = 99
        with self.assertRaises(ValueError):
            statistics.paired_inventory_comparison(first, bad)
        with self.assertRaises(ValueError):
            statistics.paired_inventory_comparison(first + first[:1], second)
        mismatched = [inventory_row("0001", 4, horizon=30)]
        self.assertFalse(statistics.paired_inventory_comparison(first, mismatched)["zero_initial_stock:tsb"]["available"])

    def test_sku_and_volume_coverage_explicit_numerator_denominator(self):
        result = statistics.coverage_summary({"0001": 9, "0002": 1, "0003": 0}, {"0001"})
        self.assertEqual(result["sku"]["numerator"], 1)
        self.assertEqual(result["sku"]["denominator"], 3)
        self.assertEqual(result["volume"]["percent"], 90)
        with self.assertRaises(ValueError):
            statistics.coverage_summary({"0001": 1}, {"absent"})

    def test_abstention_all_sku_and_volume_partition(self):
        result = statistics.abstention_summary({"0001": 5, "0002": 4, "0003": 1},
            {"0001": {"status": "available", "reason": []},
             "0002": {"status": "limited", "reason": ["insufficient_calibration"]}})
        self.assertEqual(sum(item["sku"]["numerator"] for item in result.values()), 3)
        self.assertEqual(sum(item["volume"]["percent"] for item in result.values()), 100)
        self.assertEqual(result["abstain"]["reasons"], {"no_evaluable_origin": 1})

    def test_pooled_scaled_errors_weight_by_targets(self):
        rows = [fold(), fold("0002", actual=[5, 5], predicted=[2, 2])]
        result = statistics.summarize_pairs(rows)
        self.assertEqual(result["n"], 4)
        self.assertEqual(result["actual_total"], 13)
        self.assertEqual(result["mase"], 1.75)
        self.assertAlmostEqual(result["rmsse"], (19 / 4) ** 0.5)

    def test_segments_never_disappear_into_global_pool(self):
        rows = [fold(str(index), zero_rate_segment="regular", adi_cv2_classification="smooth")
                for index in range(5)] + [fold("sparse", zero_rate_segment="extremely_intermittent",
                                                adi_cv2_classification="lumpy")]
        result = statistics.segment_summary(rows)
        self.assertTrue(result["zero_rate_segment"]["regular"]["available"])
        self.assertFalse(result["zero_rate_segment"]["extremely_intermittent"]["available"])
        self.assertEqual(result["adi_cv2_classification"]["lumpy"]["summary"]["sku_count"], 1)

    def test_brier_exact_and_bins_minimum_counts(self):
        rows = [fold(str(index), actual=[float(index % 2), 0],
                     two_part={"available": True, "occurrence_probability": .5}) for index in range(20)]
        result = statistics.calibration_summary(rows)["occurrence"]
        self.assertEqual(result["brier_score"], .25)
        self.assertTrue(result["bins_available"])
        self.assertEqual(result["bins"][2]["count"], 20)
        self.assertEqual(result["bins"][2]["observed_positive_rate"], .5)
        self.assertFalse(statistics.calibration_summary(rows[:19])["occurrence"]["bins_available"])

    def test_nominal_coverage_never_substitutes_observed(self):
        interval = {"available": True, "lower_bound": 1, "upper_bound": 4,
                    "nominal_coverage": .8, "calibration_count": 9}
        rows = [fold(actual=[1, 1], uncertainty=interval),
                fold("0002", actual=[3, 3], uncertainty=interval),
                fold("0003", uncertainty={"available": False, "unavailable_reason": "too_short"})]
        result = statistics.calibration_summary(rows)["intervals"]
        self.assertEqual(result["nominal_coverage"], .8)
        self.assertEqual(result["observed_coverage"], .5)
        self.assertEqual(result["average_interval_width"], 3)
        self.assertEqual(result["unavailable_count"], 1)
        self.assertEqual(result["unavailable_reason"], {"too_short": 1})

    def test_bootstrap_paired_cluster_reproducible_ci_known(self):
        candidate_rows = [fold(str(index), actual=[2, 2], predicted=[2, 2]) for index in range(8)]
        reference_rows = [fold(str(index), actual=[2, 2], predicted=[1, 1]) for index in range(8)]
        first = statistics.paired_bootstrap(candidate_rows, reference_rows, seed=34, iterations=100)
        self.assertEqual(first, statistics.paired_bootstrap(candidate_rows, reference_rows, seed=34, iterations=100))
        self.assertEqual(first["mean_difference"], -1)
        self.assertEqual(first["median_difference"], -1)
        self.assertEqual(first["confidence_interval"], [-1, -1])
        self.assertEqual(first["N_sku"], 8)
        json.dumps(first, allow_nan=False)

    def test_bootstrap_no_silent_pair_intersection_or_target_rewrite(self):
        rows = [fold(str(index)) for index in range(6)]
        self.assertFalse(statistics.paired_bootstrap(rows, rows[:-1])["available"])
        mismatched = deepcopy(rows)
        mismatched[0]["actual"][0] = 90
        self.assertEqual(statistics.paired_bootstrap(rows, mismatched)["unavailable_reason"], "target_values_differ")
        self.assertEqual(statistics.paired_bootstrap(rows[:4], rows[:4])["unavailable_reason"],
                         "insufficient_sku_clusters")
        with self.assertRaises(ValueError):
            statistics.paired_bootstrap(rows + rows[:1], rows)

    def test_baseline_chosen_on_development_only(self):
        rows = [fold("0001", model=model, predicted=[2, 1] if model == "mean" else [0, 0],
                     selection_role="development") for model in models.BASELINES]
        result = statistics.select_development_baseline(rows)
        self.assertEqual(result["model"], "mean")
        self.assertFalse(result["selection_on_evaluation_targets"])
        with self.assertRaises(ValueError):
            statistics.select_development_baseline([fold(model="mean", selection_role="test")])

    def test_empty_outputs_finite_and_explicitly_unavailable(self):
        for result in (statistics.summarize_pairs([]), statistics.coverage_summary({}, []),
                       statistics.abstention_summary({}, {}), statistics.calibration_summary([]),
                       statistics.paired_bootstrap([], [])):
            json.dumps(result, allow_nan=False)

    def test_statistics_reject_nonfinite_supplied_scales_and_nominal_coverage(self):
        row = fold()
        row["metrics"]["mase"] = float("nan")
        with self.assertRaises(ValueError):
            statistics.summarize_pairs([row])
        row = fold(uncertainty={"available": True, "lower_bound": 0, "upper_bound": 3,
                                "nominal_coverage": float("nan")})
        with self.assertRaises(ValueError):
            statistics.calibration_summary([row])

    def test_modules_never_import_application_or_create_orders(self):
        from pathlib import Path
        for module in (models, statistics):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn("from app.models", source)
            self.assertNotIn("create_app(", source)
            self.assertNotIn("PurchaseOrder", source)
        for event in ("sqlite3.connect", "socket.connect"):
            with self.assertRaises(RuntimeError):
                offline_guard(event, ())


if __name__ == "__main__":
    unittest.main(verbosity=2)
