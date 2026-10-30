"""Pure experimental summaries, chronology diagnostics and paired inference.

No production objects are imported. Callers carry source/provenance metadata and
select the explicit universe, target view and exposure scenario before scoring.
"""
from collections import Counter, defaultdict
from math import fsum, isfinite, sqrt
import random
from statistics import mean, median

from .experimental_2025_models import BASELINES

MIN_CALIBRATION_ROWS = 20
MIN_BIN_ROWS = 5
MIN_BOOTSTRAP_SKUS = 5
INVENTORY_METRICS = ("total_demand", "fulfilled_units", "unmet_units", "fill_rate", "stockout_periods",
                     "cycle_service_level", "average_on_hand", "max_on_hand", "ending_stock",
                     "order_count", "total_ordered_units")


def _nonnegative(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
        raise ValueError("Finite nonnegative quantities are required")
    return float(value)


def _ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "percent": numerator / denominator * 100 if denominator else None,
            "unavailable_reason": None if denominator else "zero_denominator"}


def coverage_summary(universe_volumes, evaluable_codes):
    """Universe volume is explicit demand, never inventory stock or forecast."""
    volumes = {str(code): _nonnegative(value) for code, value in universe_volumes.items()}
    codes = {str(code) for code in evaluable_codes}
    if not codes.issubset(volumes):
        raise ValueError("Evaluable SKU must belong to the declared universe")
    return {"sku": _ratio(len(codes), len(volumes)),
            "volume": _ratio(fsum(volumes[code] for code in codes), fsum(volumes.values())),
            "volume_definition": "sum_of_target_partition_demand_for_declared_universe",
            "evaluable_sku": len(codes), "nonevaluable_sku": len(volumes) - len(codes)}


def abstention_summary(universe_volumes, policies):
    volumes = {str(code): _nonnegative(value) for code, value in universe_volumes.items()}
    totals = fsum(volumes.values())
    result = {}
    for state in ("available", "limited", "abstain"):
        codes, reasons = [], Counter()
        for code in volumes:
            policy = policies.get(code, {"status": "abstain", "reason": ["no_evaluable_origin"]})
            if policy["status"] not in ("available", "limited", "abstain"):
                raise ValueError("Unknown abstention status")
            if policy["status"] == state:
                codes.append(code)
                reasons.update(policy.get("reason", []))
        result[state] = {"sku": _ratio(len(codes), len(volumes)),
                         "volume": _ratio(fsum(volumes[code] for code in codes), totals),
                         "reasons": dict(sorted(reasons.items()))}
    return result


def _successful(rows):
    return [row for row in rows if row.get("available", row.get("status") == "SUCCESS")
            and row.get("actual") and len(row["actual"]) == len(row.get("predicted", []))]


def summarize_pairs(rows):
    """Pooled daily errors plus pair-weighted scaled errors with denominators.

    RMSSE is sqrt(mean(per-target squared scaled error)); unavailable scales
    are excluded and counted explicitly. Neither scaled mean selects a model.
    """
    rows = _successful(rows)
    actual, predicted = [], []
    for row in rows:
        actual.extend(_nonnegative(value) for value in row["actual"])
        predicted.extend(_nonnegative(value) for value in row["predicted"])
    n, total = len(actual), fsum(actual)
    errors = [forecast - observed for observed, forecast in zip(actual, predicted)]
    absolute = fsum(abs(error) for error in errors)
    squared = fsum(error * error for error in errors)
    scaled = {}
    counts = {}
    for metric in ("mase", "rmsse"):
        available = [(_nonnegative(row["metrics"][metric]), len(row["actual"])) for row in rows
                     if row.get("metrics", {}).get(metric) is not None]
        count = sum(size for _, size in available)
        value = (fsum((value * value if metric == "rmsse" else value) * size
                      for value, size in available) / count) if count else None
        scaled[metric] = sqrt(value) if metric == "rmsse" and value is not None else value
        counts[metric] = {"available_pair_count": count, "unavailable_pair_count": n - count}
    return {"fold_count": len(rows), "sku_count": len({row["product_code"] for row in rows}),
            "n": n, "actual_total": total, "absolute_error_total": absolute,
            "squared_error_total": squared, "signed_error_total": fsum(errors),
            "wape": absolute / total * 100 if total else None,
            "mae": absolute / n if n else None, "rmse": sqrt(squared / n) if n else None,
            "bias": fsum(errors) / n if n else None, **scaled,
            "scaled_counts": counts, "scaled_aggregation": "per_target_training_scaled_error",
            "unavailable_reason": "no_evaluation_pairs" if not n else
                                  "zero_target_demand_for_wape" if not total else None}


def segment_summary(rows, *, minimum_skus=5):
    result = {}
    dimensions = ("zero_rate_segment", "adi_cv2_classification")
    for dimension in dimensions:
        groups = defaultdict(list)
        for row in rows:
            segment = row.get(dimension, row.get("profile", {}).get(dimension, "unavailable"))
            groups[segment].append(row)
        result[dimension] = {}
        for name, group in sorted(groups.items()):
            summary = summarize_pairs(group)
            sufficient = summary["sku_count"] >= minimum_skus
            result[dimension][name] = {"available": sufficient,
                                       "unavailable_reason": None if sufficient else "insufficient_segment_skus",
                                       "minimum_skus": minimum_skus, "summary": summary}
    return result


def _brier(rows):
    pairs = []
    unavailable = Counter()
    for row in rows:
        candidate = row.get("two_part") or {}
        probability = candidate.get("occurrence_probability")
        if not candidate.get("available") or probability is None:
            unavailable[candidate.get("unavailable_reason") or "two_part_unavailable"] += 1
        elif not row.get("actual"):
            unavailable["target_unavailable"] += 1
        else:
            probability = _nonnegative(probability)
            if probability > 1:
                raise ValueError("Occurrence probability must be within [0, 1]")
            pairs.append((probability, float(fsum(row["actual"]) > 0)))
    count = len(pairs)
    bins = []
    sufficient = count >= MIN_CALIBRATION_ROWS
    if sufficient:
        for index in range(5):
            low, high = index / 5, (index + 1) / 5
            group = [(probability, target) for probability, target in pairs
                     if low <= probability < high or (index == 4 and probability == 1)]
            usable = len(group) >= MIN_BIN_ROWS
            bins.append({"lower": low, "upper": high, "count": len(group), "available": usable,
                         "mean_predicted_probability": mean(probability for probability, _ in group) if usable else None,
                         "observed_positive_rate": mean(target for _, target in group) if usable else None,
                         "unavailable_reason": None if usable else "insufficient_bin_count"})
    return {"brier_score": mean((probability - target) ** 2 for probability, target in pairs) if count else None,
            "count": count, "unavailable_count": sum(unavailable.values()),
            "unavailable_reasons": dict(sorted(unavailable.items())), "bins": bins,
            "bins_available": sufficient,
            "minimum_count": MIN_CALIBRATION_ROWS, "minimum_bin_count": MIN_BIN_ROWS,
            "bins_unavailable_reason": None if sufficient else "insufficient_calibration_rows",
            "event_definition": "horizon_total_demand_greater_than_zero"}


def _intervals(rows):
    available, unavailable = [], Counter()
    for row in rows:
        interval = row.get("uncertainty") or {}
        if not interval.get("available"):
            unavailable[interval.get("unavailable_reason") or "interval_unavailable"] += 1
            continue
        if not row.get("actual"):
            unavailable["target_unavailable"] += 1
            continue
        lower, upper = _nonnegative(interval["lower_bound"]), _nonnegative(interval["upper_bound"])
        if lower > upper:
            raise ValueError("Interval bounds must be ordered")
        nominal = _nonnegative(interval["nominal_coverage"])
        if nominal > 1:
            raise ValueError("Nominal coverage must be within [0, 1]")
        total = fsum(row["actual"])
        available.append((lower <= total <= upper, upper - lower, interval))
    count = len(available)
    nominals = sorted({item[2]["nominal_coverage"] for item in available})
    return {"nominal_coverage": nominals[0] if len(nominals) == 1 else None,
            "nominal_coverages": nominals, "observed_coverage": mean(hit for hit, _, _ in available) if count else None,
            "evaluation_count": count, "covered_count": sum(hit for hit, _, _ in available),
            "calibration_count": sum(item[2].get("calibration_count", 0) for item in available),
            "calibration_count_definition": "sum_of_origin_training_calibration_blocks_not_unique_external_targets",
            "average_interval_width": mean(width for _, width, _ in available) if count else None,
            "unavailable_count": sum(unavailable.values()), "unavailable_reason": dict(sorted(unavailable.items())),
            "calibration_claim": "observed_evaluation_coverage_not_guaranteed_calibration"}


def calibration_summary(rows):
    """Brier and external observed interval coverage, never nominal=calibrated."""
    rows = list(rows)
    return {"occurrence": _brier(rows), "intervals": _intervals(rows),
            "dependence_warning": "origins_within_sku_can_be_dependent; report_cluster_count"}


def _key(row):
    return (str(row["product_code"]), str(row.get("warehouse_code", "")),
            str(row["origin"]), tuple(str(day) for day in row["target_dates"]))


def _indexed(rows):
    indexed = {}
    for row in _successful(rows):
        key = _key(row)
        if key in indexed:
            raise ValueError("Duplicate evaluation identity")
        for value in row["actual"] + row["predicted"]:
            _nonnegative(value)
        indexed[key] = row
    return indexed


def _unavailable_bootstrap(reason, **metadata):
    return {"available": False, "unavailable_reason": reason,
            "mean_difference": None, "median_difference": None,
            "confidence_interval": None, **metadata}


def _quantile(values, probability):
    ordered = sorted(values)
    location = (len(ordered) - 1) * probability
    lower = int(location)
    fraction = location - lower
    return ordered[lower] * (1 - fraction) + ordered[min(lower + 1, len(ordered) - 1)] * fraction


def paired_bootstrap(candidate_rows, reference_rows, *, seed=2025, iterations=1000, confidence=0.95):
    """Resample SKU clusters with all origins paired; difference in per-SKU MAE.

    Exactly equal product/warehouse/origin/target identities and actual targets
    are required. A caller may explicitly choose a matched subset beforehand,
    but this function never silently intersects incomplete comparisons.
    """
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 1:
        raise ValueError("Positive bootstrap iterations are required")
    if not 0 < confidence < 1:
        raise ValueError("Confidence must be within (0, 1)")
    candidate, reference = _indexed(candidate_rows), _indexed(reference_rows)
    metadata = {"unit": "sku_cluster_all_paired_origins", "seed": seed, "iterations": iterations,
                "confidence_level": confidence, "N": len(candidate), "difference_sign": "candidate_minus_reference",
                "metric": "per_sku_mean_absolute_error", "selection_on_evaluation_targets": False}
    if not candidate or candidate.keys() != reference.keys():
        return _unavailable_bootstrap("unequal_or_empty_evaluation_pairs", **metadata)
    clustered = defaultdict(list)
    for key, row in candidate.items():
        other = reference[key]
        if row["actual"] != other["actual"]:
            return _unavailable_bootstrap("target_values_differ", **metadata)
        errors = [(abs(predicted - actual) - abs(baseline - actual))
                  for actual, predicted, baseline in zip(row["actual"], row["predicted"], other["predicted"])]
        clustered[key[0]].extend(errors)
    differences = [mean(errors) for _, errors in sorted(clustered.items())]
    metadata["N_sku"] = len(differences)
    if len(differences) < MIN_BOOTSTRAP_SKUS:
        return _unavailable_bootstrap("insufficient_sku_clusters", **metadata)
    rng = random.Random(seed)
    bootstrap = [mean(rng.choices(differences, k=len(differences))) for _ in range(iterations)]
    alpha = (1 - confidence) / 2
    return {"available": True, "unavailable_reason": None,
            "mean_difference": mean(differences), "median_difference": median(differences),
            "confidence_interval": [_quantile(bootstrap, alpha), _quantile(bootstrap, 1 - alpha)],
            "method": "paired_cluster_percentile_bootstrap", **metadata}


def select_development_baseline(development_rows):
    """Choose only from caller-declared development folds, on common pairs.

    Explicit selection_role=development is mandatory; test/transfer rows are
    rejected. Tie break follows fixed BASELINES order. No zero benchmark tuning.
    """
    groups = defaultdict(list)
    for row in development_rows:
        if row.get("selection_role") != "development":
            raise ValueError("Baseline selection requires development-only rows")
        if row.get("model") in BASELINES:
            groups[row["model"]].append(row)
    common = None
    for model in BASELINES:
        keys = set(_indexed(groups[model]))
        common = keys if common is None else common & keys
    if not common:
        return {"available": False, "model": None, "reason": "no_common_development_pairs"}
    scored = {}
    for model in BASELINES:
        matched = [row for row in groups[model] if _key(row) in common]
        scored[model] = summarize_pairs(matched)["mae"]
    winner = min(BASELINES, key=lambda model: scored[model])
    return {"available": True, "model": winner, "metric": "development_pooled_mae",
            "scores": scored, "paired_fold_count": len(common),
            "selection_role": "development", "selection_on_evaluation_targets": False}


def _inventory_key(row):
    return (str(row["product_code"]), str(row["model"]), str(row["scenario_id"]),
            str(row["start"]), str(row["end"]), str(row["time_unit"]), str(row["horizon"]))


def _inventory_index(rows):
    groups = defaultdict(dict)
    for row in rows:
        group = str(row["scenario_id"]) + ":" + str(row["model"])
        key = _inventory_key(row)
        if key in groups[group]:
            raise ValueError("Duplicate inventory simulation identity")
        _nonnegative(row["metrics"]["total_demand"])
        groups[group][key] = row
    return groups


def _paired_inventory_metrics(candidate, reference, keys):
    candidate_means, reference_means, differences, counts = {}, {}, {}, {}
    for metric in INVENTORY_METRICS:
        pairs = [(candidate[key]["metrics"][metric], reference[key]["metrics"][metric])
                 for key in keys if candidate[key]["metrics"].get(metric) is not None
                 and reference[key]["metrics"].get(metric) is not None]
        numeric = [(_nonnegative(first), _nonnegative(second)) for first, second in pairs]
        counts[metric] = {"available_pairs": len(numeric), "unavailable_pairs": len(keys) - len(numeric)}
        candidate_means[metric] = mean(first for first, _ in numeric) if numeric else None
        reference_means[metric] = mean(second for _, second in numeric) if numeric else None
        differences[metric] = mean(first - second for first, second in numeric) if numeric else None
    return {"candidate_macro_metrics": candidate_means, "reference_macro_metrics": reference_means,
            "difference_candidate_minus_reference": differences, "metric_pair_counts": counts}


def paired_inventory_comparison(candidate_rows, reference_rows):
    """Descriptive paired simulation comparison; never choose a scenario.

    Keys require SKU/model/scenario/start/end/time unit/horizon equality. Exact
    demand totals must also match. The common support is explicit, with discarded
    counts; quantities retain the catalog index limitation. No significance is
    claimed by this arithmetic comparison, and no orders can be created.
    """
    candidate_groups, reference_groups = _inventory_index(candidate_rows), _inventory_index(reference_rows)
    result = {}
    for group in sorted(candidate_groups.keys() | reference_groups.keys()):
        candidate, reference = candidate_groups.get(group, {}), reference_groups.get(group, {})
        keys = sorted(candidate.keys() & reference.keys())
        if any(candidate[key]["metrics"]["total_demand"] != reference[key]["metrics"]["total_demand"] for key in keys):
            raise ValueError("Paired inventory comparison requires identical actual demand totals")
        result[group] = {"available": bool(keys),
                         "unavailable_reason": None if keys else "no_identical_simulation_pairs",
                         "paired_sku_count": len(keys), "candidate_sku_count": len(candidate),
                         "reference_sku_count": len(reference),
                         "discarded_candidate_skus": len(candidate) - len(keys),
                         "discarded_reference_skus": len(reference) - len(keys),
                         "pair_identity": "sku_model_scenario_start_end_time_unit_horizon",
                         "explicit_common_support": True, "scenario_selected": False,
                         "aggregation": "unweighted_mean_of_paired_sku_metrics;quantities_are_catalog_indices",
                         **_paired_inventory_metrics(candidate, reference, keys)}
    return result
