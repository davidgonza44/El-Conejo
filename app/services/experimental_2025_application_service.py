"""Read-only, aggregate-only presentation of an explicitly configured offline report."""
from __future__ import annotations

from datetime import date
import json
import math
from pathlib import Path
import re


REPORT_ROOT = Path(__file__).resolve().parents[2] / "reports/generated/experimental_2025"
VIEW_KINDS = {
    "2025_ONLY": {"training": "synthetic_structured", "evaluation": "synthetic_structured"},
    "2026_REAL_ONLY": {"training": "real_observed", "evaluation": "real_observed"},
    "2025_TO_2026_TRANSFER": {"training": "synthetic_structured", "evaluation": "real_observed"},
    "2025_PLUS_2026_OBSERVED_TO_DATE": {
        "training": ["synthetic_structured", "real_observed"], "evaluation": "real_observed",
    },
}
METRICS = ("wape", "mae", "rmse", "bias", "mase", "rmsse")
_MODELS = frozenset({"zero", "zero_forecast", "naive", "mean", "moving_average", "linear_trend",
                     "arima", "random_forest", "croston", "sba", "tsb", "two_part", "adida"})
_SEGMENTS = frozenset({"regular", "intermittent", "extremely_intermittent", "smooth", "erratic", "lumpy", "unavailable"})
_REASONS = frozenset({
    "no_source_demand", "insufficient_training_periods", "insufficient_positive_training_periods",
    "cross_year_identity_or_unit_unverified", "incomplete_real_targets", "no_verified_common_transfer_pairs",
    "zero_denominator", "no_evaluable_origin", "no_evaluation_pairs", "zero_target_demand_for_wape",
    "insufficient_segment_skus", "two_part_unavailable", "target_unavailable", "insufficient_bin_count",
    "insufficient_calibration_rows", "interval_unavailable", "unequal_or_empty_evaluation_pairs",
    "target_values_differ", "insufficient_sku_clusters", "no_common_development_pairs", "no_simulation_targets",
    "snapshot_not_before_simulation", "invalid_history", "insufficient_training", "unobserved_calendar_gap",
    "numeric_or_date_range", "numeric_range", "not_requested", "insufficient_positive_events",
    "insufficient_training_scale", "zero_training_scale", "zero_target_demand", "invalid_aggregation_size",
    "insufficient_history", "extreme_intermittency", "insufficient_horizon_blocks", "insufficient_positive_blocks",
    "insufficient_independent_calibration", "insufficient_chronological_calibration", "inactive_product",
    "unsupported_observation_unit_or_horizon", "incomplete_calendar", "invalid_configuration",
    "invalid_dates", "invalid_observation", "non_finite_observation", "future_history", "unordered_history",
    "unobserved_gap", "unobserved_trailing_period", "service_excluded", "no_2025_code",
    "cross_year_name_mismatch", "cross_year_unit_unverified", "cross_year_unit_mismatch",
    "2026_source_directory_not_supplied",
    "no_identical_simulation_pairs", "snapshot_identity_unverified",
})
_SCENARIOS = frozenset({"zero_initial_stock", "synthetic_2025_snapshot_initial_stock", "experimental_scenario"})
_REASON_KEYS = frozenset({"reason", "reasons", "unavailable_reason", "unavailable_reasons"})
_LIST_KEYS = frozenset({"bins", "confidence_interval", "nominal_coverages", "date_range", "range",
                        "reasons", "reason", "sources", "training_data_kind", "evaluation_data_kind",
                        "models", "horizons", "source_period_2026"})
_KEYS = frozenset({
    "metadata", "data_kind", "year", "purpose", "experimental", "evaluation_label", "view", "audit",
    "source", "source_id", "file_name", "filename", "source_basename", "training_data_kind",
    "evaluation_data_kind", "date_range", "start", "end", "date", "min", "max", "period_start", "period_end",
    "available", "status", "reason", "reasons", "code", "unavailable_reason", "unavailable_count",
    "universe", "universe_count", "evaluable", "evaluable_count", "not_evaluable", "not_evaluable_count",
    "count", "n", "N", "rows", "observations", "observations_available", "observation_count",
    "characterization", "profile_summary", "profiles", "positive_count", "zero_rate", "adi", "cv2",
    "zero_rate_segment", "adi_cv2_classification", "segment_distribution", "distribution", "segments",
    "by_zero_rate_segment", "by_adi_cv2", "zero_rate_segments", "total_skus", "eligible_skus", "ineligible_skus",
    "coverage", "sku", "volume", "sku_coverage", "volume_coverage", "numerator", "denominator",
    "numerator_sku", "denominator_sku", "numerator_volume", "denominator_volume", "percentage", "rate",
    "horizon", "horizon_days", "horizons", "model", "models", "metrics", "pooled", "by_segment",
    "wape", "mae", "rmse", "bias", "mase", "rmsse", "value", "training_scale", "scaled_metrics",
    "legacy_metrics", "scale_source", "pair_count", "fold_count", "sku_count", "target_count",
    "actual_total", "predicted_total", "evaluated_demand", "actual_demand", "volume_total",
    "two_part", "occurrence_probability", "positive_demand_expectation", "unconditional_expected_quantity",
    "calibration", "brier_score", "bins", "lower", "upper", "mean_probability", "observed_frequency",
    "uncertainty", "nominal_coverage", "observed_coverage", "calibration_count", "average_interval_width",
    "interval_count", "interval_available_count", "lower_bound", "upper_bound", "abstention", "limited",
    "abstain", "available_count", "limited_count", "abstain_count", "sku_percent", "volume_percent",
    "inventory", "simulation", "simulations", "scenario", "scenario_id", "parameters", "results",
    "initial_stock", "lead_time_periods", "review_period", "target_stock", "stockout_mode", "policy",
    "total_demand", "fulfilled_units", "unmet_units", "fill_rate", "stockout_periods", "cycle_service_level",
    "average_on_hand", "max_on_hand", "ending_stock", "order_count", "total_ordered_units",
    "bootstrap", "comparison", "candidate", "baseline", "unit", "seed", "iterations", "confidence_level",
    "mean_difference", "median_difference", "confidence_interval", "ci_lower", "ci_upper",
    "aggregation_size", "selection_performed", "comparison_performed", "calibration_performed",
    "protocol", "protocol_id", "time_unit", "scientific_status", "sources", "training", "evaluation",
    "temporal_comparison", "absolute_error", "squared_error", "mean_error", "zero_benchmark",
    "nonevaluable", "adi_cv2_categories", "percent", "evaluable_sku", "nonevaluable_sku",
    "unavailable_reasons", "occurrence", "intervals", "mean_predicted_probability", "observed_positive_rate",
    "bins_available", "minimum_count", "minimum_bin_count", "bins_unavailable_reason", "evaluation_count",
    "covered_count", "nominal_coverages", "all_successful_origin_metrics", "scaled_counts",
    "available_pair_count", "unavailable_pair_count", "absolute_error_total", "squared_error_total",
    "signed_error_total", "summary", "minimum_skus", "minimum_folds", "success_fold_count", "origin_count",
    "target_date_count", "calendar_horizon_claim", "macro_metrics", "quantity_index_total_demand",
    "quantity_index_fulfilled_units", "quantity_index_fill_rate", "parameters_are_enterprise_estimates",
    "reference_model", "explicit_common_support", "candidate_fold_count", "reference_fold_count",
    "paired_fold_count", "discarded_candidate_folds", "discarded_reference_folds", "N_sku", "method", "metric",
    "difference_sign", "selection_on_evaluation_targets", "same_pairs", "real_only_total_folds",
    "expanding_total_folds", "real_only", "expanding", "difference_expanding_minus_real", "scenario_selected",
    "baseline_selection", "scores", "selection_role", "read_only", "live_integration", "integrity",
    "sources_2025_unchanged", "frozen_2026_inputs_unchanged", "no_database", "no_real_orders", "identity_bridge",
    "matched_products", "units_2026", "renumbering_performed", "evaluation_2026", "cohort", "range",
    "raw_sources_reaudited", "label", "event_definition", "calibration_claim", "scaled_aggregation",
    "macro_definition", "volume_definition", "volume_period", "volume_caveat",
    "demand", "movements", "dates", "minimum", "maximum", "row_count", "quantity_unit",
    "calendar_scenario", "absence_is_observed_zero", "completeness_demonstrated", "snapshot_not_demand",
    "production_integration", "monetary_evaluation",
    "gate",
    "configuration", "minimum_training_periods", "minimum_positive_training_periods", "minimum_publicable_folds",
    "alpha", "beta", "baseline_window", "bootstrap_seed", "bootstrap_iterations", "development_target_start",
    "development_latest_target_end", "2025_evaluation_start", "2026_first_origin_index", "2025_calendar_policy",
    "2025_transfer_calendar_policy", "2026_calendar_policy", "time_units", "2026_views", "scaling_boundary_policy",
    "source_positive_dates_2025", "source_period_2026", "uncertainty_scope", "scenario_selection_on_2026",
    "original_protocol_changed", "paired_sku_count", "candidate_sku_count", "reference_sku_count",
    "discarded_candidate_skus", "discarded_reference_skus", "pair_identity", "aggregation",
    "candidate_macro_metrics", "reference_macro_metrics", "difference_candidate_minus_reference",
    "metric_pair_counts", "available_pairs", "unavailable_pairs",
})
_TOKENS = frozenset({
    "synthetic_structured", "real_observed", "experimental_evaluation", "calendar_day", "operational_day",
    "available", "limited", "abstain", "unavailable", "observed", "training_only", "experimental",
    "lost_sales", "backorder", "fixed_order_up_to", "forecast_order_up_to", "sku", "sku_origin",
    "product", "product_origin", "paired_product", "paired_sku", "pooled", "experimental_2025",
    "Demanda_limpia_2025_CORREGIDA_FINAL.csv", "Movimientos_Detalle_2025_CORREGIDO_FINAL.csv",
    "Existencias_2025_CORREGIDAS_FINAL.csv", "EVALUACIÓN EXPERIMENTAL 2025",
    "empirical_2026_series_strict.csv", "STRICT", "development", "development_pooled_mae",
    "sku_cluster_all_paired_origins", "candidate_minus_reference", "per_sku_mean_absolute_error",
    "paired_cluster_percentile_bootstrap", "horizon_total_demand_greater_than_zero",
    "observed_evaluation_coverage_not_guaranteed_calibration", "per_target_training_scaled_error",
    "unweighted_mean_per_sku;quantities_not_homogeneous_catalog_total",
    "sum_of_target_partition_demand_for_declared_universe", "union_of_nonoverlapping_target_dates",
    "recorded_quantity_index_mixes_sku_units_not_economic_or_physical_total",
    "explicit_positive_dates_only", "calendar_zero_fill_synthetic_scenario", "source_product_unit_no_conversion",
    "PASSED", "PASSED_WITH_WARNINGS", "FAILED",
    "assumed_synthetic_complete_calendar_from_first_positive",
    "synthetic_activity_dates_exposure_scenario_not_real_operating_calendar", "frozen_verified_operational_calendar",
    "exclude_cross_year_training_differences", "two_part_only;other_candidates_not_requested",
    "sku_model_scenario_start_end_time_unit_horizon",
    "unweighted_mean_of_paired_sku_metrics;quantities_are_catalog_indices",
}) | _MODELS | _SEGMENTS | _REASONS | _SCENARIOS | frozenset(VIEW_KINDS)


def _finite_tree(value, depth=0):
    if depth > 20:
        return False
    if isinstance(value, (int, float)):
        try:
            return math.isfinite(value)
        except OverflowError:
            return False
    if isinstance(value, dict):
        return all(_finite_tree(item, depth + 1) for item in value.values())
    if isinstance(value, list):
        return all(_finite_tree(item, depth + 1) for item in value)
    return True


def _safe_text(value, key):
    if value in _TOKENS or value.lower() in _MODELS:
        return value
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            return None
    return None


def _aggregate(value, key="", depth=0):
    """Allowlist aggregate fields; no identities, prices, paths, raw rows or free text."""
    if depth > 12:
        return None
    if isinstance(value, dict):
        allowed = _KEYS | _MODELS | _SEGMENTS | {"7", "30", "2025", "2026"} | frozenset(VIEW_KINDS)
        if key in _REASON_KEYS:
            allowed |= _REASONS | {model + ":" + reason for model in _MODELS for reason in _REASONS}
        if key in {"results", "inventory"}:
            allowed |= {scenario + ":" + model for scenario in _SCENARIOS for model in _MODELS}
        return {name: _aggregate(item, name, depth + 1) for name, item in value.items() if name in allowed}
    if isinstance(value, list):
        if key not in _LIST_KEYS:
            return None
        return [_aggregate(item, key, depth + 1) for item in value]
    if isinstance(value, str):
        return _safe_text(value, key)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return None


def _unavailable(reason):
    return {"available": False, "status": "unavailable", "reason": reason,
            "metadata": {"data_kind": "synthetic_structured", "year": 2025,
                         "purpose": "experimental_evaluation", "evaluation_label": "EVALUACIÓN EXPERIMENTAL 2025"},
            "views": {}, "metric_rows": []}


def _validate_metadata(payload):
    metadata = payload.get("metadata", payload)
    return (isinstance(metadata, dict) and metadata.get("data_kind") == "synthetic_structured"
            and metadata.get("year") == 2025 and metadata.get("purpose") == "experimental_evaluation")


def _metric_values(values):
    metrics = values.get("metrics", values)
    if not isinstance(metrics, dict):
        return dict.fromkeys(METRICS)
    legacy = metrics.get("legacy_metrics", metrics)
    scaled = metrics.get("scaled_metrics", metrics)
    legacy = legacy if isinstance(legacy, dict) else {}
    scaled = scaled if isinstance(scaled, dict) else {}
    result = {metric: legacy.get(metric) for metric in METRICS[:4]}
    for metric in METRICS[4:]:
        value = scaled.get(metric)
        result[metric] = value.get("value") if isinstance(value, dict) else value
    return result


def _horizon_rows(view, horizon, data):
    if not isinstance(data, dict):
        return []
    models = data.get("models", data.get("metrics", {}))
    if not isinstance(models, dict):
        return []
    return [dict(view=view, horizon=horizon, time_unit=data.get("time_unit"), model=name, **_metric_values(values))
            for name, values in models.items() if name in _MODELS and isinstance(values, dict)]


def _metric_rows(views):
    rows = []
    for name, view in views.items():
        horizons = view.get("horizons")
        if not isinstance(horizons, dict):
            continue
        for horizon, data in horizons.items():
            rows.extend(_horizon_rows(name, horizon, data))
    return rows


def _segment_chart(characterization):
    if not isinstance(characterization, dict):
        return []
    profile = characterization.get("2025_ONLY", {})
    if not isinstance(profile, dict):
        return []
    distribution = profile.get("segments", profile.get("zero_rate_segments", profile.get("segment_distribution",
                                profile.get("by_zero_rate_segment", {}))))
    if not isinstance(distribution, dict):
        return []
    counts = {name: count for name, count in distribution.items()
              if name in _SEGMENTS and isinstance(count, int) and 0 <= count <= 2 ** 53}
    total = sum(counts.values())
    return [{"segment": name, "count": count, "percent": 100 * count / total if total else 0}
            for name, count in counts.items()]


def _nested(value, *keys):
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _source_ranges(result):
    actual_range = _nested(result, "metadata", "evaluation_2026", "range")
    actual_range = actual_range if isinstance(actual_range, list) and len(actual_range) == 2 else [None, None]
    return {"2025": {"start": _nested(result, "audit", "sources", "demand", "dates", "minimum"),
                     "end": _nested(result, "audit", "sources", "demand", "dates", "maximum")},
            "2026": {"start": actual_range[0], "end": actual_range[1]}}


def present_report(payload):
    """Present the technical report without changing its stored contents."""
    if not isinstance(payload, dict) or not _finite_tree(payload):
        return _unavailable("invalid_report")
    if not _validate_metadata(payload):
        return _unavailable("invalid_provenance")
    audit = payload.get("audit", {})
    if isinstance(audit, dict) and audit.get("gate") == "FAILED":
        return _unavailable("audit_failed")
    raw_views = payload.get("views", {})
    if not isinstance(raw_views, dict):
        return _unavailable("invalid_report")
    views = {}
    for name, kinds in VIEW_KINDS.items():
        raw = raw_views.get(name)
        if not isinstance(raw, dict):
            continue
        views[name] = _aggregate(raw)
        views[name]["provenance"] = kinds
    if not views:
        return _unavailable("no_evaluable_views")
    result = _aggregate(payload)
    result.update({"available": True, "status": "available", "reason": None,
                   "views": views, "metric_rows": _metric_rows(views),
                   "segment_chart": _segment_chart(result.get("characterization", {}))})
    metadata = result.get("metadata", {})
    metadata.update(_unavailable(None)["metadata"])
    result["metadata"] = metadata
    result["source_ranges"] = _source_ranges(result)
    return result


def read_report(report_path, *, report_root=None):
    """Read only an explicitly configured JSON beneath the generated-report directory."""
    if not report_path:
        return _unavailable("report_not_configured")
    try:
        root = Path(report_root or REPORT_ROOT).resolve()
        path = Path(report_path).resolve()
        if not path.is_relative_to(root) or path.suffix.lower() != ".json":
            return _unavailable("report_path_not_allowed")
        if path.stat().st_size > 64 * 1024 * 1024:
            return _unavailable("report_too_large")
        with path.open("r", encoding="utf-8-sig") as handle:
            payload = json.load(handle, parse_constant=_reject_nonfinite)
    except FileNotFoundError:
        return _unavailable("report_not_found")
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        return _unavailable("invalid_report")
    return present_report(payload)


def _reject_nonfinite(_value):
    raise ValueError("Nonfinite JSON number")
