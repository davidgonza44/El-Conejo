"""Predeclared offline views. Targets never select a model or scenario."""
from collections import Counter, defaultdict
from datetime import date, timedelta
from math import fsum
from types import SimpleNamespace

from . import experimental_demand_service as demand
from . import experimental_2025_models as models
from . import experimental_2025_statistics as statistics

MIN_TRAIN = 60
MIN_POSITIVE = 8
MIN_FOLDS = 3
SEED = 2025
BOOTSTRAP_ITERATIONS = 1000
VIEWS = ("2025_ONLY", "2026_REAL_ONLY", "2025_TO_2026_TRANSFER",
         "2025_PLUS_2026_OBSERVED_TO_DATE")


def profile(points, time_unit):
    series = SimpleNamespace(values=[p["quantity"] for p in points], granularity="daily",
                             time_unit=time_unit)
    return {**demand.demand_profile(series), "observations_available": len(points)}


def characterize(panel, universe, time_unit):
    rows = {}
    reasons = Counter()
    for code in universe:
        points = panel.get(code, [])
        result = profile(points, time_unit)
        reason = eligibility(points)
        result.update(evaluable=reason is None, reason=reason)
        rows[code] = result
        if reason:
            reasons[reason] += 1
    return rows, {"universe": len(universe), "evaluable": sum(r["evaluable"] for r in rows.values()),
                  "nonevaluable": sum(not r["evaluable"] for r in rows.values()),
                  "reasons": dict(reasons), "time_unit": time_unit,
                  "segments": dict(Counter(r["zero_rate_segment"] for r in rows.values())),
                  "adi_cv2_categories": dict(Counter(r["adi_cv2_classification"] for r in rows.values()))}


def eligibility(points):
    if not points:
        return "no_source_demand"
    if len(points) < MIN_TRAIN:
        return "insufficient_training_periods"
    if sum(p["quantity"] > 0 for p in points) < MIN_POSITIVE:
        return "insufficient_positive_training_periods"
    return None


def origins(calendar, horizon, view):
    if view == "2025_ONLY":
        first = next((index for index, day in enumerate(calendar) if day >= date(2025, 7, 1)), len(calendar))
    else:
        first = MIN_TRAIN
    return [calendar[index:index + horizon] for index in range(first, len(calendar) - horizon + 1, horizon)]


def training(points, origin, view, history):
    if view not in VIEWS:
        raise ValueError("Unknown experimental view")
    if any(p["date"].year != 2025 or p["data_kind"] != "synthetic_structured" for p in history):
        raise ValueError("Transfer development history must remain synthetic 2025")
    history = [p for p in history if p["date"] < origin]
    observed = [p for p in points if p["date"] < origin]
    if view == "2025_TO_2026_TRANSFER":
        return history
    if view == "2025_PLUS_2026_OBSERVED_TO_DATE":
        return history + observed
    return observed


def development_selection(panel, horizon):
    rows = []
    for code, points in panel.items():
        fit = [p for p in points if p["date"] < date(2025, 6, 1)]
        target = [p for p in points if date(2025, 6, 1) <= p["date"] < date(2025, 6, 1) + timedelta(days=horizon)]
        if eligibility(fit) or len(target) != horizon:
            continue
        for model in models.BASELINES:
            candidate = models.forecast_candidate(model, [p["quantity"] for p in fit],
                                                  [p["date"] for p in fit], horizon,
                                                  with_uncertainty=False)
            if candidate["available"]:
                row = scored_row(code, model, fit, target, candidate, "calendar_day")
                row["selection_role"] = "development"
                rows.append(row)
    return statistics.select_development_baseline(rows)


def scored_row(code, model, fit, targets, candidate, time_unit, *, fit_profile=None):
    actual, predicted = [p["quantity"] for p in targets], candidate["forecast"]
    metrics = models.score_forecast([p["quantity"] for p in fit], actual, predicted,
                                    training_dates=[p["date"] for p in fit], time_unit=time_unit)
    return {"product_code": code, "model": model, "origin": targets[0]["date"].isoformat(),
            "target_dates": [p["date"].isoformat() for p in targets], "actual": actual,
            "predicted": predicted, "metrics": metrics, "status": "SUCCESS",
            "two_part": candidate["two_part"], "uncertainty": candidate["uncertainty"],
            "prediction_policy": candidate["prediction_policy"],
            "profile": fit_profile if fit_profile is not None else profile(fit, time_unit),
            "training_count": len(fit), "training_end": fit[-1]["date"].isoformat(),
            "training_sources": dict(Counter(p["data_kind"] for p in fit)),
            "target_sources": dict(Counter(p["data_kind"] for p in targets)),
            "time_unit": time_unit}


def evaluate_view(panel, calendar, *, universe, view, horizon, history_panel=None,
                  bridge=None, baseline_selection=None, emit=lambda row: None):
    if view not in VIEWS:
        raise ValueError("Unknown experimental view")
    expected_year, expected_kind = ((2025, "synthetic_structured") if view == "2025_ONLY" else
                                     (2026, "real_observed"))
    if any(p["date"].year != expected_year or p["data_kind"] != expected_kind
           for points in panel.values() for p in points):
        raise ValueError("Evaluation targets must retain their declared year and provenance")
    time_unit = "calendar_day" if view == "2025_ONLY" else "operational_day"
    history_panel, bridge = history_panel or {}, bridge or {}
    target_grid = origins(calendar, horizon, view)
    evaluated_dates = {day for days in target_grid for day in days}
    volumes = {code: fsum(p["quantity"] for p in panel.get(code, []) if p["date"] in evaluated_dates)
               for code in universe}
    by_model = {model: [] for model in models.MODELS}
    policies = {model: {} for model in models.MODELS}
    reasons = {model: Counter() for model in models.MODELS}
    for code in universe:
        observations = panel.get(code, [])
        dated = {p["date"]: p for p in observations}
        for target_dates in target_grid:
            targets = [dated[day] for day in target_dates if day in dated]
            fit = training(observations, target_dates[0], view, history_panel.get(code, []))
            reason = ("cross_year_identity_or_unit_unverified" if view in VIEWS[2:] and code not in bridge else
                      "incomplete_real_targets" if len(targets) != horizon else eligibility(fit))
            fit_profile = profile(fit, time_unit) if not reason else None
            for model in models.MODELS:
                if reason:
                    candidate = {"available": False, "unavailable_reason": reason,
                                 "prediction_policy": {"status": "abstain", "reason": [reason]}}
                else:
                    candidate = models.forecast_candidate(model, [p["quantity"] for p in fit],
                                                          [p["date"] for p in fit], horizon,
                                                          time_unit=time_unit, aggregation_size=7,
                                                          with_uncertainty=(model == "two_part"))
                policies[model][code] = candidate["prediction_policy"]
                if not candidate["available"]:
                    reasons[model][candidate["unavailable_reason"]] += 1
                    continue
                row = scored_row(code, model, fit, targets, candidate, time_unit, fit_profile=fit_profile)
                by_model[model].append(row)
                emit({"view": view, "horizon": horizon, **row})
    return summarize_view(by_model, policies, reasons, volumes, target_grid,
                          time_unit, baseline_selection), by_model


def summarize_view(groups, policies, reasons, volumes, target_grid, time_unit, selection):
    metrics, coverage, abstention, segments, calibration = {}, {}, {}, {}, {}
    for model, rows in groups.items():
        counts = Counter(row["product_code"] for row in rows)
        evaluable = {code for code, count in counts.items() if count >= MIN_FOLDS}
        public_rows = [row for row in rows if row["product_code"] in evaluable]
        metrics[model] = statistics.summarize_pairs(public_rows)
        metrics[model]["all_successful_origin_metrics"] = statistics.summarize_pairs(rows)
        coverage[model] = statistics.coverage_summary(volumes, evaluable)
        coverage[model].update(minimum_folds=MIN_FOLDS, success_fold_count=len(rows),
                               unavailable_reasons=dict(reasons[model]),
                               volume_period="union_of_nonoverlapping_target_dates",
                               volume_caveat="recorded_quantity_index_mixes_sku_units_not_economic_or_physical_total")
        abstention[model] = statistics.abstention_summary(volumes, policies[model])
        abstention[model]["policy_origin"] = "last_scheduled_origin;not_minimum_fold_publicability"
        segments[model] = statistics.segment_summary(public_rows)
        calibration[model] = statistics.calibration_summary(rows)
    return {"time_unit": time_unit, "calendar_horizon_claim": time_unit == "calendar_day",
            "origin_count": len(target_grid), "target_date_count": sum(map(len, target_grid)),
            "coverage": coverage, "metrics": metrics, "abstention": abstention,
            "segments": segments, "calibration": calibration,
            "baseline_selection": selection,
            "bootstrap": bootstrap_candidates(groups, selection)}


def row_key(row):
    return row["product_code"], row["origin"], tuple(row["target_dates"])


def bootstrap_candidates(groups, selection):
    baseline = selection.get("model") if selection and selection.get("available") else None
    result = {}
    reference = {row_key(row): row for row in groups.get(baseline, [])}
    for model in ("tsb", "two_part", "adida"):
        candidate = {row_key(row): row for row in groups[model]}
        keys = sorted(candidate.keys() & reference.keys())
        paired = statistics.paired_bootstrap([candidate[key] for key in keys], [reference[key] for key in keys],
                                             seed=SEED, iterations=BOOTSTRAP_ITERATIONS)
        result[model] = {**paired, "reference_model": baseline,
                         "explicit_common_support": True, "candidate_fold_count": len(candidate),
                         "reference_fold_count": len(reference), "paired_fold_count": len(keys),
                         "discarded_candidate_folds": len(candidate) - len(keys),
                         "discarded_reference_folds": len(reference) - len(keys)}
    return result


def temporal_comparison(real_groups, expanding_groups):
    result = {}
    for model in models.MODELS:
        real = {row_key(row): row for row in real_groups[model]}
        expanding = {row_key(row): row for row in expanding_groups[model]}
        keys = sorted(real.keys() & expanding.keys())
        if any(real[key]["actual"] != expanding[key]["actual"] for key in keys):
            raise ValueError("Temporal comparison requires identical actual targets")
        a = statistics.summarize_pairs([real[key] for key in keys])
        b = statistics.summarize_pairs([expanding[key] for key in keys])
        real_rows, expanding_rows = [real[key] for key in keys], [expanding[key] for key in keys]
        volumes = defaultdict(float)
        real_policies, expanding_policies = {}, {}
        for left, right in zip(real_rows, expanding_rows):
            code = left["product_code"]
            volumes[code] += fsum(left["actual"])
            real_policies[code] = left["prediction_policy"]
            expanding_policies[code] = right["prediction_policy"]
        result[model] = {"same_pairs": len(keys), "real_only_total_folds": len(real),
                         "expanding_total_folds": len(expanding), "real_only": a, "expanding": b,
                         "difference_expanding_minus_real": {
                             metric: b[metric] - a[metric] if b[metric] is not None and a[metric] is not None else None
                             for metric in ("wape", "mae", "rmse", "bias", "mase", "rmsse")},
                         "scenario_selected": False,
                         "real_only_calibration": statistics.calibration_summary(real_rows),
                         "expanding_calibration": statistics.calibration_summary(expanding_rows),
                         "real_only_abstention": statistics.abstention_summary(volumes, real_policies),
                         "expanding_abstention": statistics.abstention_summary(volumes, expanding_policies),
                         "matched_sku_count": len(volumes),
                         "unavailable_reason": None if keys else "no_verified_common_transfer_pairs"}
    return result
