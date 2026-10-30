"""Independent streaming verification of experiment artifacts; no application/DB.

Checks exported arithmetic and chronology. Training values and inventory traces
are not exported, so this verifier does not claim to reconstruct those inputs.
"""
import argparse
from collections import defaultdict
import csv
from datetime import date, timedelta
import hashlib
import json
from math import isclose, isfinite, sqrt
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
FROZEN_BUILD_SHA256 = "087bda535bc60f49f773d026434e10df5adcf5ce59fbc8669ffc861dd9ce82f8"
REQUIRED = {"audit_2025.json", "configuration.json", "profiles_2025.csv", "inventory_context_2025.csv",
            "forecasts.csv", "inventory_simulations.csv", "metrics.csv", "results.json", "observations.csv"}
VIEWS = ("2025_ONLY", "2026_REAL_ONLY", "2025_TO_2026_TRANSFER", "2025_PLUS_2026_OBSERVED_TO_DATE")
MODELS = {"zero", "naive", "mean", "moving_average", "linear_trend", "croston", "sba", "tsb", "two_part", "adida"}
INVENTORY_METRICS = ("total_demand", "fulfilled_units", "unmet_units", "fill_rate", "stockout_periods",
                     "cycle_service_level", "average_on_hand", "max_on_hand", "ending_stock", "order_count", "total_ordered_units")


class VerificationError(ValueError):
    """Only stable aggregate error codes; never include a SKU or source path."""


def require(condition, code):
    if not condition:
        raise VerificationError(code)


def finite_tree(value):
    if isinstance(value, float):
        require(isfinite(value), "nonfinite_number")
    elif isinstance(value, dict):
        for child in value.values():
            finite_tree(child)
    elif isinstance(value, list):
        for child in value:
            finite_tree(child)


def decode(value):
    def invalid_constant(_value):
        raise VerificationError("nonfinite_json_constant")
    result = json.loads(value, parse_constant=invalid_constant)
    finite_tree(result)
    return result


def read_json(path):
    return decode(path.read_text(encoding="utf-8-sig"))


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def number(value, code="invalid_numeric_value", *, nonnegative=False):
    require(isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value), code)
    require(not nonnegative or value >= 0, code)
    return value


def close(actual, expected, code):
    if actual is None or expected is None:
        require(actual is expected, code)
    else:
        number(actual)
        number(expected)
        require(isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-8), code)


def artifact_integrity(run):
    manifest = read_json(run / "artifact_sha256.json")
    require(REQUIRED.issubset(manifest), "incomplete_artifact_manifest")
    for name, expected in manifest.items():
        require(Path(name).name == name and name not in ("", ".", ".."), "unsafe_artifact_name")
        require(isinstance(expected, str) and len(expected) == 64, "invalid_artifact_hash")
        require(digest(run / name) == expected, "artifact_hash_mismatch")
    return {"verified_artifacts": len(manifest), "manifest_sha256": digest(run / "artifact_sha256.json")}


def source_integrity(report, frozen_directory, source_directory=None):
    metadata, integrity = report["metadata"], report["integrity"]
    before, after = metadata["sources_2025_sha256"], integrity["sources_2025_sha256_after"]
    require(before == after and integrity["sources_2025_unchanged"], "structured_source_recorded_integrity_failure")
    require(metadata["data_kind"] == "synthetic_structured" and metadata["year"] == 2025 and metadata["purpose"] == "experimental_evaluation", "structured_metadata_provenance_failure")
    for source in report["audit"]["sources"].values():
        require(before[source["source_id"]] == source["sha256"], "audit_source_hash_disagreement")
    require(integrity["frozen_2026_inputs_unchanged"], "frozen_source_recorded_integrity_failure")
    require(integrity["no_database"] and integrity["no_real_orders"] and report["read_only"] and not report["live_integration"], "production_isolation_declaration_failure")
    frozen = read_json(frozen_directory / "empirical_2026_build_report.json")
    require(digest(frozen_directory / "empirical_2026_build_report.json") == FROZEN_BUILD_SHA256, "frozen_build_manifest_hash_failure")
    require(frozen["READY_FOR_BACKTEST"] and frozen["source_sha256_before"] == frozen["source_sha256_after"], "frozen_build_integrity_failure")
    for name, expected in metadata["inputs_2026_sha256"].items():
        require(Path(name).name == name, "unsafe_frozen_input_name")
        require(digest(frozen_directory / name) == expected, "frozen_input_hash_failure")
        if name != "empirical_2026_build_report.json":
            require(expected == frozen["artifact_sha256"][name], "frozen_input_reference_hash_failure")
    if source_directory is not None:
        for name, expected in before.items():
            require(Path(name).name == name and digest(source_directory / name) == expected, "current_structured_source_hash_failure")
    with (frozen_directory / "empirical_2026_operational_calendar.csv").open(encoding="utf-8-sig", newline="") as handle:
        calendar = [date.fromisoformat(row["fecha_real"]) for row in csv.DictReader(handle)]
    require(calendar == sorted(set(calendar)), "frozen_calendar_order_failure")
    return {"recorded_2025_before_after_equal": True, "current_2025_sources_rehashed": source_directory is not None,
            "frozen_2026_inputs_rehashed": True, "no_database_or_orders_declared": True}, {day: index for index, day in enumerate(calendar)}


def _forecast_chronology(row, calendar):
    horizon = int(row["horizon"])
    require(row["view"] in VIEWS and horizon in (7, 30), "unknown_forecast_view_or_horizon")
    actual, predicted = decode(row["actual"]), decode(row["predicted"])
    dates = [date.fromisoformat(day) for day in decode(row["target_dates"])]
    require(len(actual) == len(predicted) == len(dates) == horizon, "forecast_pair_length_failure")
    require(dates == sorted(set(dates)), "target_date_order_failure")
    require(date.fromisoformat(row["origin"]) == dates[0], "origin_target_mismatch")
    require(date.fromisoformat(row["training_end"]) < dates[0], "training_target_leakage")
    synthetic = row["view"] == "2025_ONLY"
    year, kind = (2025, "synthetic_structured") if synthetic else (2026, "real_observed")
    require(all(day.year == year for day in dates), "target_year_failure")
    require(decode(row["target_sources"]) == {kind: horizon}, "target_provenance_failure")
    require(row["time_unit"] == ("calendar_day" if synthetic else "operational_day"), "forecast_cadence_failure")
    if synthetic:
        require(all(right - left == timedelta(days=1) for left, right in zip(dates, dates[1:])), "calendar_target_gap")
    else:
        require(all(day in calendar for day in dates), "target_outside_frozen_calendar")
        require(all(calendar[right] == calendar[left] + 1 for left, right in zip(dates, dates[1:])), "operational_target_gap")
    training = decode(row["training_sources"])
    allowed = ({"synthetic_structured"} if row["view"] in ("2025_ONLY", "2025_TO_2026_TRANSFER") else
               {"real_observed"} if row["view"] == "2026_REAL_ONLY" else {"synthetic_structured", "real_observed"})
    require(set(training).issubset(allowed) and sum(training.values()) == int(row["training_count"]), "training_provenance_failure")
    require(training and all(isinstance(count, int) and not isinstance(count, bool) and count > 0 for count in training.values()), "training_source_count_failure")
    expected_training_year = 2026 if training.get("real_observed", 0) > 0 else 2025
    require(date.fromisoformat(row["training_end"]).year == expected_training_year, "training_end_year_failure")
    for value in actual + predicted:
        number(value, nonnegative=True)
    return actual, predicted, dates


def _fold_metrics(row, actual, predicted):
    metrics = decode(row["metrics"])
    errors = [p - a for a, p in zip(actual, predicted)]
    n, total, absolute, squared, signed = len(actual), sum(actual), sum(map(abs, errors)), sum(e * e for e in errors), sum(errors)
    calculated = {"n": n, "actual_total": total, "absolute_error_total": absolute, "squared_error_total": squared,
                  "signed_error_total": signed, "mae": absolute / n, "rmse": sqrt(squared / n), "bias": signed / n,
                  "wape": absolute / total * 100 if total else None}
    for key, value in calculated.items():
        close(metrics[key], value, "fold_" + key + "_failure")
    require(metrics["scale_source"] == "training_only", "scale_source_failure")
    require(0 <= metrics["scale_pair_count"] <= int(row["training_count"]) - metrics["scale_m"], "scale_training_pair_count_failure")
    for key, numerator in (("mase", absolute / n), ("rmsse", squared / n)):
        denominator = metrics[key + "_denominator"]
        if metrics[key] is None:
            require(denominator is None or denominator == 0, "scaled_unavailable_denominator_failure")
            require(bool(metrics["unavailable_reasons"][key]), "scaled_missing_unavailable_reason")
        else:
            number(denominator, nonnegative=True)
            require(denominator > 0 and metrics["scale_pair_count"] > 0, "scaled_nonpositive_training_denominator")
            expected = numerator / denominator
            close(metrics[key], sqrt(expected) if key == "rmsse" else expected, "fold_" + key + "_failure")
    if row["model"] == "zero":
        require(all(p == 0 for p in predicted), "zero_benchmark_forecast_failure")
        if total:
            close(metrics["wape"], 100.0, "zero_benchmark_wape_failure")
    for field in ("two_part", "uncertainty", "prediction_policy", "profile"):
        if row[field]:
            decode(row[field])
    return metrics, calculated


def empty_aggregate():
    return {"fold_count": 0, "n": 0, "actual_total": 0.0, "absolute_error_total": 0.0, "squared_error_total": 0.0,
            "signed_error_total": 0.0, "mase_weighted": 0.0, "rmsse_squared_weighted": 0.0,
            "mase_n": 0, "rmsse_n": 0, "last_target": None}


def accumulate(target, values):
    for key in ("fold_count", "n", "actual_total", "absolute_error_total", "squared_error_total", "signed_error_total",
                "mase_weighted", "rmsse_squared_weighted", "mase_n", "rmsse_n"):
        target[key] += values[key]


def finalize(values, sku_count):
    n, total = values["n"], values["actual_total"]
    return {**{key: values[key] for key in ("fold_count", "n", "actual_total", "absolute_error_total", "squared_error_total", "signed_error_total")},
            "sku_count": sku_count, "wape": values["absolute_error_total"] / total * 100 if total else None,
            "mae": values["absolute_error_total"] / n if n else None,
            "rmse": sqrt(values["squared_error_total"] / n) if n else None,
            "bias": values["signed_error_total"] / n if n else None,
            "mase": values["mase_weighted"] / values["mase_n"] if values["mase_n"] else None,
            "rmsse": sqrt(values["rmsse_squared_weighted"] / values["rmsse_n"]) if values["rmsse_n"] else None}


def _forecast_summaries(groups):
    summaries = defaultdict(lambda: {"all": empty_aggregate(), "public": empty_aggregate(), "all_skus": 0, "public_skus": 0})
    for (view, horizon, model, _code), values in groups.items():
        summary = summaries[(view, horizon, model)]
        accumulate(summary["all"], values)
        summary["all_skus"] += 1
        if values["fold_count"] >= 3:
            accumulate(summary["public"], values)
            summary["public_skus"] += 1
    return summaries


def _compare_forecast_summary(summary, recorded):
    for scope, expected in (("public", recorded), ("all", recorded["all_successful_origin_metrics"])):
        for key, value in finalize(summary[scope], summary[scope + "_skus"]).items():
            close(expected[key], value, scope + "_summary_" + key + "_failure")


def _compare_forecast_groups(summaries, report):
    comparisons = 0
    for view, definition in report["views"].items():
        for horizon, evaluation in definition["horizons"].items():
            for model, recorded in evaluation["metrics"].items():
                _compare_forecast_summary(summaries[(view, horizon, model)], recorded)
                comparisons += 1
    return comparisons


def verify_forecasts(path, report, configuration, calendar):
    groups = defaultdict(empty_aggregate)
    row_count = 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            require(row["model"] in configuration["models"], "unconfigured_forecast_model")
            require(row["view"] in report["views"] and row["horizon"] in report["views"][row["view"]]["horizons"] and row["model"] in report["views"][row["view"]]["horizons"][row["horizon"]]["metrics"], "unreported_forecast_group")
            actual, predicted, dates = _forecast_chronology(row, calendar)
            metrics, calculated = _fold_metrics(row, actual, predicted)
            key = row["view"], row["horizon"], row["model"], row["product_code"]
            target = groups[key]
            require(target["last_target"] is None or target["last_target"] < dates[0], "duplicate_or_overlapping_forecast_pair")
            target["last_target"] = dates[-1]
            values = {**empty_aggregate(), **calculated, "fold_count": 1}
            for metric in ("mase", "rmsse"):
                if metrics[metric] is not None:
                    values[metric + "_n"] = len(actual)
                    values["mase_weighted" if metric == "mase" else "rmsse_squared_weighted"] = (metrics[metric] if metric == "mase" else metrics[metric] ** 2) * len(actual)
            accumulate(target, values)
            row_count += 1
    require(configuration["minimum_publicable_folds"] == 3, "unexpected_publicability_rule")
    comparisons = _compare_forecast_groups(_forecast_summaries(groups), report)
    return {"forecast_rows": row_count, "sku_model_view_groups": len(groups), "summary_groups_verified": comparisons,
            "publicability": "minimum_three_successful_folds_per_sku", "all_successful_and_public_metrics_recomputed": True}


def verify_metric_table(path, report):
    fields = ("sku_count", "fold_count", "n", "wape", "mae", "rmse", "bias", "mase", "rmsse", "actual_total")
    expected = {(view, horizon, model): metrics for view, item in report["views"].items()
                for horizon, evaluation in item["horizons"].items() for model, metrics in evaluation["metrics"].items()}
    seen = set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            key = row["view"], row["horizon"], row["model"]
            require(key in expected and key not in seen, "metrics_table_identity_failure")
            seen.add(key)
            for field in fields:
                value = None if row[field] in ("", "null") else float(row[field])
                close(value, expected[key][field], "metrics_table_" + field + "_failure")
    require(seen == set(expected), "metrics_table_incomplete")
    return {"rows": len(seen), "matches_recomputed_public_summaries": True}


def _inventory_row(row):
    metrics, scenario, accounting = (decode(row[key]) for key in ("metrics", "scenario", "accounting"))
    require(row["view"] in VIEWS and int(row["horizon"]) in (7, 30), "invalid_inventory_view_or_horizon")
    require(scenario["stockout_mode"] == "lost_sales" and scenario["policy"] == "forecast_order_up_to", "unexpected_inventory_scenario")
    require(scenario["lead_time_periods"] + scenario["review_period"] == int(row["horizon"]), "inventory_horizon_mismatch")
    require(scenario["policy_parameters"] == {"target_stock": 0.0}, "inventory_buffer_not_declared")
    require(scenario["periods"] > 0 and scenario["review_period"] > 0, "invalid_inventory_periods")
    for value in metrics.values():
        if value is not None:
            number(value, nonnegative=True)
    initial = number(scenario["initial_stock"], nonnegative=True)
    kind = "scenario_constant" if row["scenario_id"] == "zero_initial_stock" else "synthetic_structured"
    require(row["initial_stock_data_kind"] == kind, "inventory_initial_stock_provenance_failure")
    if row["scenario_id"] == "zero_initial_stock":
        require(initial == 0, "inventory_zero_initial_stock_failure")
    else:
        require(row["scenario_id"] == "synthetic_2025_snapshot_initial_stock" and row["view"] != "2025_ONLY", "inventory_snapshot_time_failure")
    year = 2025 if row["view"] == "2025_ONLY" else 2026
    start, end = date.fromisoformat(row["start"]), date.fromisoformat(row["end"])
    require(start <= end and start.year == end.year == year, "inventory_target_year_failure")
    balances = {"stock_balance_error": initial + metrics["received_units"] - metrics["fulfilled_units"] - metrics["ending_stock"],
                "demand_balance_error": metrics["total_demand"] - metrics["fulfilled_units"] - metrics["lost_units"] - metrics["ending_backorder"],
                "pipeline_balance_error": metrics["total_ordered_units"] - metrics["received_units"] - metrics["ending_pipeline_units"]}
    for key, value in balances.items():
        require(abs(value) <= 1e-7 and abs(accounting[key]) <= 1e-7, "inventory_accounting_balance_failure")
        close(accounting[key], value, "inventory_recorded_accounting_failure")
    close(metrics["unmet_units"], metrics["lost_units"], "inventory_unmet_units_failure")
    close(metrics["fill_rate"], metrics["immediately_fulfilled_units"] / metrics["total_demand"] if metrics["total_demand"] else None, "inventory_fill_rate_failure")
    cycles = scenario["periods"] // scenario["review_period"]
    close(metrics["complete_review_cycles"], cycles, "inventory_review_cycles_failure")
    close(metrics["cycle_service_level"], metrics["successful_review_cycles"] / cycles if cycles else None, "inventory_cycle_service_failure")
    require(0 <= metrics["stockout_periods"] <= scenario["periods"], "inventory_stockout_count_failure")
    require(metrics["average_on_hand"] <= metrics["max_on_hand"] and metrics["ending_stock"] <= metrics["max_on_hand"], "inventory_stock_summary_failure")
    return metrics


def verify_inventory(path, report):
    groups = defaultdict(lambda: {"rows": 0, "values": defaultdict(float), "counts": defaultdict(int), "codes": set()})
    expected = {(view, horizon, key) for view, definition in report["views"].items()
                for horizon, evaluation in definition["horizons"].items()
                for key in evaluation["inventory"]["results"]}
    count = 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            metrics = _inventory_row(row)
            key = row["view"], row["horizon"], row["scenario_id"] + ":" + row["model"]
            require(key in expected, "unreported_inventory_group")
            group = groups[key]
            require(row["product_code"] not in group["codes"], "duplicate_inventory_identity")
            group["codes"].add(row["product_code"])
            group["rows"] += 1
            for metric in INVENTORY_METRICS:
                if metrics[metric] is not None:
                    group["values"][metric] += metrics[metric]
                    group["counts"][metric] += 1
            count += 1
    comparisons = 0
    for view, definition in report["views"].items():
        for horizon, evaluation in definition["horizons"].items():
            for key, recorded in evaluation["inventory"]["results"].items():
                group = groups[(view, horizon, key)]
                close(recorded["sku_count"], group["rows"], "inventory_summary_sku_count_failure")
                for metric in INVENTORY_METRICS:
                    expected = group["values"][metric] / group["counts"][metric] if group["counts"][metric] else None
                    close(recorded["macro_metrics"][metric], expected, "inventory_summary_" + metric + "_failure")
                total, fulfilled = group["values"]["total_demand"], group["values"]["fulfilled_units"]
                close(recorded["quantity_index_total_demand"], total, "inventory_summary_total_failure")
                close(recorded["quantity_index_fulfilled_units"], fulfilled, "inventory_summary_fulfilled_failure")
                close(recorded["quantity_index_fill_rate"], fulfilled / total if total else None, "inventory_summary_fill_rate_failure")
                comparisons += 1
    return {"inventory_rows": count, "summary_groups_verified": comparisons, "exported_accounting_and_fill_metrics_recomputed": True,
            "trace_reconstruction": "unavailable_trace_not_exported"}


def verify_structure(report, configuration):
    """Require the complete fixed final-run matrix, even for unavailable groups."""
    require(report.get("configuration") == configuration, "configuration_export_disagreement")
    require(set(configuration.get("models", [])) == MODELS and len(configuration["models"]) == len(MODELS),
            "incomplete_configured_models")
    require(configuration.get("horizons") == [7, 30], "unexpected_configured_horizons")
    require(set(report["views"]) == set(VIEWS), "incomplete_view_matrix")
    horizons = {str(value) for value in configuration["horizons"]}
    for definition in report["views"].values():
        require(set(definition["horizons"]) == horizons, "incomplete_horizon_matrix")
        for evaluation in definition["horizons"].values():
            require(set(evaluation["metrics"]) == set(configuration["models"]), "incomplete_model_matrix")


def verify_run(run, *, frozen_directory, source_directory=None):
    hashes = artifact_integrity(run)
    report, configuration = read_json(run / "results.json"), read_json(run / "configuration.json")
    verify_structure(report, configuration)
    require(report["audit"] == read_json(run / "audit_2025.json"), "audit_export_disagreement")
    require(configuration["data_kind"] == "synthetic_structured" and configuration["year"] == 2025 and not configuration["scenario_selection_on_2026"] and not configuration["original_protocol_changed"], "configuration_provenance_or_selection_failure")
    sources, calendar = source_integrity(report, frozen_directory, source_directory)
    forecasts = verify_forecasts(run / "forecasts.csv", report, configuration, calendar)
    metrics_table = verify_metric_table(run / "metrics.csv", report)
    inventory = verify_inventory(run / "inventory_simulations.csv", report)
    return {"status": "VERIFIED", "data_kind_2025": "synthetic_structured", "data_kind_2026": "real_observed",
            "purpose": "experimental_evaluation", "artifact_integrity": hashes, "source_integrity": sources,
            "forecasts": forecasts, "metrics_table": metrics_table, "inventory": inventory, "no_database_or_application_imports": True,
            "limitations": ["Training quantities are not exported: denominator arithmetic and training-only declarations are checked; independent recomputation of training scales is unavailable.",
                            "Inventory traces are not exported: accounting/ratios/exported summaries are checked; period-by-period reconstruction is unavailable.",
                            "Forecast actual values are not independently joined to frozen observations; target chronology and provenance declarations are checked.",
                            "Calibration, intervals, bootstrap, segments, abstention and temporal comparisons are hash-bound, not independently recomputed by this verifier.",
                            "Hashes prove identity against the recorded/frozen manifest, not real-world source completeness or historical authenticity."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verified-2026", type=Path, default=ROOT / "artifacts/empirical_2026")
    parser.add_argument("--source-2025", type=Path)
    args = parser.parse_args()
    require(not args.output.resolve().is_relative_to(args.run.resolve()), "verification_output_must_be_separate")
    result = verify_run(args.run, frozen_directory=args.verified_2026, source_directory=args.source_2025)
    with args.output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"status": result["status"], "forecast_rows": result["forecasts"]["forecast_rows"],
                      "inventory_rows": result["inventory"]["inventory_rows"], "verification_sha256": digest(args.output)}, allow_nan=False))


if __name__ == "__main__":
    try:
        main()
    except (VerificationError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "FAILED", "reason": str(error) if isinstance(error, VerificationError) else "invalid_or_unavailable_artifact"}))
        sys.exit(1)
