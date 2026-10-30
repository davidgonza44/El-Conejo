"""Read completed experimental artifacts and add sanitized coverage by unit.

This postprocessing does not fit, select, simulate, or modify the completed run.
Its separate output is regenerable technical material, never academic prose.
"""
import argparse
from collections import defaultdict
import csv
from datetime import date
import hashlib
import json
import math
from pathlib import Path

from experimental_2025_runtime import ROOT, activate_guard

SYNTHETIC_SCENARIO = "2025_calendar_synthetic_completeness"
REAL_SCENARIO = "2026_verified_operational_observations"
UNVERIFIED_UNIT = "UNVERIFIED_UNIT"
REQUIRED_FILES = ("results.json", "configuration.json", "forecasts.csv",
                  "observations.csv", "inventory_context_2025.csv", "metrics.csv",
                  "inventory_simulations.csv", "profiles_2025.csv")


def rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _quantity(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("Observed quantity must be finite and nonnegative")
    return number


def forecast_support(forecasts):
    """Count distinct successful origins with bitsets; no prediction arrays kept."""
    masks, grids, origin_indices = {}, defaultdict(dict), {}
    for row in forecasts:
        view, horizon = row["view"], int(row["horizon"])
        key, origin = (view, horizon), row["origin"]
        existing = grids[key].get(origin)
        if existing is None:
            targets = tuple(json.loads(row["target_dates"]))
            if len(targets) != horizon or len(set(targets)) != horizon or targets[0] != origin:
                raise ValueError("Forecast target identity is invalid")
            if any(date.fromisoformat(day).year != (2025 if view == "2025_ONLY" else 2026) for day in targets):
                raise ValueError("Forecast target year differs from its view")
            grids[key][origin] = (row["target_dates"], targets)
            origin_indices[key, origin] = len(grids[key]) - 1
        elif existing[0] != row["target_dates"]:
            raise ValueError("Models disagree about the target dates of an origin")
        pair = (view, horizon, row["model"], row["product_code"])
        masks[pair] = masks.get(pair, 0) | (1 << origin_indices[key, origin])
    target_dates = {}
    for key, origins in grids.items():
        owned = {}
        for origin, (_, targets) in origins.items():
            for day in targets:
                if day in owned and owned[day] != origin:
                    raise ValueError("Distinct evaluation origins have overlapping targets")
                owned[day] = origin
        target_dates[key] = set(owned)
    return masks, target_dates


def _set_unit(units, code, unit):
    value = unit.strip() if unit else UNVERIFIED_UNIT
    previous = units.setdefault(code, value)
    if previous != value:
        raise ValueError("A SKU changes unit within the same observation partition")


def partition_quantities(observations, target_dates):
    """Each SKU/date contributes once, independently of the number of models."""
    units = {2025: {}, 2026: {}}
    volumes = {key: defaultdict(float) for key in target_dates}
    seen, duplicates = {}, 0
    dates_by_scenario = defaultdict(dict)
    for key, dates in target_dates.items():
        scenario = SYNTHETIC_SCENARIO if key[0] == "2025_ONLY" else REAL_SCENARIO
        for day in dates:
            dates_by_scenario[scenario].setdefault(day, []).append(key)
    for row in observations:
        scenario = row["scenario"]
        if scenario not in (SYNTHETIC_SCENARIO, REAL_SCENARIO):
            continue
        year = 2025 if scenario == SYNTHETIC_SCENARIO else 2026
        kind = "synthetic_structured" if year == 2025 else "real_observed"
        if row["data_kind"] != kind or int(row["year"]) != year or date.fromisoformat(row["date"]).year != year:
            raise ValueError("Observation partition provenance is inconsistent")
        code, day = row["product_code"], row["date"]
        _set_unit(units[year], code, row.get("unit"))
        quantity = _quantity(row["quantity"])
        partitions = dates_by_scenario[scenario].get(day, [])
        if not partitions:
            continue
        identity, value = (year, code, day), (units[year][code], quantity)
        if identity in seen:
            if seen[identity] != value:
                raise ValueError("Duplicate observation has conflicting quantity or unit")
            duplicates += 1
            continue
        seen[identity] = value
        for key in partitions:
            volumes[key][code] += quantity
    return units, volumes, duplicates


def _ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "percent": numerator / denominator * 100 if denominator else None,
            "unavailable_reason": None if denominator else "zero_denominator"}


def unit_coverage(universe, units, volumes, evaluable):
    grouped = defaultdict(list)
    if not set(evaluable).issubset(universe):
        raise ValueError("Forecast support includes SKU outside the declared universe")
    for code in universe:
        grouped[units.get(code, UNVERIFIED_UNIT)].append(code)
    result = {}
    for unit, codes in sorted(grouped.items()):
        selected = [code for code in codes if code in evaluable]
        result[unit] = {"sku": _ratio(len(selected), len(codes)),
                        "quantity": _ratio(math.fsum(volumes.get(code, 0.0) for code in selected),
                                           math.fsum(volumes.get(code, 0.0) for code in codes)),
                        "quantity_unit_verified": unit != UNVERIFIED_UNIT,
                        "quantity_interpretation": "recorded_units_within_exact_unit_label;no_unit_conversion",
                        "unit_unavailable_reason": "unit_not_verified" if unit == UNVERIFIED_UNIT else None}
    return result


def coverage_by_unit(forecasts, observations, contexts, report):
    """Pure iterable API used by tests; returns no SKU identities."""
    masks, target_dates = forecast_support(forecasts)
    units, volumes, duplicates = partition_quantities(observations, target_dates)
    context_units = {}
    for row in contexts:
        _set_unit(context_units, row["product_code"], row.get("unit"))
    universe_2025, universe_2026 = set(context_units), set(units[2026])
    # Demand units govern demand coverage even where stock has a different unit.
    source_units_2025 = {**context_units, **units[2025]}
    minimum = report["configuration"]["minimum_publicable_folds"]
    output = {}
    for view, view_report in sorted(report["views"].items()):
        output[view] = {}
        for horizon_text, summary in sorted(view_report["horizons"].items()):
            horizon, key = int(horizon_text), (view, int(horizon_text))
            materialized = target_dates.get(key, set())
            expected = summary["target_date_count"]
            if len(materialized) != expected:
                output[view][horizon_text] = {"available": False,
                    "unavailable_reason": "forecast_target_grid_not_fully_materialized",
                    "materialized_target_date_count": len(materialized), "expected_target_date_count": expected}
                continue
            universe = universe_2025 if view == "2025_ONLY" else universe_2026
            selected_units = source_units_2025 if view == "2025_ONLY" else units[2026]
            grouped = {}
            for model, core in sorted(summary["coverage"].items()):
                if len(universe) != core["sku"]["denominator"]:
                    raise ValueError("Consolidation universe differs from completed evaluation")
                evaluable = {code for (local_view, local_horizon, local_model, code), mask in masks.items()
                             if (local_view, local_horizon, local_model) == (view, horizon, model)
                             and mask.bit_count() >= minimum}
                detail = unit_coverage(universe, selected_units, volumes.get(key, {}), evaluable)
                _check_core_coverage(detail, core)
                grouped[model] = detail
            output[view][horizon_text] = {"available": True, "minimum_publicable_folds": minimum,
                "target_date_count": len(materialized),
                "target_range": [min(materialized), max(materialized)] if materialized else None,
                "volume_period": "union_of_unique_nonoverlapping_target_dates",
                "models": grouped}
    return {"coverage_by_unit": output, "diagnostics": {"duplicate_selected_observation_rows_ignored": duplicates,
            "universe_2025_sku": len(universe_2025), "universe_2026_sku": len(universe_2026),
            "snapshot_only_2025_sku": len(universe_2025 - set(units[2025])),
            "demand_snapshot_unit_mismatch_sku": sum(context_units.get(code) != unit
                                                     for code, unit in units[2025].items())}}


def _check_core_coverage(detail, core):
    for metric, source in (("sku", "sku"), ("quantity", "volume")):
        for field in ("numerator", "denominator"):
            summed = math.fsum(value[metric][field] for value in detail.values())
            expected = core[source][field]
            if not math.isclose(summed, expected, rel_tol=1e-10, abs_tol=1e-8):
                raise ValueError("Unit coverage fails to reconcile with completed aggregate coverage")


def artifact_provenance(hashes):
    """SHA-bound companion metadata for tables whose individual rows use views."""
    purpose = "experimental_evaluation"
    synthetic = {"source_id": "Demanda_limpia_2025_CORREGIDA_FINAL.csv", "year": 2025,
                 "data_kind": "synthetic_structured", "purpose": purpose}
    real = {"source_id": "empirical_2026_series_strict.csv", "year": 2026,
            "data_kind": "real_observed", "purpose": purpose}
    view_roles = {
        "2025_ONLY": {"training_components": [synthetic], "targets": synthetic,
                      "time_unit": "calendar_day", "training_cutoff": "strictly_before_origin"},
        "2026_REAL_ONLY": {"training_components": [real], "targets": real,
                           "time_unit": "operational_day", "training_cutoff": "strictly_before_origin"},
        "2025_TO_2026_TRANSFER": {"training_components": [synthetic], "targets": real,
                                 "time_unit": "operational_day", "training_cutoff": "synthetic_2025_only",
                                 "synthetic_cadence": "activity_date_exposure_scenario_not_real_opening_days"},
        "2025_PLUS_2026_OBSERVED_TO_DATE": {"training_components": [synthetic, real], "targets": real,
                                          "time_unit": "operational_day", "training_cutoff": "strictly_before_origin",
                                          "synthetic_cadence": "activity_date_exposure_scenario_not_real_opening_days"}}
    result = {}
    for name, digest in sorted(hashes.items()):
        metadata = {"sha256": digest, "purpose": purpose, "companion_metadata": True,
                    "source_paths_excluded": True}
        if name in ("forecasts.csv", "metrics.csv", "inventory_simulations.csv", "results.json", "configuration.json"):
            metadata["view_roles"] = view_roles
        if name == "observations.csv":
            metadata["row_metadata_columns"] = ["scenario", "source_id", "year", "data_kind", "purpose", "observation_basis"]
            metadata["scenario_roles"] = {SYNTHETIC_SCENARIO: synthetic,
                "2025_activity_date_synthetic_exposure": synthetic, REAL_SCENARIO: real}
        if name == "profiles_2025.csv":
            metadata.update(synthetic)
        if name == "inventory_context_2025.csv":
            metadata.update(source_id="Existencias_2025_CORREGIDAS_FINAL.csv", year=2025,
                            data_kind="synthetic_structured", role="snapshot_context_not_demand")
        if name == "inventory_simulations.csv":
            metadata["initial_stock_roles"] = {
                "zero_initial_stock": {"source_id": "declared_experimental_zero_constant", "year": None,
                    "data_kind": "experimental_scenario_constant", "purpose": purpose},
                "synthetic_2025_snapshot_initial_stock": {"source_id": "Existencias_2025_CORREGIDAS_FINAL.csv",
                    "year": 2025, "data_kind": "synthetic_structured", "purpose": purpose,
                    "snapshot_date": "2025-12-31", "snapshot_date_basis": "experimental_assumption",
                    "scope": "separate_2026_sensitivity_only"}}
        result[name] = metadata
    return result


def consolidate(run_directory):
    activate_guard()
    directory = Path(run_directory).resolve()
    allowed = (ROOT / "reports/generated/experimental_2025").resolve()
    if not directory.is_relative_to(allowed) or directory == allowed:
        raise ValueError("Only a dedicated completed experimental run is accepted")
    index = json.loads((directory / "artifact_sha256.json").read_text(encoding="utf-8"))
    verified, states = {}, {}
    for name in REQUIRED_FILES:
        path = directory / name
        verified[name] = sha256(path)
        if verified[name] != index.get(name):
            raise ValueError("Completed artifact does not match its frozen run manifest")
        states[name] = (path.stat().st_size, path.stat().st_mtime_ns)
    report = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    integrity = report.get("integrity", {})
    if not all(integrity.get(field) for field in ("sources_2025_unchanged", "frozen_2026_inputs_unchanged",
                                                "code_unchanged", "no_database", "no_real_orders")):
        raise ValueError("Run integrity or no-DB completion evidence is unavailable")
    summary = coverage_by_unit(rows(directory / "forecasts.csv"), rows(directory / "observations.csv"),
                               rows(directory / "inventory_context_2025.csv"), report)
    if any(states[name] != ((directory / name).stat().st_size, (directory / name).stat().st_mtime_ns)
           for name in REQUIRED_FILES):
        raise ValueError("A completed input changed during consolidation")
    return {"metadata": {"data_kind": "synthetic_structured", "year": 2025,
                          "purpose": "experimental_evaluation", "evaluation_label": "EVALUACIÓN EXPERIMENTAL 2025",
                          "target_2026_data_kind": "real_observed", "postprocessing_only": True,
                          "source_artifacts_sha256": verified, "no_database": True}, **summary,
            "artifact_provenance": artifact_provenance(verified),
            "snapshots_2026": {"available": False, "unavailable_reason": "Existencias_2026_not_supplied_to_this_work_unit"},
            "limitations": ["Exact unit labels remain separate; no physical or monetary conversion is inferred.",
                "Synthetic missing SKU dates are scenario zeros, never demonstrated real opening or closure.",
                "Independence of synthetic 2025 generation from real 2026 is unverified; transfer is experimental.",
                "Published coverage uses >=minimum_publicable_folds; bootstrap uses all SUCCESS common pairs.",
                "Brier and intervals concern two-part only; nominal 80% is not proven calibrated coverage."],
            "original_run_modified": False, "sanitized": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = consolidate(arguments.run)
    target = (arguments.output or arguments.run.with_name(arguments.run.name + "_consolidation") /
              "final_consolidation.json").resolve()
    allowed = (ROOT / "reports/generated/experimental_2025").resolve()
    if not target.is_relative_to(allowed) or target.is_relative_to(arguments.run.resolve()):
        raise ValueError("Consolidation must be separate from the sealed run and inside experimental outputs")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"status": "COMPLETE", "postprocessing_only": True,
                      "coverage_views": len(result["coverage_by_unit"]), "output_file": target.name}))


if __name__ == "__main__":
    main()
