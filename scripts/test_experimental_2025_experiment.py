"""Chronology, provenance, inventory and frozen-input guards on synthetic fixtures."""
from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from experimental_prediction_tools import load_service
from run_experimental_predictive_regressions import offline_guard

sys.addaudithook(offline_guard)
experiment = load_service("experimental_2025_experiment")
simulation = load_service("experimental_2025_simulation")
temporal = load_service("experimental_2025_temporal")
models = load_service("experimental_2025_models")


def points(year, count, quantity=2.0, *, start=None, kind=None):
    start = start or date(year, 1, 1)
    return [{"date": start + timedelta(days=index), "quantity": float(quantity),
             "data_kind": kind or ("synthetic_structured" if year == 2025 else "real_observed"),
             "source_id": "synthetic_test_fixture", "year": year} for index in range(count)]


def panel2026(count=95):
    observations = points(2026, count)
    return {"0001": observations}, [item["date"] for item in observations]


def evaluate(panel, calendar, view="2026_REAL_ONLY", **kwargs):
    return experiment.evaluate_view(panel, calendar, universe=["0001"], view=view, horizon=7, **kwargs)


def small_frozen_inputs():
    calendar = [date(2026, 1, 1) + timedelta(days=index) for index in range(216)]
    manifests = [{"codigo_producto": f"{index:04d}", "cohorte": "STRICT",
                  "nombre_actual": "Fixture", "is_service": "false"} for index in range(2087)]
    rows = [{"codigo_producto": item["codigo_producto"], "fecha_real": calendar[-1].isoformat(),
             "demanda_observada": "1", "cohorte": "STRICT", "product_operational_index": "1",
             "operational_index": "216"} for item in manifests]
    report = {"READY_FOR_BACKTEST": True, "source_sha256_before": {"productos_2026.csv": "catalog-hash"},
              "source_sha256_after": {"productos_2026.csv": "catalog-hash"},
              "artifact_sha256": {filename: "artifact-hash" for filename in temporal.FILES}}
    tables = {temporal.FILES[0]: manifests,
              temporal.FILES[1]: [{"fecha_real": day.isoformat()} for day in calendar],
              temporal.FILES[2]: rows}
    return tables, report


class ChronologyTests(unittest.TestCase):
    def test_minimum_train_and_positive_requirements(self):
        self.assertEqual(experiment.eligibility(points(2026, 59)), "insufficient_training_periods")
        sparse = points(2026, 60, 0)
        for index in range(7):
            sparse[index]["quantity"] = 1
        self.assertEqual(experiment.eligibility(sparse), "insufficient_positive_training_periods")
        self.assertIsNone(experiment.eligibility(points(2026, 60)))

    def test_each_origin_uses_only_preorigin_training(self):
        panel, calendar = panel2026()
        _, groups = evaluate(panel, calendar)
        for rows in groups.values():
            for row in rows:
                self.assertLess(row["training_end"], row["origin"])
                self.assertGreaterEqual(row["training_count"], experiment.MIN_TRAIN)
                self.assertTrue(all(day.startswith("2026-") for day in row["target_dates"]))
                self.assertEqual(set(row["target_sources"]), {"real_observed"})

    def test_transfer_and_expanding_provenance_remain_separate(self):
        panel, calendar = panel2026()
        history = {"0001": points(2025, 365)}
        for view in experiment.VIEWS[2:]:
            _, groups = evaluate(panel, calendar, view, history_panel=history,
                                 bridge={"0001": {"unit": "UNIDAD"}})
            row = groups["mean"][0]
            self.assertEqual(row["target_sources"], {"real_observed": 7})
            self.assertEqual(row["training_sources"]["synthetic_structured"], 365)
            if view == "2025_TO_2026_TRANSFER":
                self.assertNotIn("real_observed", row["training_sources"])
                self.assertEqual(row["training_end"], "2025-12-31")
            else:
                self.assertEqual(row["training_sources"]["real_observed"], 60)

    def test_changing_future_real_targets_does_not_change_earlier_forecasts(self):
        panel, calendar = panel2026()
        changed = deepcopy(panel)
        for item in changed["0001"][80:]:
            item["quantity"] = 1000
        history = {"0001": points(2025, 300)}
        kwargs = {"history_panel": history, "bridge": {"0001": {"unit": "UNIDAD"}}}
        for view in ("2026_REAL_ONLY", "2025_TO_2026_TRANSFER", "2025_PLUS_2026_OBSERVED_TO_DATE"):
            _, first = evaluate(panel, calendar, view, **kwargs)
            _, second = evaluate(changed, calendar, view, **kwargs)
            for model in models.MODELS:
                if first[model]:
                    self.assertEqual(first[model][0]["predicted"], second[model][0]["predicted"])
                    self.assertEqual(first[model][0]["two_part"], second[model][0]["two_part"])

    def test_history_filter_never_accepts_postorigin_points(self):
        origin = date(2025, 3, 2)
        history = points(2025, 90)
        result = experiment.training([], origin, "2025_TO_2026_TRANSFER", history)
        self.assertTrue(all(item["date"] < origin for item in result))
        self.assertTrue(all(item["year"] == 2025 for item in result))

    def test_transfer_history_rejects_real_or_non2025_contamination(self):
        origin = date(2026, 3, 2)
        history = points(2025, 60) + points(2026, 2, start=origin + timedelta(days=1))
        with self.assertRaises(ValueError):
            experiment.training(points(2026, 80), origin, "2025_TO_2026_TRANSFER", history)

    def test_invalid_view_and_target_provenance_rejected(self):
        panel, calendar = panel2026()
        with self.assertRaises(ValueError):
            evaluate(panel, calendar, "unknown")
        wrong = deepcopy(panel)
        wrong["0001"][70]["data_kind"] = "synthetic_structured"
        with self.assertRaises(ValueError):
            evaluate(wrong, calendar)

    def test_short_2025_calendar_returns_no_origins(self):
        self.assertEqual(experiment.origins([date(2025, 1, 1)], 7, "2025_ONLY"), [])

    def test_unverified_cross_year_unit_never_transfers(self):
        panel, calendar = panel2026()
        for view in experiment.VIEWS[2:]:
            summary, groups = evaluate(panel, calendar, view,
                                       history_panel={"0001": points(2025, 365)})
            self.assertTrue(all(not rows for rows in groups.values()))
            self.assertEqual(summary["coverage"]["mean"]["sku"]["numerator"], 0)
            self.assertIn("cross_year_identity_or_unit_unverified",
                          summary["abstention"]["mean"]["abstain"]["reasons"])

    def test_coverage_volume_uses_union_of_nonoverlapping_unique_targets(self):
        panel, calendar = panel2026()
        summary, groups = evaluate(panel, calendar)
        dates = {target for row in groups["mean"] for target in row["target_dates"]}
        folds = groups["mean"]
        self.assertEqual(len(dates), sum(len(row["target_dates"]) for row in folds))
        self.assertEqual(summary["coverage"]["mean"]["volume"]["denominator"], len(dates) * 2)
        self.assertEqual(summary["coverage"]["mean"]["sku"]["denominator"], 1)

    def test_temporal_comparison_uses_explicit_common_pairs(self):
        panel, calendar = panel2026()
        _, real = evaluate(panel, calendar)
        _, expanding = evaluate(panel, calendar, "2025_PLUS_2026_OBSERVED_TO_DATE",
                                 history_panel={"0001": points(2025, 365)}, bridge={"0001": {"unit": "UNIDAD"}})
        comparison = experiment.temporal_comparison(real, expanding)
        self.assertEqual(comparison["mean"]["same_pairs"], len(real["mean"]))
        self.assertEqual(comparison["mean"]["real_only"]["actual_total"],
                         comparison["mean"]["expanding"]["actual_total"])
        self.assertFalse(comparison["mean"]["scenario_selected"])

    def test_temporal_comparison_rejects_target_values_mismatch(self):
        panel, calendar = panel2026()
        _, groups = evaluate(panel, calendar)
        changed = deepcopy(groups)
        changed["mean"][0]["actual"][0] = 80
        with self.assertRaises(ValueError):
            experiment.temporal_comparison(groups, changed)

    def test_full_fixture_pipeline_reproducible_and_finite(self):
        panel, calendar = panel2026()
        first, _ = evaluate(panel, calendar)
        second, _ = evaluate(panel, calendar)
        self.assertEqual(first, second)
        json.dumps(first, allow_nan=False)


class InventoryChronologyTests(unittest.TestCase):
    def test_2025_terminal_snapshot_never_initializes_earlier_simulation(self):
        observations = points(2025, 250)
        panel, calendar = {"0001": observations}, [item["date"] for item in observations]
        snapshot = [{"product_code": "0001", "stock": 900, "usable_for_demand_unit": True,
                     "snapshot_date": "2025-12-31", "data_kind": "synthetic_structured"}]
        result = simulation.simulate_view(panel, calendar, view="2025_ONLY", horizon=7,
                                          inventory_context=snapshot, bridge={"0001": {"unit": "UNIDAD"}})
        self.assertTrue(result["results"])
        self.assertTrue(all(key.startswith("zero_initial_stock") for key in result["results"]))

    def test_2026_snapshot_sensitivity_is_explicit_and_precedes_simulation(self):
        panel, calendar = panel2026()
        emitted = []
        snapshot = [{"product_code": "0001", "stock": 20, "usable_for_demand_unit": True,
                     "snapshot_date": "2025-12-31", "data_kind": "synthetic_structured"}]
        result = simulation.simulate_view(panel, calendar, view="2026_REAL_ONLY", horizon=7,
                                          inventory_context=snapshot, bridge={"0001": {"unit": "UNIDAD"}},
                                          emit=emitted.append)
        self.assertIn("synthetic_2025_snapshot_initial_stock:mean", result["results"])
        for row in emitted:
            self.assertTrue(all(abs(value) < 1e-7 for value in row["accounting"].values()))
            if row["scenario_id"] == "synthetic_2025_snapshot_initial_stock":
                self.assertEqual(row["initial_stock_data_kind"], "synthetic_structured")

    def test_future_snapshot_is_excluded_without_lookahead(self):
        panel, calendar = panel2026()
        snapshot = [{"product_code": "0001", "stock": 900, "usable_for_demand_unit": True,
                     "snapshot_date": "2026-12-31", "data_kind": "synthetic_structured"}]
        result = simulation.simulate_view(panel, calendar, view="2026_REAL_ONLY", horizon=7,
                                          inventory_context=snapshot, bridge={"0001": {"unit": "UNIDAD"}})
        self.assertFalse(any(key.startswith("synthetic_2025_snapshot") for key in result["results"]))

    def test_review_forecast_never_contains_current_or_future_target(self):
        panel, calendar = panel2026()
        original = simulation.models.forecast_candidate
        seen = []
        def inspected(model, history, starts, horizon, **kwargs):
            seen.append(starts[-1])
            return original(model, history, starts, horizon, **kwargs)
        with patch.object(simulation.models, "forecast_candidate", side_effect=inspected):
            first = simulation.simulate_view(panel, calendar, view="2026_REAL_ONLY", horizon=7)
        self.assertTrue(seen)
        self.assertTrue(all(day < calendar[-1] for day in seen))
        second = simulation.simulate_view(panel, calendar, view="2026_REAL_ONLY", horizon=7)
        self.assertEqual(first, second)
        json.dumps(first, allow_nan=False)


class TemporalSourceTests(unittest.TestCase):
    def test_2026_loader_rejects_manifest_hash_before_reading_observations(self):
        with patch.object(temporal, "digest", return_value="changed"), \
                patch.object(temporal, "csv_rows", side_effect=AssertionError("Source read prohibited")):
            with self.assertRaisesRegex(ValueError, "manifest changed"):
                temporal.load_verified_2026("unused_fixture_path")

    def test_verified_fixture_keeps_real_provenance_and_codes(self):
        tables, report = small_frozen_inputs()
        def hashed(path):
            return temporal.BUILD_SHA256 if Path(path).name.endswith("build_report.json") else "artifact-hash"
        with patch.object(temporal, "digest", side_effect=hashed), \
                patch.object(Path, "read_text", return_value=json.dumps(report)), \
                patch.object(temporal, "csv_rows", side_effect=lambda path: tables[Path(path).name]):
            result = temporal.load_verified_2026("unused_fixture_path")
        self.assertEqual(result["metadata"]["data_kind"], "real_observed")
        self.assertIn("0001", result["observations"])
        self.assertEqual(result["observations"]["0001"][0]["year"], 2026)

    def test_2026_loader_rejects_nonfinite_and_wrong_operational_index(self):
        for field, value in (("demanda_observada", "Infinity"), ("operational_index", "215")):
            tables, report = small_frozen_inputs()
            tables[temporal.FILES[2]][0][field] = value
            def hashed(path):
                return temporal.BUILD_SHA256 if Path(path).name.endswith("build_report.json") else "artifact-hash"
            with patch.object(temporal, "digest", side_effect=hashed), \
                    patch.object(Path, "read_text", return_value=json.dumps(report)), \
                    patch.object(temporal, "csv_rows", side_effect=lambda path: tables[Path(path).name]):
                with self.assertRaises(ValueError):
                    temporal.load_verified_2026("unused_fixture_path")

    def test_catalog_must_match_exact_frozen_source_hash(self):
        _, report = small_frozen_inputs()
        with patch.object(Path, "read_text", return_value=json.dumps(report)), \
                patch.object(temporal, "digest", return_value="changed"), \
                patch.object(temporal, "csv_rows", side_effect=AssertionError("Catalog read prohibited")):
            with self.assertRaisesRegex(ValueError, "catalog differs"):
                temporal.load_external_units(None, "unused_fixture_path", catalog_path="catalog_fixture.csv")

    def test_catalog_unit_source_is_explicit_and_missing_source_unavailable(self):
        _, report = small_frozen_inputs()
        with patch.object(Path, "read_text", return_value=json.dumps(report)), \
                patch.object(temporal, "digest", return_value="catalog-hash"), \
                patch.object(temporal, "csv_rows", return_value=[{"codigo": "0001", "unidad_deventa_a": " UNIDAD "}]):
            units, metadata = temporal.load_external_units(None, "unused_fixture_path", catalog_path="catalog_fixture.csv")
            self.assertEqual(units, {"0001": "UNIDAD"})
            self.assertTrue(metadata["available"])
            units, metadata = temporal.load_external_units(None, "unused_fixture_path")
            self.assertEqual(units, {})
            self.assertFalse(metadata["available"])

    def test_identity_bridge_requires_code_name_and_unit(self):
        bundle = {"product_identity_by_code": {"0001": {"name": "Clavos", "unit": "UNIDAD"}}}
        external = {"manifest": {"0001": {"nombre_actual": " CLAVOS ", "is_service": "false"}}}
        bridge, reasons = temporal.identity_bridge(bundle, external)
        self.assertFalse(bridge)
        self.assertEqual(reasons, {"cross_year_unit_unverified": 1})
        bridge, _ = temporal.identity_bridge(bundle, external, external_units={"0001": "UNIDAD"})
        self.assertIn("0001", bridge)
        bridge, reasons = temporal.identity_bridge(bundle, external, external_units={"0001": "CAJA"})
        self.assertFalse(bridge)
        self.assertEqual(reasons, {"cross_year_unit_mismatch": 1})
        external["manifest"]["0001"]["nombre_actual"] = "Tuerca"
        self.assertFalse(temporal.identity_bridge(bundle, external, external_units={"0001": "UNIDAD"})[0])

    def test_synthetic_zero_scenario_never_extends_before_first_source_positive(self):
        bundle = {"observations": [{"product_code": "0001", "date": "2025-01-01", "quantity": 2},
                                    {"product_code": "0001", "date": "2025-01-04", "quantity": 2},
                                    {"product_code": "0002", "date": "2025-01-03", "quantity": 1}]}
        calendar_panel, calendar = temporal.scenario_panel(bundle)
        operational_panel, activity = temporal.scenario_panel(bundle, activity_calendar=True)
        self.assertEqual(len(calendar), 4)
        self.assertEqual(len(activity), 3)
        self.assertEqual(calendar_panel["0002"][0]["date"], date(2025, 1, 3))
        self.assertEqual(operational_panel["0002"][0]["date"], date(2025, 1, 3))
        zeros = [item for item in calendar_panel["0001"] if item["quantity"] == 0]
        self.assertTrue(all(item["observation_basis"].startswith("assumed_zero") for item in zeros))
        self.assertTrue(all(item["data_kind"] == "synthetic_structured" for item in zeros))


if __name__ == "__main__":
    unittest.main(verbosity=2)
