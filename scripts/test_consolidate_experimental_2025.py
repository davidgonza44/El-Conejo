"""Synthetic iterable fixtures for coverage postprocessing; no private sources."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import consolidate_experimental_2025 as consolidation
from run_experimental_predictive_regressions import offline_guard

sys.addaudithook(offline_guard)


def forecast(code, origin, target, *, view="2025_ONLY", model="mean"):
    return {"product_code": code, "origin": origin, "view": view, "horizon": "1",
            "model": model, "target_dates": json.dumps([target])}


def observation(code, day, quantity, unit="UND", *, year=2025):
    return {"product_code": code, "date": day, "quantity": str(quantity), "unit": unit,
            "scenario": consolidation.SYNTHETIC_SCENARIO if year == 2025 else consolidation.REAL_SCENARIO,
            "data_kind": "synthetic_structured" if year == 2025 else "real_observed", "year": str(year)}


def fixture():
    forecasts = [forecast("0001", day, day) for day in ("2025-07-01", "2025-07-02", "2025-07-03")]
    observations = [observation("0001", day, 2) for day in ("2025-07-01", "2025-07-02", "2025-07-03")]
    observations.append(observation("0002", "2025-07-01", 4, "KG."))
    contexts = [{"product_code": "0001", "unit": "CAJA"}, {"product_code": "0002", "unit": "KG."},
                {"product_code": "0003", "unit": "UND"}]
    report = {"configuration": {"minimum_publicable_folds": 3}, "views": {
        "2025_ONLY": {"horizons": {"1": {"target_date_count": 3, "coverage": {"mean": {
            "sku": {"numerator": 1, "denominator": 3}, "volume": {"numerator": 6, "denominator": 10}}}}}}}}
    return forecasts, observations, contexts, report


class ConsolidationTests(unittest.TestCase):
    def test_sha_bound_artifact_metadata_retains_all_four_views_and_initial_stock_roles(self):
        result = consolidation.artifact_provenance({filename: "fixture-hash" for filename in consolidation.REQUIRED_FILES})
        for filename in ("forecasts.csv", "metrics.csv", "inventory_simulations.csv"):
            metadata = result[filename]
            self.assertEqual(metadata["sha256"], "fixture-hash")
            self.assertEqual(metadata["purpose"], "experimental_evaluation")
            roles = metadata["view_roles"]
            self.assertEqual(roles["2025_ONLY"]["targets"]["data_kind"], "synthetic_structured")
            self.assertEqual(roles["2026_REAL_ONLY"]["targets"]["year"], 2026)
            self.assertEqual(roles["2025_TO_2026_TRANSFER"]["training_components"][0]["year"], 2025)
            self.assertEqual(roles["2025_TO_2026_TRANSFER"]["targets"]["data_kind"], "real_observed")
            expanding = roles["2025_PLUS_2026_OBSERVED_TO_DATE"]["training_components"]
            self.assertEqual([item["data_kind"] for item in expanding], ["synthetic_structured", "real_observed"])
        initial = result["inventory_simulations.csv"]["initial_stock_roles"]
        self.assertEqual(initial["synthetic_2025_snapshot_initial_stock"]["data_kind"], "synthetic_structured")
        self.assertEqual(initial["zero_initial_stock"]["data_kind"], "experimental_scenario_constant")
        self.assertEqual(result["inventory_context_2025.csv"]["role"], "snapshot_context_not_demand")
        json.dumps(result, allow_nan=False)

    def test_real_and_transfer_views_share_real_denominators_without_year_mixing(self):
        views = ("2026_REAL_ONLY", "2025_TO_2026_TRANSFER", "2025_PLUS_2026_OBSERVED_TO_DATE")
        days = ("2026-03-01", "2026-03-02", "2026-03-03")
        forecasts = [forecast("0001", day, day, view=view) for view in views for day in days]
        observations = [observation("0001", day, 3, "KG.", year=2026) for day in days]
        observations.append(observation("0001", "2025-07-01", 900, "UND"))
        report = {"configuration": {"minimum_publicable_folds": 3}, "views": {
            view: {"horizons": {"1": {"target_date_count": 3, "coverage": {"mean": {
                "sku": {"numerator": 1, "denominator": 1},
                "volume": {"numerator": 9, "denominator": 9}}}}}} for view in views}}
        result = consolidation.coverage_by_unit(forecasts, observations, [{"product_code": "0001", "unit": "UND"}], report)
        for view in views:
            unit = result["coverage_by_unit"][view]["1"]["models"]["mean"]["KG."]
            self.assertEqual(unit["quantity"]["denominator"], 9)
            self.assertEqual(unit["sku"]["denominator"], 1)
        self.assertEqual(result["diagnostics"]["universe_2026_sku"], 1)

    def test_manifest_mismatch_rejected_before_source_tables_are_read(self):
        directory = consolidation.ROOT / "reports/generated/experimental_2025/synthetic_unused_fixture"
        index = {name: "expected" for name in consolidation.REQUIRED_FILES}
        with patch.object(Path, "read_text", return_value=json.dumps(index)), \
                patch.object(consolidation, "sha256", return_value="changed"), \
                patch.object(consolidation, "rows", side_effect=AssertionError("Tables must not be read")):
            with self.assertRaisesRegex(ValueError, "does not match"):
                consolidation.consolidate(directory)

    def test_unit_counts_and_quantities_reconcile_without_mixed_unit_sum(self):
        result = consolidation.coverage_by_unit(*fixture())
        units = result["coverage_by_unit"]["2025_ONLY"]["1"]["models"]["mean"]
        self.assertEqual(units["UND"]["sku"]["denominator"], 2)
        self.assertEqual(units["UND"]["quantity"]["percent"], 100)
        self.assertEqual(units["KG."]["quantity"]["denominator"], 4)
        self.assertEqual(units["KG."]["quantity"]["numerator"], 0)
        self.assertNotIn("CAJA", units)
        self.assertEqual(result["diagnostics"]["snapshot_only_2025_sku"], 1)
        self.assertEqual(result["diagnostics"]["demand_snapshot_unit_mismatch_sku"], 1)
        json.dumps(result, allow_nan=False)

    def test_duplicate_observation_not_counted_twice(self):
        forecasts, observations, contexts, report = fixture()
        observations.append(deepcopy(observations[0]))
        result = consolidation.coverage_by_unit(forecasts, observations, contexts, report)
        units = result["coverage_by_unit"]["2025_ONLY"]["1"]["models"]["mean"]
        self.assertEqual(units["UND"]["quantity"]["denominator"], 6)
        self.assertEqual(result["diagnostics"]["duplicate_selected_observation_rows_ignored"], 1)

    def test_conflicting_duplicate_or_changing_unit_rejected(self):
        for field, value in (("quantity", "99"), ("unit", "CAJA")):
            forecasts, observations, contexts, report = fixture()
            duplicated = deepcopy(observations[0])
            duplicated[field] = value
            observations.append(duplicated)
            with self.assertRaises(ValueError):
                consolidation.coverage_by_unit(forecasts, observations, contexts, report)

    def test_duplicate_forecast_origin_does_not_inflate_fold_count(self):
        forecasts, observations, contexts, report = fixture()
        forecasts = [forecasts[0]] * 4
        report["views"]["2025_ONLY"]["horizons"]["1"]["target_date_count"] = 1
        core = report["views"]["2025_ONLY"]["horizons"]["1"]["coverage"]["mean"]
        core["sku"]["numerator"] = 0
        core["volume"] = {"numerator": 0, "denominator": 6}
        result = consolidation.coverage_by_unit(forecasts, observations, contexts, report)
        unit = result["coverage_by_unit"]["2025_ONLY"]["1"]["models"]["mean"]["UND"]
        self.assertEqual(unit["sku"]["numerator"], 0)

    def test_repeated_models_do_not_repeat_observed_volume(self):
        forecasts, observations, contexts, report = fixture()
        extra = [dict(row, model="zero") for row in forecasts]
        summary = report["views"]["2025_ONLY"]["horizons"]["1"]
        summary["coverage"]["zero"] = deepcopy(summary["coverage"]["mean"])
        result = consolidation.coverage_by_unit(forecasts + extra, observations, contexts, report)
        units = result["coverage_by_unit"]["2025_ONLY"]["1"]["models"]
        self.assertEqual(units["zero"]["UND"]["quantity"]["denominator"], 6)
        self.assertEqual(units["zero"], units["mean"])

    def test_demand_before_evaluation_or_activity_scenario_does_not_enter_denominator(self):
        forecasts, observations, contexts, report = fixture()
        observations.append(observation("0001", "2025-01-01", 900))
        activity = observation("0001", "2025-07-01", 1000)
        activity["scenario"] = "2025_activity_date_synthetic_exposure"
        observations.append(activity)
        result = consolidation.coverage_by_unit(forecasts, observations, contexts, report)
        unit = result["coverage_by_unit"]["2025_ONLY"]["1"]["models"]["mean"]["UND"]
        self.assertEqual(unit["quantity"]["denominator"], 6)

    def test_partial_materialized_grid_returns_unavailable_without_invention(self):
        forecasts, observations, contexts, report = fixture()
        result = consolidation.coverage_by_unit(forecasts[:1], observations, contexts, report)
        self.assertFalse(result["coverage_by_unit"]["2025_ONLY"]["1"]["available"])
        self.assertEqual(result["coverage_by_unit"]["2025_ONLY"]["1"]["unavailable_reason"],
                         "forecast_target_grid_not_fully_materialized")

    def test_wrong_year_or_nonfinite_observation_rejected(self):
        for field, value in (("data_kind", "real_observed"), ("quantity", "NaN")):
            forecasts, observations, contexts, report = fixture()
            observations[0][field] = value
            with self.assertRaises(ValueError):
                consolidation.coverage_by_unit(forecasts, observations, contexts, report)

    def test_disagreeing_target_identity_or_overlapping_origins_rejected(self):
        first = forecast("0001", "2025-07-01", "2025-07-01")
        other = dict(first, target_dates=json.dumps(["2025-07-02"]))
        with self.assertRaises(ValueError):
            consolidation.forecast_support([first, other])
        one = dict(first, horizon="2", target_dates=json.dumps(["2025-07-01", "2025-07-02"]))
        two = dict(first, origin="2025-07-02", horizon="2",
                   target_dates=json.dumps(["2025-07-02", "2025-07-03"]))
        with self.assertRaises(ValueError):
            consolidation.forecast_support([one, two])

    def test_unverified_unit_explicit_and_zero_quantity_finite(self):
        result = consolidation.unit_coverage({"0001"}, {}, {}, set())
        unit = result[consolidation.UNVERIFIED_UNIT]
        self.assertFalse(unit["quantity_unit_verified"])
        self.assertIsNone(unit["quantity"]["percent"])
        json.dumps(result, allow_nan=False)

    def test_leading_zero_codes_remain_distinct_and_outputs_sanitized(self):
        result = consolidation.unit_coverage({"001", "1"}, {"001": "UND", "1": "UND"},
                                              {"001": 2, "1": 8}, {"001"})
        self.assertEqual(result["UND"]["quantity"]["percent"], 20)
        full = consolidation.coverage_by_unit(*fixture())
        serialized = json.dumps(full, allow_nan=False)
        self.assertNotIn("0001", serialized)
        self.assertNotIn("product_code", serialized)

    def test_reproducibility_without_mutating_inputs(self):
        inputs = fixture()
        before = deepcopy(inputs)
        self.assertEqual(consolidation.coverage_by_unit(*inputs), consolidation.coverage_by_unit(*inputs))
        self.assertEqual(inputs, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
