"""Audit, execute and export isolated 2025/2026 experiments. No database access."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sys

from experimental_2025_runtime import ROOT, activate_guard, service


def write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")


class Table:
    def __init__(self, path, fields):
        self.handle = path.open("x", encoding="utf-8", newline="")
        self.fields = fields
        self.writer = csv.DictWriter(self.handle, fieldnames=fields)
        self.writer.writeheader()

    def write(self, row):
        self.writer.writerow({key: json.dumps(row.get(key), ensure_ascii=False, sort_keys=True, allow_nan=False)
                              if isinstance(row.get(key), (list, dict)) else row.get(key)
                              for key in self.fields})

    def close(self):
        self.handle.close()


def source_paths(directory):
    return [directory / name for name in ("Demanda_limpia_2025_CORREGIDA_FINAL.csv",
                                          "Movimientos_Detalle_2025_CORREGIDO_FINAL.csv",
                                          "Existencias_2025_CORREGIDAS_FINAL.csv")]


def execute(arguments):
    activate_guard()
    source = service("experimental_sources_2025")
    temporal = service("experimental_2025_temporal")
    experiment = service("experimental_2025_experiment")
    simulation = service("experimental_2025_simulation")
    code_paths = [Path(module.__file__) for name, module in sys.modules.items()
                  if name.startswith("experimental2025_offline.") and getattr(module, "__file__", None)]
    code_paths += [Path(__file__).resolve(), ROOT / "scripts/experimental_2025_runtime.py"]
    code_before = {path.relative_to(ROOT).as_posix(): temporal.digest(path) for path in sorted(set(code_paths))}
    frozen_paths = sorted(path for path in arguments.verified_2026.iterdir() if path.is_file())
    frozen_before = {path.name: temporal.digest(path) for path in frozen_paths}
    output = arguments.output.resolve()
    allowed = (ROOT / "reports/generated/experimental_2025").resolve()
    if not output.is_relative_to(allowed) or output == allowed:
        raise ValueError("Output must be a dedicated run directory under reports/generated/experimental_2025")
    if output.exists():
        raise ValueError("Run directory already exists; never overwrite previous results")
    paths = source_paths(arguments.source_2025)
    before = {path.name: temporal.digest(path) for path in paths}
    bundle = source.load_2025_bundle(*paths)
    external = temporal.load_verified_2026(arguments.verified_2026)
    units, unit_audit = temporal.load_external_units(arguments.source_2026, arguments.verified_2026,
                                                    catalog_path=arguments.catalog_2026)
    bridge, bridge_reasons = temporal.identity_bridge(bundle, external, external_units=units)
    panel, calendar = temporal.scenario_panel(bundle)
    history, activity_dates = temporal.scenario_panel(bundle, activity_calendar=True)
    inventory_universe = sorted({r["product_code"] for r in bundle["inventory_context"]})
    real_universe = sorted(external["observations"])
    profiles, characterization = experiment.characterize(panel, inventory_universe, "calendar_day")
    output.mkdir(parents=True)
    write_json(output / "audit_2025.json", bundle["audit"])
    write_json(output / "configuration.json", configuration(experiment, external, bundle))
    tables = create_tables(output)
    report = {"metadata": {**bundle["metadata"], "evaluation_label": "EVALUACIÓN EXPERIMENTAL 2025",
                            "evaluation_2026": external["metadata"],
                            "sources_2025_sha256": before, "inputs_2026_sha256": external["input_sha256"]},
              "audit": bundle["audit"], "characterization": {"2025_ONLY": characterization},
              "identity_bridge": {"matched_products": len(bridge), "reasons": bridge_reasons,
                                  "units_2026": unit_audit, "renumbering_performed": False},
              "views": {name: {"horizons": {}} for name in experiment.VIEWS},
              "configuration": configuration(experiment, external, bundle),
              "temporal_comparison": {}, "limitations": limitations(),
              "read_only": True, "live_integration": False, "scientific_status": "experimental_evaluation"}
    try:
        for code, value in profiles.items():
            tables["profiles"].write({"product_code": code, **value, "data_kind": "synthetic_structured",
                                       "year": 2025, "purpose": "experimental_evaluation"})
        for row in bundle["inventory_context"]:
            tables["context"].write(row)
        export_observations(tables["observations"], bundle, panel, history, external["observations"], units)
        for horizon in (7, 30):
            selection = experiment.development_selection(panel, horizon)
            real_groups = None
            real_inventory = None
            for view in experiment.VIEWS:
                local_panel, local_calendar = (panel, calendar) if view == "2025_ONLY" else (
                    external["observations"], external["calendar"])
                universe = inventory_universe if view == "2025_ONLY" else real_universe
                print(json.dumps({"phase": "evaluation", "view": view, "horizon": horizon,
                                  "universe": len(universe), "verified_transfer_skus": len(bridge)}), flush=True)
                summary, groups = experiment.evaluate_view(
                    local_panel, local_calendar, universe=universe, view=view, horizon=horizon,
                    history_panel=history, bridge=bridge, baseline_selection=selection,
                    emit=tables["forecasts"].write)
                inventory_rows = []
                def emit_inventory(row):
                    inventory_rows.append(row)
                    tables["inventory"].write(row)
                summary["inventory"] = simulation.simulate_view(
                    local_panel, local_calendar, view=view, horizon=horizon, history_panel=history,
                    bridge=bridge, inventory_context=bundle["inventory_context"],
                    emit=emit_inventory)
                report["views"][view]["horizons"][str(horizon)] = summary
                if view == "2026_REAL_ONLY":
                    real_groups = groups
                    real_inventory = inventory_rows
                if view == "2025_PLUS_2026_OBSERVED_TO_DATE":
                    report["temporal_comparison"][str(horizon)] = experiment.temporal_comparison(real_groups, groups)
                    report["temporal_comparison"][str(horizon)]["inventory"] = experiment.statistics.paired_inventory_comparison(
                        inventory_rows, real_inventory)
                export_metrics(tables["metrics"], view, horizon, summary)
            # Groups from one horizon are released before the next run.
            real_groups = None
        after = {path.name: temporal.digest(path) for path in paths}
        if before != after:
            raise ValueError("Source files changed during read-only evaluation")
        external_after = temporal.load_verified_2026(arguments.verified_2026)["input_sha256"]
        if external["input_sha256"] != external_after:
            raise ValueError("Frozen 2026 inputs changed during evaluation")
        code_after = {path.relative_to(ROOT).as_posix(): temporal.digest(path) for path in sorted(set(code_paths))}
        frozen_after = {path.name: temporal.digest(path) for path in frozen_paths}
        if code_before != code_after or frozen_before != frozen_after:
            raise ValueError("Execution code or frozen 2026 artifacts changed during run")
        report["integrity"] = {"sources_2025_unchanged": before == after,
                               "frozen_2026_inputs_unchanged": external_after == external["input_sha256"],
                               "sources_2025_sha256_after": after,
                               "code_sha256": code_after, "code_unchanged": code_before == code_after,
                               "frozen_2026_all_artifacts_sha256": frozen_after,
                               "frozen_2026_all_artifacts_unchanged": frozen_before == frozen_after,
                               "no_database": True, "no_real_orders": True}
        write_json(output / "results.json", report)
    finally:
        for table in tables.values():
            table.close()
    hashes = {path.name: temporal.digest(path) for path in sorted(output.iterdir()) if path.is_file()}
    write_json(output / "artifact_sha256.json", hashes)
    print(json.dumps({"status": "COMPLETE", "audit_gate": bundle["audit"].get("gate"),
                      "output": str(output.relative_to(ROOT)), "matched_transfer_skus": len(bridge)}, ensure_ascii=False))
    return report


def create_tables(output):
    return {
        "profiles": Table(output / "profiles_2025.csv", ("product_code", "data_kind", "year", "purpose",
            "positive_count", "zero_rate", "adi", "cv2", "zero_rate_segment", "adi_cv2_classification",
            "observations_available", "evaluable", "reason")),
        "context": Table(output / "inventory_context_2025.csv", ("product_code", "unit", "stock",
            "last_movement_date", "snapshot_date", "snapshot_date_basis", "kardex_status",
            "usable_for_demand_unit", "data_kind", "year", "purpose")),
        "forecasts": Table(output / "forecasts.csv", ("view", "horizon", "time_unit", "product_code", "model",
            "origin", "target_dates", "actual", "predicted", "metrics", "two_part", "uncertainty",
            "prediction_policy", "training_count", "training_end", "training_sources", "target_sources", "profile")),
        "inventory": Table(output / "inventory_simulations.csv", ("view", "horizon", "time_unit", "product_code",
            "model", "scenario_id", "start", "end", "scenario", "metrics", "accounting", "initial_stock_data_kind")),
        "metrics": Table(output / "metrics.csv", ("view", "horizon", "time_unit", "model", "sku_count", "fold_count",
            "n", "wape", "mae", "rmse", "bias", "mase", "rmsse", "actual_total")),
        "observations": Table(output / "observations.csv", ("scenario", "product_code", "date", "quantity",
            "unit", "data_kind", "year", "purpose", "source_id", "observation_basis")),
    }


def export_metrics(table, view, horizon, summary):
    for model, metrics in summary["metrics"].items():
        table.write({"view": view, "horizon": horizon, "time_unit": summary["time_unit"], "model": model, **metrics})


def export_observations(table, bundle, panel, history, real, units):
    for scenario, grouped in (("2025_calendar_synthetic_completeness", panel),
                               ("2025_activity_date_synthetic_exposure", history),
                               ("2026_verified_operational_observations", real)):
        for code, observations in grouped.items():
            identity = bundle["product_identity_by_code"].get(code, {})
            unit = identity.get("unit") if scenario.startswith("2025") else units.get(code)
            for row in observations:
                table.write({**row, "date": row["date"].isoformat(), "scenario": scenario,
                             "product_code": code, "unit": unit, "purpose": "experimental_evaluation"})


def configuration(experiment, external, bundle):
    return {"data_kind": "synthetic_structured", "year": 2025, "purpose": "experimental_evaluation",
            "models": list(experiment.models.MODELS), "horizons": [7, 30],
            "minimum_training_periods": experiment.MIN_TRAIN, "minimum_positive_training_periods": experiment.MIN_POSITIVE,
            "minimum_publicable_folds": experiment.MIN_FOLDS, "alpha": 0.1, "beta": 0.1,
            "aggregation_size": 7, "baseline_window": 7, "bootstrap_seed": experiment.SEED,
            "bootstrap_iterations": experiment.BOOTSTRAP_ITERATIONS,
            "development_target_start": "2025-06-01", "development_latest_target_end": "2025-06-30",
            "2025_evaluation_start": "2025-07-01", "2026_first_origin_index": experiment.MIN_TRAIN,
            "2025_calendar_policy": "assumed_synthetic_complete_calendar_from_first_positive",
            "2025_transfer_calendar_policy": "synthetic_activity_dates_exposure_scenario_not_real_operating_calendar",
            "2026_calendar_policy": "frozen_verified_operational_calendar",
            "time_units": {"2025_ONLY": "calendar_day", "2026_views": "operational_day"},
            "scaling_boundary_policy": "exclude_cross_year_training_differences",
            "source_positive_dates_2025": len(bundle["activity_dates"]),
            "source_period_2026": external["metadata"]["range"],
            "uncertainty_scope": "two_part_only;other_candidates_not_requested",
            "scenario_selection_on_2026": False, "original_protocol_changed": False}


def limitations():
    return [
        "2025 is synthetic_structured, never historical real_observed transactions.",
        "2025 absent SKU dates are assumed zero ONLY in explicitly named synthetic completeness scenarios.",
        "2025 activity dates are a synthetic exposure scenario, not evidence of business opening or closure.",
        "2026 uses the verified frozen STRICT cohort. QUARANTINE and SENSITIVITY are not mixed into it.",
        "2026 horizons are 7/30 operational periods; 2025_ONLY horizons are 7/30 calendar days.",
        "2026 sales quantities do not measure latent demand lost during stockouts.",
        "Transfer requires exact code, normalized demand name and explicit unit; unmatched SKU abstain.",
        "Quantity volume is a catalogue quantity index mixing units, not a homogeneous physical/economic total.",
        "MASE/RMSSE use only within-year training differences; no difference bridges 2025 and 2026.",
        "Baseline reference is selected on June 2025 development; no scenario is selected on 2026 targets.",
        "Observed interval coverage evaluates nominal80% intervals; it does not guarantee calibration.",
        "Interval/Brier evaluation is for two-part. Other candidates have uncertainty not_requested.",
        "Bootstrap resamples SKU clusters with all paired origins; dependence across SKUs is not proven absent.",
        "Inventory L/R/initial0/buffer0/lost_sales are experimental scenarios, not enterprise parameters.",
        "Synthetic terminal2025 stock is context and a separately labelled2026 sensitivity, never 2025 initialization.",
        "Existing real2026 frozen model results are preserved; these additional methods use a new separate protocol.",
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-2025", type=Path, required=True)
    parser.add_argument("--verified-2026", type=Path, default=ROOT / "artifacts/empirical_2026")
    parser.add_argument("--source-2026", type=Path)
    parser.add_argument("--catalog-2026", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    execute(arguments)


if __name__ == "__main__":
    main()
