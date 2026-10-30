"""Descriptive reporting only; forecast and error formulas remain in services."""
from collections import Counter, defaultdict
import math
import statistics

MODELS = ("naive", "mean", "moving_average", "linear_trend", "arima", "random_forest")
BASELINES = MODELS[:4]
STRATA = ("zeros_lt_50pct", "zeros_50_to_lt_80pct", "zeros_80_to_lt_95pct", "zeros_ge_95pct")
METRICS = ("mae", "rmse", "bias", "wape", "mape")


def descriptor(series):
    positive = sum(value > 0 for value in series.values)
    size = len(series.values)
    zero = (size - positive) / size
    level = sum(zero >= edge for edge in (0.5, 0.8, 0.95))
    return {"observed_days": size, "positive_days": positive, "zero_proportion": zero,
            "adi_observed": size / positive if positive else None, "stratum": STRATA[level]}


def tied(a, b):
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)


def winners(rows, metric):
    defined = [row for row in rows if row[metric] is not None]
    if not defined:
        return []
    best = min(row[metric] for row in defined)
    return [row["model"] for row in defined if tied(row[metric], best)]


def product_rows(evaluation, series):
    common = evaluation.valid_common_folds
    rows = [{"product_code": evaluation.rows[0].product_code, "model": model,
             "is_service": series.is_service, "valid_common_folds": common,
             "publishable": common >= 3, **descriptor(series),
             **evaluation.common_metrics(model).to_dict()} for model in MODELS]
    for metric in ("mae", "rmse", "wape"):
        leaders = winners(rows, metric) if common >= 3 else []
        for row in rows:
            row["lowest_" + metric] = row["model"] in leaders
            row["lowest_" + metric + "_tie_size"] = len(leaders)
    return rows


def percentage(count, total):
    return 100 * count / total if total else None


def quantile(values, fraction):
    """Linear interpolation at (n-1)*q (Hyndman-Fan type 7)."""
    values = sorted(values)
    if not values:
        return None
    index = (len(values) - 1) * fraction
    low = int(index)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (index - low)


def macro(rows):
    result = []
    for model in MODELS:
        selected = [row for row in rows if row["model"] == model and row["publishable"]]
        values = {metric: [row[metric] for row in selected if row[metric] is not None]
                  for metric in METRICS}
        size = len(selected)
        pairs = sum(row["n"] for row in selected)
        used = sum(row["mape_n"] for row in selected)
        result.append({"model": model, "products": size,
                       **{"median_" + key: statistics.median(items) if items else None
                          for key, items in values.items()},
                       "wape_defined_products": len(values["wape"]),
                       "wape_q1": quantile(values["wape"], 0.25),
                       "wape_q3": quantile(values["wape"], 0.75),
                       "mape_defined_products": len(values["mape"]),
                       "mape_product_coverage_pct": percentage(len(values["mape"]), size),
                       "mape_pair_coverage_pct": percentage(used, pairs),
                       "mape_pairs": used, "common_pairs": pairs,
                       "positive_bias_pct": percentage(sum(v > 0 for v in values["bias"]), size),
                       "negative_bias_pct": percentage(sum(v < 0 for v in values["bias"]), size),
                       "zero_bias_pct": percentage(sum(v == 0 for v in values["bias"]), size)})
    return result


def comparison(rows):
    grouped = defaultdict(list)
    for row in rows:
        if row["publishable"]:
            grouped[row["product_code"]].append(row)
    result = {}
    for metric in ("mae", "rmse", "wape"):
        leaders, events = Counter(), Counter()
        for product in grouped.values():
            values = {row["model"]: row[metric] for row in product}
            if any(value is None for value in values.values()):
                continue
            events["defined_products"] += 1
            methods = winners(product, metric)
            leaders.update(methods)
            events["tied_minimum_products"] += len(methods) > 1
            best = min(values[model] for model in BASELINES)
            improved = []
            for model in MODELS[4:]:
                same = tied(values[model], best)
                better = values[model] < best and not same
                improved.append(better)
                events[model + "_better"] += better
                events[model + "_tied_best_baseline"] += same
                events[model + "_worse"] += values[model] > best and not same
            events["neither_advanced_better"] += not any(improved)
            events["both_advanced_better"] += all(improved)
            events["any_advanced_tied_best_baseline"] += any(tied(values[m], best) for m in MODELS[4:])
        denominator = events["defined_products"]
        result[metric] = {"defined_products": denominator,
                          "lowest_error": {model: {"products": leaders[model],
                              "pct": percentage(leaders[model], denominator)} for model in MODELS},
                          "events": {key: {"products": value, "pct": percentage(value, denominator)}
                                     for key, value in events.items() if key != "defined_products"}}
    return result


class Coverage:
    """Original TRAIN60/12 denominator is independent of model-specific rejection."""
    def __init__(self):
        self.counts = {model: Counter() for model in MODELS}
        self.products = defaultdict(set)
        self.reasons = Counter()

    def add(self, row, common, train_constant=False):
        counts = self.counts[row.model]
        original = row.train_size >= 60 and row.positives_train >= 12
        counts["panel_opportunities"] += 1
        counts["original_eligible_opportunities"] += original
        counts["model_eligible_opportunities"] += row.train_eligible
        counts[row.status] += 1
        counts["eligible_" + row.status] += original
        counts["common_support_folds"] += common
        counts["eligible_constant_train"] += original and train_constant
        counts["constant_train_ineligible"] += original and train_constant and row.status == "INELIGIBLE"
        self.products[row.model, row.status].add(row.product_code)
        if row.reason:
            self.reasons[row.model, row.status, row.reason] += 1
            self.products[row.model, row.status, row.reason].add(row.product_code)

    def rows(self):
        result = []
        for model, counts in self.counts.items():
            row = {"model": model, **{name: counts[name] for name in (
                "panel_opportunities", "original_eligible_opportunities", "model_eligible_opportunities",
                "SUCCESS", "INELIGIBLE", "MODEL_UNAVAILABLE", "ERROR", "eligible_SUCCESS",
                "eligible_INELIGIBLE", "eligible_MODEL_UNAVAILABLE", "eligible_ERROR", "common_support_folds",
                "eligible_constant_train", "constant_train_ineligible")}}
            row["success_rate_original_eligible"] = (counts["eligible_SUCCESS"] /
                counts["original_eligible_opportunities"] if counts["original_eligible_opportunities"] else None)
            for status in ("SUCCESS", "INELIGIBLE", "MODEL_UNAVAILABLE", "ERROR"):
                row[status + "_products"] = len(self.products[model, status])
            result.append(row)
        return result

    def causes(self):
        return [{"model": model, "status": status, "reason": reason, "opportunities": count,
                 "products": len(self.products[model, status, reason])}
                for (model, status, reason), count in sorted(self.reasons.items())]


def summarize(rows, evaluations, coverage, empirical):
    first = [row for row in rows if row["model"] == MODELS[0]]
    distribution = Counter(row["valid_common_folds"] for row in first)
    publicable = [ev for ev in evaluations if ev.individual_comparison_publishable]
    pooled = lambda selected: {model: empirical.pooled_common_metrics(selected, model).to_dict()
                               for model in MODELS}
    strata = {}
    for label in STRATA:
        selected = [row for row in rows if row["stratum"] == label]
        codes = {row["product_code"] for row in selected if row["publishable"]}
        subset = [ev for ev in publicable if ev.rows[0].product_code in codes]
        strata[label] = {"cohort_products": len({row["product_code"] for row in selected}),
                         "publishable_products": len(codes), "macro": macro(selected),
                         "pooled_publishable": pooled(subset), "comparison": comparison(selected)}
    return {"products_processed": len(first), "products_ge_3_common_folds": sum(v for k, v in distribution.items() if k >= 3),
            "products_1_or_2_common_folds": sum(distribution[k] for k in (1, 2)),
            "products_0_common_folds": distribution[0], "common_fold_distribution": dict(sorted(distribution.items())),
            "common_product_origins": sum(k * v for k, v in distribution.items()),
            "publicable_common_product_origins": sum(k * v for k, v in distribution.items() if k >= 3),
            "coverage": coverage.rows(), "failure_and_ineligibility_causes": coverage.causes(),
            "macro_publishable": macro(rows), "pooled_publishable_same_pairs": pooled(publicable),
            "pooled_all_common_including_1_or_2_folds": pooled(evaluations),
            "descriptive_comparison": comparison(rows), "intermittency_strata": strata}
