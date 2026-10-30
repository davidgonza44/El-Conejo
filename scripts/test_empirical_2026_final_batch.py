"""Synthetic-only final batch contracts. No real data, fitted models or database."""
from contextlib import ExitStack
import csv
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_empirical_2026_final_batch as batch

TEMP_ROOT = batch.ROOT / ".tmp"
TEMP_ROOT.mkdir(exist_ok=True)


def panel(size=216, offset=0):
    calendar = [date(2026, 1, 1) + timedelta(days=i) for i in range(size)]
    values = [2.0 + i % 3 if i % 4 == 0 else 0.0 for i in range(size - offset)]
    point = batch.frozen.TimeSeries(1, "daily", calendar[offset:], values, time_unit="operational_day")
    return calendar, {"synthetic": point}


def predictions(train, horizon, model, *args, **kwargs):
    return [1.0] * horizon


class FinalBatchTests(unittest.TestCase):
    def test_only_final_three_configurations_and_correct_metadata(self):
        self.assertEqual(batch.EXPERIMENTS, (("STRICT", 30), ("SENSITIVITY", 7), ("SENSITIVITY", 30)))
        for cohort, horizon in batch.EXPERIMENTS:
            config = batch.configuration(cohort, horizon)
            self.assertEqual((config["cohort"], config["horizon"], config["step"]), (cohort, horizon, horizon))
            self.assertEqual((config["min_train"], config["min_positive_train"], config["min_publicable_common_folds"]), (60, 12, 3))
            self.assertEqual(config["rf_unchanged"], batch.frozen.configuration(False)["rf_unchanged"])
            self.assertNotIn("No 30-day", " ".join(config["limitations"]))
            self.assertIn(str((216 - 60) % horizon) + " final", " ".join(config["limitations"]))
        for invalid in (("STRICT", 7), ("STRICT", 90), ("QUARANTINE", 30), ("sensitivity", 7)):
            with self.assertRaises(ValueError):
                batch.configuration(*invalid)

    def test_shared_grids_are_complete_and_nonoverlapping(self):
        calendar, _ = panel()
        for horizon, expected in ((7, list(range(60, 208, 7))), (30, [60, 90, 120, 150, 180])):
            origins = batch.empirical.operational_origins(calendar, horizon=horizon, step=horizon)
            self.assertEqual(origins, expected)
            self.assertTrue(all(a + horizon <= b for a, b in zip(origins, origins[1:])))
            self.assertLessEqual(origins[-1] + horizon, len(calendar))

    def test_full_grid_30_serializes_all_pairs_and_retains_na(self):
        calendar, series = panel()
        series["synthetic"].values[60:] = [0.0] * 156
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temp, patch.object(batch, "INPUT_DIR", Path(temp)), \
                patch.object(batch.frozen.forecast, "predict", side_effect=predictions):
            products, evaluations, coverage, _, zeros = batch.evaluate_panel(series, calendar, 30, "synthetic_")
            with (Path(temp) / "synthetic_predictions.csv").open(encoding="utf-8", newline="") as handle:
                pairs = list(csv.DictReader(handle))
            self.assertEqual(len(pairs), 5 * 6 * 30)
            self.assertTrue(all(row["actual"] == "0.0" for row in pairs))
            self.assertEqual({row["target_index"] for row in pairs}, set(map(str, range(1, 31))))
            self.assertEqual(zeros["publicable_common_product_origins"], 5)
            self.assertTrue(all(row["valid_common_folds"] == 5 and row["wape"] is None and row["mape"] is None for row in products))
            summary = batch.reporting.summarize(products, evaluations, coverage, batch.empirical)
            self.assertEqual(summary["pooled_publishable_same_pairs"]["arima"]["n"], 150)

    def test_failures_never_become_predictions_or_common_support(self):
        calendar, series = panel()

        def fail(train, horizon, model, *args, **kwargs):
            if model == "arima":
                raise batch.frozen.forecast.ModelUnavailable("synthetic")
            if model == "random_forest":
                raise RuntimeError("synthetic")
            return predictions(train, horizon, model)

        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temp, patch.object(batch, "INPUT_DIR", Path(temp)), \
                patch.object(batch.frozen.forecast, "predict", side_effect=fail):
            products, evaluations, coverage, _, _ = batch.evaluate_panel(series, calendar, 30, "synthetic_")
            failures = list(batch.frozen.read_csv(Path(temp) / "synthetic_failures.csv"))
            pairs = list(batch.frozen.read_csv(Path(temp) / "synthetic_predictions.csv"))
            self.assertEqual(evaluations, [])
            self.assertEqual(len(failures), 10)
            self.assertEqual({row["status"] for row in failures}, {"MODEL_UNAVAILABLE", "ERROR"})
            self.assertTrue(all(row["predicted"] == "" for row in pairs if row["model"] in batch.reporting.MODELS[4:]))
            self.assertTrue(all(row["valid_common_folds"] == 0 for row in products))
            self.assertEqual(coverage.rows()[4]["original_eligible_opportunities"], 5)

    def test_left_censored_fold_retains_absent_actuals(self):
        calendar, series = panel(offset=65)
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temp, patch.object(batch, "INPUT_DIR", Path(temp)), \
                patch.object(batch.frozen.forecast, "predict", side_effect=predictions):
            batch.evaluate_panel(series, calendar, 30, "synthetic_")
            pairs = list(batch.frozen.read_csv(Path(temp) / "synthetic_predictions.csv"))
            first = [row for row in pairs if row["origin"] == "60"]
            self.assertEqual(len(first), 180)
            self.assertTrue(all(row["actual"] == "" and row["predicted"] == "" for row in first))

    def test_batch_prechecks_every_destination_before_any_run(self):
        with patch.object(batch, "guard_output", side_effect=[None, ValueError("existing")]), patch.object(batch, "run") as run:
            with self.assertRaises(ValueError):
                batch.run_batch(Path("synthetic"))
            run.assert_not_called()
        calls = []
        with patch.object(batch, "guard_output"), patch.object(batch, "run", side_effect=lambda source, cohort, horizon: calls.append((cohort, horizon))):
            batch.run_batch(Path("synthetic"))
        self.assertEqual(calls, list(batch.EXPERIMENTS))

    def test_existing_output_rejected_without_overwrite(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temp:
            root = Path(temp)
            (root / ".repomixignore").write_text("artifacts/empirical_2026/**\n", encoding="utf-8")
            prefix = batch.prefix("STRICT", 30)
            target = root / (prefix + "summary.json")
            target.write_text("untouched", encoding="utf-8")
            with patch.object(batch, "ROOT", root), patch.object(batch, "INPUT_DIR", root), \
                    patch.object(batch.subprocess, "run") as ignored:
                ignored.return_value.returncode = 0
                with self.assertRaisesRegex(ValueError, "never overwrite"):
                    batch.guard_output(prefix)
            self.assertEqual(target.read_text(encoding="utf-8"), "untouched")

    def synthetic_run_context(self, stack, output, calendar, series):
        source = output / "synthetic_input.csv"
        source.write_text("synthetic\n", encoding="utf-8")
        paths = {source.name: source}
        hashes = {source.name: batch.frozen.digest(source)}
        prior = {"versions": {name: batch.version(name) for name in batch.PACKAGES}, "python": batch.platform.python_version()}
        for target, name, value in ((batch, "INPUT_DIR", output), (batch, "frozen_snapshot", (paths, hashes, prior)),
                (batch.frozen, "integrity_inputs", ({}, paths, hashes)), (batch, "source_identity", (paths, hashes)),
                (batch, "load_panel", (calendar, series))):
            if name == "INPUT_DIR":
                stack.enter_context(patch.object(target, name, value))
            else:
                stack.enter_context(patch.object(target, name, return_value=value))
        stack.enter_context(patch.object(batch, "guard_output"))
        stack.enter_context(patch.dict(batch.os.environ, {"PYTHONHASHSEED": "0"}))
        stack.enter_context(patch.object(batch.frozen.forecast, "predict", side_effect=predictions))
        return source

    def test_each_run_roundtrip_has_separate_hashes_and_identity(self):
        calendar, series = panel()
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temp, ExitStack() as stack:
            output = Path(temp)
            source = self.synthetic_run_context(stack, output, calendar, series)
            for cohort, horizon in batch.EXPERIMENTS:
                result = batch.run(output, cohort, horizon)
                prefix = batch.prefix(cohort, horizon)
                manifest = json.loads((output / (prefix + "manifest.json")).read_text(encoding="utf-8"))
                hashes = json.loads((output / (prefix + "artifact_hashes.json")).read_text(encoding="utf-8"))["sha256"]
                self.assertEqual(len(hashes), 8)
                self.assertTrue(all(name.startswith(prefix) for name in hashes))
                self.assertTrue(all(batch.frozen.digest(output / name) == sha for name, sha in hashes.items()))
                self.assertEqual(result["status"], "COMPLETE")
                self.assertEqual(result["code_sha256_after"], manifest["code_sha256_before"])
                self.assertEqual(result["frozen_strict7_sha256_after"], manifest["frozen_strict7_sha256_before"])
            self.assertEqual(len(list(output.glob("*_artifact_hashes.json"))), 3)
            self.assertEqual(source.read_text(encoding="utf-8"), "synthetic\n")

    def test_failed_run_diagnostic_and_stops_later_runs(self):
        calendar, series = panel()
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temp, ExitStack() as stack:
            output = Path(temp)
            self.synthetic_run_context(stack, output, calendar, series)
            stack.enter_context(patch.object(batch, "evaluate_panel", side_effect=RuntimeError("synthetic interruption")))
            with self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
                batch.run_batch(output)
            prefix = batch.prefix("STRICT", 30)
            diagnostic = json.loads((output / (prefix + "diagnostic.json")).read_text(encoding="utf-8"))
            self.assertEqual(diagnostic["status"], "FAILED")
            self.assertFalse((output / (prefix + "summary.json")).exists())
            self.assertFalse(any(output.glob("*sensitivity*")))

    def test_source_identity_covers_only_explicit_adapter_sources(self):
        with patch.object(batch.frozen, "source_identity", return_value=({}, {})), \
                patch.object(batch.frozen, "digest", return_value="synthetic"):
            paths, hashes = batch.source_identity()
        self.assertEqual(set(paths), {"scripts/run_empirical_2026_final_batch.py", "scripts/test_empirical_2026_final_batch.py"})
        self.assertEqual(set(hashes), set(paths))

    def test_required_hashseed_is_enforced_before_source_access(self):
        with patch.dict(batch.os.environ, {"PYTHONHASHSEED": "1"}), patch.object(batch, "frozen_snapshot") as snapshot:
            with self.assertRaisesRegex(ValueError, "PYTHONHASHSEED"):
                batch.run(Path("synthetic"), "STRICT", 30)
            snapshot.assert_not_called()

    def test_runtime_input_mutation_invalidates_run(self):
        calendar, series = panel()
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temp, ExitStack() as stack:
            output = Path(temp)
            source = self.synthetic_run_context(stack, output, calendar, series)
            evaluate = batch.evaluate_panel

            def mutate(*args):
                result = evaluate(*args)
                source.write_text("changed synthetic input\n", encoding="utf-8")
                return result

            stack.enter_context(patch.object(batch, "evaluate_panel", side_effect=mutate))
            with self.assertRaisesRegex(ValueError, "Input SHA-256 changed"):
                batch.run(output, "STRICT", 30)
            prefix = batch.prefix("STRICT", 30)
            self.assertTrue((output / (prefix + "diagnostic.json")).exists())
            self.assertFalse((output / (prefix + "summary.json")).exists())

    def test_loader_selects_only_declared_cohort(self):
        calendar, _ = panel()
        calendar_rows = [{"fecha_real": str(day), "operational_index": str(index)} for index, day in enumerate(calendar, 1)]
        manifest = [{"codigo_producto": "synthetic", "cohorte": "SENSITIVITY", "is_service": "false"},
                    {"codigo_producto": "not_selected", "cohorte": "STRICT", "is_service": "false"}]
        values = [{"codigo_producto": "synthetic", "cohorte": "SENSITIVITY", "is_service": "false",
                   "fecha_real": str(day), "demanda_observada": "1", "product_operational_index": str(index),
                   "operational_index": str(index)} for index, day in enumerate(calendar, 1)]
        inputs = {"empirical_2026_operational_calendar.csv": calendar_rows,
                  "empirical_2026_cohort_manifest.csv": manifest, "empirical_2026_series_sensitivity.csv": values}
        with patch.dict(batch.COHORT_SIZES, {"SENSITIVITY": 1}), \
                patch.object(batch.frozen, "read_csv", side_effect=lambda path: iter(inputs[path.name])):
            actual_calendar, series = batch.load_panel("SENSITIVITY")
            self.assertEqual(actual_calendar, calendar)
            self.assertEqual(list(series), ["synthetic"])
            values[-1]["cohorte"] = "STRICT"
            with self.assertRaisesRegex(ValueError, "Wrong cohort"):
                batch.load_panel("SENSITIVITY")


if __name__ == "__main__":
    unittest.main(verbosity=2)
