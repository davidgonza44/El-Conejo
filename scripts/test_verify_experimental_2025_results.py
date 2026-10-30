"""Meaningful corruption tests for the independent exported-result verifier."""
import copy
import csv
import importlib.util
import json
from math import sqrt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


PATH = Path(__file__).with_name("verify_experimental_2025_results.py")
SPEC = importlib.util.spec_from_file_location("verify_experimental_2025_results", PATH)
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


def json_write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, allow_nan=False), encoding="utf-8")


def forecast(origin="2025-07-01", code="0001"):
    start = VERIFY.date.fromisoformat(origin)
    actual, predicted = [1, 2, 0, 0, 0, 0, 0], [0] * 7
    metrics = {"n": 7, "actual_total": 3, "absolute_error_total": 3, "squared_error_total": 5, "signed_error_total": -3,
               "mae": 3 / 7, "rmse": sqrt(5 / 7), "bias": -3 / 7, "wape": 100,
               "mase": 3 / 7, "rmsse": sqrt(5 / 14), "mase_denominator": 1, "rmsse_denominator": 2,
               "scale_source": "training_only", "scale_pair_count": 59, "scale_m": 1,
               "unavailable_reasons": {"mase": None, "rmsse": None}}
    return {"view": "2025_ONLY", "horizon": "7", "time_unit": "calendar_day", "product_code": code,
            "model": "zero", "origin": origin, "target_dates": [(start + VERIFY.timedelta(days=i)).isoformat() for i in range(7)],
            "actual": actual, "predicted": predicted, "metrics": metrics, "training_end": "2025-06-30",
            "training_count": "60", "training_sources": {"synthetic_structured": 60},
            "target_sources": {"synthetic_structured": 7}, "two_part": None, "uncertainty": {}, "prediction_policy": {}, "profile": {}}


def aggregate(folds, skus):
    return {"fold_count": folds, "sku_count": skus, "n": folds * 7, "actual_total": folds * 3,
            "absolute_error_total": folds * 3, "squared_error_total": folds * 5, "signed_error_total": folds * -3,
            "wape": 100, "mae": 3 / 7, "rmse": sqrt(5 / 7), "bias": -3 / 7, "mase": 3 / 7, "rmsse": sqrt(5 / 14)}


def inventory():
    metrics = {"total_demand": 3, "fulfilled_units": 2, "unmet_units": 1, "fill_rate": 2 / 3,
               "stockout_periods": 1, "cycle_service_level": 0, "average_on_hand": 0.2, "max_on_hand": 2,
               "ending_stock": 0, "order_count": 1, "total_ordered_units": 2, "received_units": 2,
               "lost_units": 1, "ending_backorder": 0, "ending_pipeline_units": 0,
               "immediately_fulfilled_units": 2, "complete_review_cycles": 1, "successful_review_cycles": 0}
    return {"view": "2025_ONLY", "horizon": "7", "product_code": "0001", "model": "zero",
            "scenario_id": "zero_initial_stock", "start": "2025-07-01", "end": "2025-07-05",
            "initial_stock_data_kind": "scenario_constant", "metrics": metrics,
            "accounting": {"stock_balance_error": 0, "demand_balance_error": 0, "pipeline_balance_error": 0},
            "scenario": {"periods": 5, "initial_stock": 0, "lead_time_periods": 2, "review_period": 5,
                         "stockout_mode": "lost_sales", "policy": "forecast_order_up_to", "policy_parameters": {"target_stock": 0.0}}}


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="verify-experiment-fixtures-")
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name) / "run"
        self.run.mkdir()
        self.frozen = Path(self.tmp.name) / "frozen"
        self.frozen.mkdir()
        self.sources = Path(self.tmp.name) / "sources"
        self.sources.mkdir()
        for name in ("demand.csv", "movements.csv", "inventory.csv"):
            (self.sources / name).write_text("synthetic fixture", encoding="utf-8")
        before = {path.name: VERIFY.digest(path) for path in self.sources.iterdir()}
        (self.frozen / "empirical_2026_operational_calendar.csv").write_text("fecha_real\n2026-01-01\n2026-01-02\n", encoding="utf-8")
        frozen_hashes = {path.name: VERIFY.digest(path) for path in self.frozen.iterdir()}
        build = {"READY_FOR_BACKTEST": True, "source_sha256_before": {}, "source_sha256_after": {}, "artifact_sha256": frozen_hashes}
        json_write(self.frozen / "empirical_2026_build_report.json", build)
        build_hash = VERIFY.digest(self.frozen / "empirical_2026_build_report.json")
        self.addCleanup(patch.stopall)
        patch.object(VERIFY, "FROZEN_BUILD_SHA256", build_hash).start()
        self.forecasts = [forecast(day) for day in ("2025-07-01", "2025-07-08", "2025-07-15")]
        self.forecasts.append(forecast(code="0002"))
        self.inventory = [inventory()]
        audit = {"sources": {name: {"source_id": name, "sha256": sha} for name, sha in before.items()}}
        public = {**aggregate(3, 1), "all_successful_origin_metrics": aggregate(4, 2)}
        inv_summary = {"sku_count": 1, "macro_metrics": {key: self.inventory[0]["metrics"][key] for key in VERIFY.INVENTORY_METRICS},
                       "quantity_index_total_demand": 3, "quantity_index_fulfilled_units": 2, "quantity_index_fill_rate": 2 / 3}
        self.report = {"metadata": {"data_kind": "synthetic_structured", "year": 2025, "purpose": "experimental_evaluation",
                                    "sources_2025_sha256": before, "inputs_2026_sha256": {**frozen_hashes, "empirical_2026_build_report.json": build_hash}},
                       "integrity": {"sources_2025_sha256_after": before, "sources_2025_unchanged": True,
                                     "frozen_2026_inputs_unchanged": True, "no_database": True, "no_real_orders": True},
                       "read_only": True, "live_integration": False, "audit": audit,
                       "views": {"2025_ONLY": {"horizons": {"7": {"metrics": {"zero": public}, "inventory": {"results": {"zero_initial_stock:zero": inv_summary}}}}}}}
        self.configuration = {"minimum_publicable_folds": 3, "data_kind": "synthetic_structured", "year": 2025,
                              "scenario_selection_on_2026": False, "original_protocol_changed": False,
                              "models": sorted(VERIFY.MODELS), "horizons": [7, 30]}
        self.report["configuration"] = copy.deepcopy(self.configuration)
        empty = VERIFY.finalize(VERIFY.empty_aggregate(), 0)
        for view in VERIFY.VIEWS:
            definition = self.report["views"].setdefault(view, {"horizons": {}})
            for horizon in ("7", "30"):
                evaluation = definition["horizons"].setdefault(horizon, {"metrics": {}, "inventory": {"results": {}}})
                for model in self.configuration["models"]:
                    evaluation["metrics"].setdefault(model, {**copy.deepcopy(empty),
                        "all_successful_origin_metrics": copy.deepcopy(empty)})

    def write_csv(self, path, rows):
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            for row in rows:
                writer.writerow({key: json.dumps(value, allow_nan=False) if isinstance(value, (list, dict)) else value for key, value in row.items()})

    def write(self):
        json_write(self.run / "results.json", self.report)
        json_write(self.run / "audit_2025.json", self.report["audit"])
        json_write(self.run / "configuration.json", self.configuration)
        self.write_csv(self.run / "forecasts.csv", self.forecasts)
        self.write_csv(self.run / "inventory_simulations.csv", self.inventory)
        fields = ("sku_count", "fold_count", "n", "wape", "mae", "rmse", "bias", "mase", "rmsse", "actual_total")
        exported = [{"view": view, "horizon": horizon, "model": model,
                     **{key: values[key] for key in fields}}
                    for view, definition in self.report["views"].items()
                    for horizon, evaluation in definition["horizons"].items()
                    for model, values in evaluation["metrics"].items()]
        self.write_csv(self.run / "metrics.csv", exported)
        for name in ("profiles_2025.csv", "inventory_context_2025.csv", "observations.csv"):
            (self.run / name).write_text("fixture\n", encoding="utf-8")
        json_write(self.run / "artifact_sha256.json", {name: VERIFY.digest(self.run / name) for name in VERIFY.REQUIRED})

    def verify(self):
        self.write()
        return VERIFY.verify_run(self.run, frozen_directory=self.frozen, source_directory=self.sources)

    def test_complete_fixture_public_minimum_three_folds(self):
        result = self.verify()
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["forecasts"]["forecast_rows"], 4)
        self.assertEqual(result["inventory"]["inventory_rows"], 1)
        self.assertTrue(result["source_integrity"]["current_2025_sources_rehashed"])

    def test_artifact_altered_after_manifest_rejected(self):
        self.write()
        with (self.run / "forecasts.csv").open("a", encoding="utf-8") as handle:
            handle.write("altered\n")
        with self.assertRaisesRegex(VERIFY.VerificationError, "artifact_hash_mismatch"):
            VERIFY.verify_run(self.run, frozen_directory=self.frozen)

    def test_rehashed_bad_public_metrics_still_rejected(self):
        self.report["views"]["2025_ONLY"]["horizons"]["7"]["metrics"]["zero"]["fold_count"] = 4
        with self.assertRaisesRegex(VERIFY.VerificationError, "public_summary_fold_count_failure"):
            self.verify()

    def test_training_target_leakage_rejected(self):
        self.forecasts[0]["training_end"] = self.forecasts[0]["origin"]
        with self.assertRaisesRegex(VERIFY.VerificationError, "training_target_leakage"):
            self.verify()

    def test_synthetic_target_disguised_as_real_rejected(self):
        self.forecasts[0]["target_sources"] = {"real_observed": 7}
        with self.assertRaisesRegex(VERIFY.VerificationError, "target_provenance_failure"):
            self.verify()

    def test_wrong_zero_forecast_rejected(self):
        self.forecasts[0]["predicted"][0] = 1
        with self.assertRaises(VERIFY.VerificationError):
            self.verify()

    def test_nonpositive_training_scale_rejected(self):
        self.forecasts[0]["metrics"]["mase_denominator"] = 0
        with self.assertRaisesRegex(VERIFY.VerificationError, "scaled_nonpositive_training_denominator"):
            self.verify()

    def test_target_scaled_declaration_rejected(self):
        self.forecasts[0]["metrics"]["scale_source"] = "targets"
        with self.assertRaisesRegex(VERIFY.VerificationError, "scale_source_failure"):
            self.verify()

    def test_nan_json_rejected_without_sensitive_values(self):
        with self.assertRaisesRegex(VERIFY.VerificationError, "nonfinite_json_constant"):
            VERIFY.decode('{"product_code":"private-test-code","value":NaN}')

    def test_frozen_hash_changed_rejected(self):
        self.write()
        (self.frozen / "empirical_2026_operational_calendar.csv").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(VERIFY.VerificationError, "frozen_input_hash_failure"):
            VERIFY.verify_run(self.run, frozen_directory=self.frozen)

    def test_current_source_hash_changed_rejected(self):
        self.write()
        (self.sources / "demand.csv").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(VERIFY.VerificationError, "current_structured_source_hash_failure"):
            VERIFY.verify_run(self.run, frozen_directory=self.frozen, source_directory=self.sources)

    def test_inventory_balance_corruption_rejected(self):
        self.inventory[0]["metrics"]["received_units"] = 3
        with self.assertRaisesRegex(VERIFY.VerificationError, "inventory_accounting_balance_failure"):
            self.verify()

    def test_zero_synthetic_snapshot_retains_source_provenance(self):
        row = inventory()
        row.update(view="2026_REAL_ONLY", scenario_id="synthetic_2025_snapshot_initial_stock",
                   initial_stock_data_kind="synthetic_structured", start="2026-01-01", end="2026-01-05")
        encoded = {key: json.dumps(value) if isinstance(value, dict) else value for key, value in row.items()}
        self.assertEqual(VERIFY._inventory_row(encoded)["ending_stock"], 0)

    def test_zero_synthetic_snapshot_cannot_be_relabelled_constant(self):
        row = inventory()
        row.update(view="2026_REAL_ONLY", scenario_id="synthetic_2025_snapshot_initial_stock",
                   start="2026-01-01", end="2026-01-05")
        encoded = {key: json.dumps(value) if isinstance(value, dict) else value for key, value in row.items()}
        with self.assertRaisesRegex(VERIFY.VerificationError, "inventory_initial_stock_provenance_failure"):
            VERIFY._inventory_row(encoded)

    def test_expanding_first_real_targets_allow_synthetic_only_training(self):
        row = forecast(origin="2026-04-08")
        row.update(view="2025_PLUS_2026_OBSERVED_TO_DATE", time_unit="operational_day",
                   training_end="2025-12-31", target_sources={"real_observed": 7})
        calendar = {VERIFY.date.fromisoformat(day): index for index, day in enumerate(row["target_dates"])}
        encoded = {key: json.dumps(value) if isinstance(value, (dict, list)) else value for key, value in row.items()}
        self.assertEqual(VERIFY._forecast_chronology(encoded, calendar)[0], row["actual"])

    def test_expanding_training_end_matches_sources_and_stays_before_targets(self):
        for sources, end, reason in (({"synthetic_structured": 60}, "2026-04-01", "training_end_year_failure"),
                                     ({"synthetic_structured": 59, "real_observed": 1}, "2025-12-31", "training_end_year_failure"),
                                     ({"real_observed": 60}, "2026-04-08", "training_target_leakage")):
            with self.subTest(sources=sources, end=end):
                row = forecast(origin="2026-04-08")
                row.update(view="2025_PLUS_2026_OBSERVED_TO_DATE", time_unit="operational_day",
                           training_end=end, training_sources=sources, target_sources={"real_observed": 7})
                calendar = {VERIFY.date.fromisoformat(day): index for index, day in enumerate(row["target_dates"])}
                encoded = {key: json.dumps(value) if isinstance(value, (dict, list)) else value for key, value in row.items()}
                with self.assertRaisesRegex(VERIFY.VerificationError, reason):
                    VERIFY._forecast_chronology(encoded, calendar)

    def test_observation_table_must_be_sealed(self):
        self.write()
        manifest = VERIFY.read_json(self.run / "artifact_sha256.json")
        del manifest["observations.csv"]
        json_write(self.run / "artifact_sha256.json", manifest)
        with self.assertRaisesRegex(VERIFY.VerificationError, "incomplete_artifact_manifest"):
            VERIFY.artifact_integrity(self.run)

    def test_configuration_exports_must_match(self):
        self.report["configuration"]["minimum_publicable_folds"] = 2
        with self.assertRaisesRegex(VERIFY.VerificationError, "configuration_export_disagreement"):
            VERIFY.verify_structure(self.report, self.configuration)

    def test_full_view_horizon_model_matrix_required(self):
        for level, reason in (("view", "incomplete_view_matrix"), ("horizon", "incomplete_horizon_matrix"),
                              ("model", "incomplete_model_matrix")):
            with self.subTest(level=level):
                report = copy.deepcopy(self.report)
                if level == "view":
                    del report["views"]["2026_REAL_ONLY"]
                elif level == "horizon":
                    del report["views"]["2026_REAL_ONLY"]["horizons"]["30"]
                else:
                    del report["views"]["2026_REAL_ONLY"]["horizons"]["30"]["metrics"]["adida"]
                with self.assertRaisesRegex(VERIFY.VerificationError, reason):
                    VERIFY.verify_structure(report, self.configuration)

    def test_unreported_inventory_group_rejected(self):
        self.inventory[0]["model"] = "adida"
        with self.assertRaisesRegex(VERIFY.VerificationError, "unreported_inventory_group"):
            self.verify()

    def test_no_db_network_or_application_import(self):
        self.write()
        with patch("socket.socket", side_effect=AssertionError("network")), patch("sqlite3.connect", side_effect=AssertionError("database")):
            result = VERIFY.verify_run(self.run, frozen_directory=self.frozen)
        self.assertTrue(result["no_database_or_application_imports"])
        self.assertNotIn("app", VERIFY.sys.modules)


if __name__ == "__main__":
    unittest.main(verbosity=2)
