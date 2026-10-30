"""Synthetic-only runner/reporting contracts. No real dataset, model fits or DB."""
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_empirical_2026_backtest as runner
import empirical_2026_reporting as reporting

TEMP_ROOT = runner.ROOT / ".tmp"
TEMP_ROOT.mkdir(exist_ok=True)


def synthetic_series(code=1, size=216, offset=0, constant=False):
    calendar = [date(2026, 1, 1) + timedelta(days=i) for i in range(size)]
    values = ([2.0] * (size - offset) if constant else
              [2.0 + i % 3 if i % 4 == 0 else 0.0 for i in range(size - offset)])
    return calendar, runner.TimeSeries(code, "daily", calendar[offset:], values, time_unit="operational_day")


def predictions(train, horizon, model, *args, **kwargs):
    return [1.0] * horizon


class RunnerTests(unittest.TestCase):
    def test_configuration_frozen(self):
        config = runner.configuration(False)
        self.assertEqual((config["horizon"], config["step"], config["min_train"], config["min_positive_train"]),
                         (7, 7, 60, 12))
        self.assertEqual(config["rf_unchanged"]["random_state"], 42)
        self.assertEqual(tuple(config["models"]), reporting.MODELS)

    def test_shared_22_origins_nonoverlap(self):
        calendar, point = synthetic_series()
        panel = runner.selected_panel({"0001": point}, calendar, False)
        self.assertEqual(panel[0][2], list(range(60, 210, 7)))
        self.assertEqual(len(panel[0][2]), 22)
        self.assertEqual(panel[0][2][-1] + 7, 214)

    def test_smoke_selection_train_only(self):
        calendar, first = synthetic_series()
        _, second = synthetic_series(2)
        before = runner.selected_panel({"0001": first, "0002": second}, calendar, True)
        first.values[67:] = [0.0] * (len(first.values) - 67)
        after = runner.selected_panel({"0001": first, "0002": second}, calendar, True)
        self.assertEqual([p[2] for p in before], [p[2] for p in after])
        self.assertEqual(before[0][2], [60, 67])

    def test_strata_fixed_edges(self):
        _, point = synthetic_series(size=100)
        for zeros, expected in ((49, 0), (50, 1), (79, 1), (80, 2), (94, 2), (95, 3)):
            point.values = [0.0] * zeros + [1.0] * (100 - zeros)
            self.assertEqual(reporting.descriptor(point)["stratum"], reporting.STRATA[expected])

    def test_quantile_and_ties(self):
        self.assertEqual(reporting.quantile([0, 10, 20, 30], .25), 7.5)
        self.assertIsNone(reporting.quantile([], .25))
        self.assertTrue(reporting.tied(1.0, 1.0 + 1e-10))
        self.assertFalse(reporting.tied(1.0, 1.1))

    def test_all_zero_valid_metrics_na(self):
        calendar, point = synthetic_series()
        point.values[60:81] = [0.0] * 21
        with patch.object(runner.forecast, "predict", side_effect=predictions):
            evaluation = runner.empirical.evaluate_operational(point, operational_calendar=calendar,
                explicit_origins=[60, 67, 74], product_code="0001")
        self.assertEqual(evaluation.valid_common_folds, 3)
        rows = reporting.product_rows(evaluation, point)
        for row in rows:
            self.assertTrue(row["publishable"])
            self.assertEqual((row["mae"], row["rmse"], row["bias"]), (1, 1, 1))
            self.assertIsNone(row["wape"])
            self.assertIsNone(row["mape"])
            self.assertEqual(row["mape_n"], 0)

    def test_original_denominator_includes_constant_arima_rejection(self):
        calendar, point = synthetic_series(constant=True)
        with patch.object(runner.forecast, "predict", side_effect=predictions):
            evaluation = runner.empirical.evaluate_operational(point, operational_calendar=calendar,
                explicit_origins=[60], product_code="0001")
        coverage = reporting.Coverage()
        for row in evaluation.rows:
            coverage.add(row, False)
        arima = next(row for row in coverage.rows() if row["model"] == "arima")
        self.assertEqual(arima["original_eligible_opportunities"], 1)
        self.assertEqual(arima["model_eligible_opportunities"], 0)
        self.assertEqual(arima["eligible_INELIGIBLE"], 1)
        self.assertEqual(arima["success_rate_original_eligible"], 0)

    def test_audit_writes_statuses_predictions_and_no_fallback(self):
        calendar, point = synthetic_series()

        def failed(train, horizon, model, *args, **kwargs):
            if model == "arima":
                raise runner.forecast.ModelUnavailable("synthetic unavailable")
            if model == "random_forest":
                raise RuntimeError("synthetic error")
            return predictions(train, horizon, model)

        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temp, patch.object(runner.forecast, "predict", side_effect=failed):
            output = Path(temp)
            rows, evaluations, coverage, _ = runner.evaluate_panel([("0001", point, [60])], calendar, output)
            self.assertEqual(evaluations, [])
            self.assertTrue(all(row["valid_common_folds"] == 0 for row in rows))
            folds = list(runner.read_csv(output / (runner.PREFIX + "folds.csv")))
            pairs = list(runner.read_csv(output / (runner.PREFIX + "predictions.csv")))
            failures = list(runner.read_csv(output / (runner.PREFIX + "failures.csv")))
            self.assertEqual((len(folds), len(pairs), len(failures)), (6, 42, 2))
            self.assertEqual([r["status"] for r in failures], ["MODEL_UNAVAILABLE", "ERROR"])
            self.assertTrue(all(row["predicted"] == "" for row in pairs if row["model"] in reporting.MODELS[4:]))
            self.assertEqual(next(row for row in coverage.rows() if row["model"] == "arima")["MODEL_UNAVAILABLE"], 1)

    def test_pooled_primary_same_publicable_pairs_and_fold_distribution(self):
        calendar, first = synthetic_series()
        _, second = synthetic_series(2)
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temp, patch.object(runner.forecast, "predict", side_effect=predictions):
            rows, evaluations, coverage, _ = runner.evaluate_panel(
                [("0001", first, [60, 67, 74]), ("0002", second, [60, 67])], calendar, Path(temp))
            summary = reporting.summarize(rows, evaluations, coverage, runner.empirical)
        self.assertEqual(summary["products_ge_3_common_folds"], 1)
        self.assertEqual(summary["products_1_or_2_common_folds"], 1)
        self.assertEqual(summary["common_fold_distribution"], {2: 1, 3: 1})
        self.assertEqual(summary["pooled_publishable_same_pairs"]["arima"]["n"], 21)
        self.assertEqual(summary["pooled_all_common_including_1_or_2_folds"]["arima"]["n"], 35)
        self.assertEqual(summary["macro_publishable"][0]["common_pairs"], 21)

    def test_comparison_preserves_ties_and_no_forced_winner(self):
        rows = [{"product_code": "0001", "model": model, "publishable": True,
                 "mae": 1.0, "rmse": 1.0, "wape": None} for model in reporting.MODELS]
        result = reporting.comparison(rows)
        self.assertEqual(result["mae"]["events"]["tied_minimum_products"]["products"], 1)
        self.assertEqual(result["mae"]["events"]["arima_tied_best_baseline"]["products"], 1)
        self.assertEqual(result["mae"]["events"]["neither_advanced_better"]["products"], 1)
        self.assertEqual(result["wape"]["defined_products"], 0)
        self.assertTrue(all(row["pct"] == 100 for row in result["mae"]["lowest_error"].values()))

    def test_left_censored_actual_not_zero(self):
        calendar, point = synthetic_series(offset=65)
        evaluation = runner.empirical.evaluate_operational(point, operational_calendar=calendar,
            explicit_origins=[60], product_code="0001")
        row = runner.serialize_fold(evaluation.rows[0], point, False)
        self.assertEqual(row["actual"], "[]")
        self.assertEqual(row["status"], "INELIGIBLE")
        self.assertIsNone(row["mae"])

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temp:
            target = Path(temp) / "record.json"
            runner.write_json(target, {"result": None})
            before = target.read_bytes()
            with self.assertRaises(FileExistsError):
                runner.write_json(target, {"result": 99})
            self.assertEqual(target.read_bytes(), before)

    def test_network_guard(self):
        with self.assertRaisesRegex(RuntimeError, "prohibited"):
            runner.offline_guard("socket.connect", ())

    def test_hash_report_failure_precedes_input_content(self):
        with patch.object(runner, "digest", return_value="altered"), patch.object(runner, "read_csv") as read:
            with self.assertRaisesRegex(ValueError, "Build report SHA-256 changed"):
                runner.integrity_inputs(Path("unused"))
            read.assert_not_called()

    def test_full_runner_synthetic_roundtrip_and_failure_diagnostic(self):
        calendar, point = synthetic_series()
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temp:
            output = Path(temp)
            source = output / "input.csv"
            source.write_text("synthetic\n", encoding="utf-8")
            inputs = {source.name: source}
            hashes = {source.name: runner.digest(source)}
            with patch.object(runner, "INPUT_DIR", output), \
                    patch.object(runner, "integrity_inputs", return_value=({}, inputs, hashes)), \
                    patch.object(runner, "source_identity", return_value=(inputs, hashes)), \
                    patch.object(runner, "load_panel", return_value=(calendar, {"0001": point})), \
                    patch.object(runner, "selected_panel", return_value=[("0001", point, [60, 67, 74])]), \
                    patch.object(runner, "guard_output"), \
                    patch.object(runner.forecast, "predict", side_effect=predictions):
                summary = runner.run(output)
            self.assertEqual(summary["status"], "COMPLETE")
            self.assertEqual(summary["generated_model_cells"], 18)
            manifest = json.loads((output / (runner.PREFIX + "artifact_hashes.json")).read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["sha256"]), 8)
            for name, expected in manifest["sha256"].items():
                self.assertEqual(runner.digest(output / name), expected)
            self.assertEqual(source.read_text(encoding="utf-8"), "synthetic\n")
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temp:
            output = Path(temp)
            with patch.object(runner, "INPUT_DIR", output), \
                    patch.object(runner, "integrity_inputs", return_value=({}, {}, {})), \
                    patch.object(runner, "source_identity", return_value=({}, {})), \
                    patch.object(runner, "load_panel", return_value=(calendar, {"0001": point})), \
                    patch.object(runner, "guard_output"), \
                    patch.object(runner, "evaluate_panel", side_effect=RuntimeError("synthetic interruption")):
                with self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
                    runner.run(output)
            diagnostic = json.loads((output / (runner.PREFIX + "diagnostic.json")).read_text(encoding="utf-8"))
            self.assertEqual(diagnostic["status"], "FAILED")
            self.assertFalse((output / (runner.PREFIX + "summary.json")).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
