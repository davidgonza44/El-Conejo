"""Independent stdlib-only readback audit; never imports or reruns forecasting code."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import time

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "artifacts/empirical_2026"
MODELS = ("naive", "mean", "moving_average", "linear_trend", "arima", "random_forest")
STATUSES = ("SUCCESS", "INELIGIBLE", "MODEL_UNAVAILABLE", "ERROR")
METRICS = ("mae", "rmse", "bias", "wape", "mape")
STRATA = ("zeros_lt_50pct", "zeros_50_to_lt_80pct", "zeros_80_to_lt_95pct", "zeros_ge_95pct")
EXPERIMENTS = {"strict30": ("STRICT", 30, 2087), "sensitivity7": ("SENSITIVITY", 7, 45),
               "sensitivity30": ("SENSITIVITY", 30, 45)}
STRICT_PREFIX = "empirical_2026_backtest_7d_strict_"
PINNED = {"manifest.json": "b305a68a69a306320d161f66865b1bf2b0890bcc1499f3ef804f35e7fa8efedb",
          "artifact_hashes.json": "f5fb56cbe04afaee0f08cddc6aade27cd54c68d1a61c134a0bdc8d91ede4672d"}


def require(condition, label):
    if not condition:
        raise AssertionError(label)  # Labels never contain product identities or source rows.


def sha(path):
    require(path.is_file() and not path.is_symlink(), "Missing file or symlink")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def document(path):
    return json.loads(path.read_text(encoding="utf-8"))


def records(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        yield from csv.DictReader(stream)


def equal(actual, expected, label="Mismatch"):
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and actual.keys() == expected.keys(), label + " keys")
        for key, value in expected.items():
            equal(actual[key], value, label)  # Do not expose sensitive dictionary keys.
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), label + " length")
        for left, right in zip(actual, expected):
            equal(left, right, label)
    elif isinstance(expected, float):
        require(isinstance(actual, (float, int)) and not isinstance(actual, bool)
                and math.isfinite(actual) and math.isfinite(expected)
                and math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-10), label + " number")
    else:
        require(type(actual) is type(expected) and actual == expected, label + " value")


def csv_equal(row, expected, label):
    require(row is not None and row.keys() == expected.keys(), label + " columns")
    for key, value in expected.items():
        text = row[key]
        if value is None:
            require(text == "", label + " NA")
        elif isinstance(value, bool):
            require(text == str(value), label + " boolean")
        elif isinstance(value, (int, float)):
            equal(type(value)(text), value, label + " " + key)
        else:
            require(text == value, label + " " + key)


def measure(pairs):
    n = len(pairs)
    errors = [p - a for a, p in pairs]
    absolute = math.fsum(abs(e) for e in errors)
    denominator = math.fsum(abs(a) for a, _ in pairs)
    apes = [abs(p - a) / abs(a) for a, p in pairs if a != 0]
    return {"n": n, "mae": absolute / n if n else None,
            "rmse": math.sqrt(math.fsum(e * e for e in errors) / n) if n else None,
            "bias": math.fsum(errors) / n if n else None,
            "wape": absolute / denominator * 100 if denominator else None,
            "mape": math.fsum(apes) / len(apes) * 100 if apes else None,
            "mape_n": len(apes), "mape_excluded": n - len(apes)}


def pct(n, total):
    return n / total * 100 if total else None


def tied(a, b):
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)


def leaders(rows, metric):
    defined = [row[metric] for row in rows if row[metric] is not None]
    return [row["model"] for row in rows if row[metric] is not None and tied(row[metric], min(defined))]


def quantile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] * (1 - position + lower) + ordered[upper] * (position - lower)


def macro(rows):
    result = []
    for model in MODELS:
        selected = [row for row in rows if row["model"] == model and row["publishable"]]
        values = {metric: [row[metric] for row in selected if row[metric] is not None] for metric in METRICS}
        n, pairs = len(selected), sum(row["n"] for row in selected)
        used = sum(row["mape_n"] for row in selected)
        result.append({"model": model, "products": n,
            **{"median_" + key: statistics.median(v) if v else None for key, v in values.items()},
            "wape_defined_products": len(values["wape"]), "wape_q1": quantile(values["wape"], .25),
            "wape_q3": quantile(values["wape"], .75), "mape_defined_products": len(values["mape"]),
            "mape_product_coverage_pct": pct(len(values["mape"]), n), "mape_pair_coverage_pct": pct(used, pairs),
            "mape_pairs": used, "common_pairs": pairs,
            **{label + "_bias_pct": pct(sum(test(v) for v in values["bias"]), n)
               for label, test in (("positive", lambda v: v > 0), ("negative", lambda v: v < 0),
                                   ("zero", lambda v: v == 0))}})
    return result


def comparisons(rows):
    products = defaultdict(dict)
    for row in rows:
        if row["publishable"]:
            products[row["product_code"]][row["model"]] = row
    result = {}
    for metric in ("mae", "rmse", "wape"):
        counts, events, denominator = Counter(), Counter(), 0
        for product in products.values():
            values = {m: product[m][metric] for m in MODELS}
            if any(v is None for v in values.values()):
                continue
            denominator += 1
            best_models = leaders(list(product.values()), metric)
            counts.update(best_models)
            events["tied_minimum_products"] += len(best_models) > 1
            baseline = min(values[m] for m in MODELS[:4])
            better = []
            for model in MODELS[4:]:
                same = tied(values[model], baseline)
                improves = values[model] < baseline and not same
                better.append(improves)
                events[model + "_better"] += improves
                events[model + "_tied_best_baseline"] += same
                events[model + "_worse"] += values[model] > baseline and not same
            events["neither_advanced_better"] += not any(better)
            events["both_advanced_better"] += all(better)
            events["any_advanced_tied_best_baseline"] += any(tied(values[m], baseline) for m in MODELS[4:])
        result[metric] = {"defined_products": denominator,
            "lowest_error": {m: {"products": counts[m], "pct": pct(counts[m], denominator)} for m in MODELS},
            "events": {key: {"products": n, "pct": pct(n, denominator)} for key, n in events.items()}}
    return result


def load_panel(cohort, size):
    calendar_rows = list(records(DATA / "empirical_2026_operational_calendar.csv"))
    calendar = [row["fecha_real"] for row in calendar_rows]
    require(len(calendar) == 216 and calendar == sorted(set(calendar)), "Calendar")
    require([int(r["operational_index"]) for r in calendar_rows] == list(range(1, 217)), "Calendar indices")
    metadata = {r["codigo_producto"]: r for r in records(DATA / "empirical_2026_cohort_manifest.csv")
                if r["cohorte"] == cohort}
    require(len(metadata) == size, "Cohort size")
    panel = {code: {"values": [], "dates": [], "service": r["is_service"] == "true"}
             for code, r in sorted(metadata.items())}
    for row in records(DATA / f"empirical_2026_series_{cohort.lower()}.csv"):
        code, value = row["codigo_producto"], float(row["demanda_observada"])
        require(code in panel and row["cohorte"] == cohort, "Series cohort")
        point = panel[code]
        require(math.isfinite(value) and value >= 0, "Observed quantity")
        require(row["is_service"] == metadata[code]["is_service"], "Service metadata")
        require(int(row["product_operational_index"]) == len(point["values"]) + 1, "Product index")
        require(calendar[int(row["operational_index"]) - 1] == row["fecha_real"], "Global index")
        require(row["es_cero_observado"] == str(value == 0).lower(), "Observed zero flag")
        point["values"].append(value)
        point["dates"].append(row["fecha_real"])
    for point in panel.values():
        require(point["values"] and point["values"][0] > 0, "Positive observed suffix start")
        point["offset"] = calendar.index(point["dates"][0])
        require(point["dates"] == calendar[point["offset"]:], "Contiguous left-censored suffix")
    return calendar, panel


def inspect_identity(prefix, source_dir, manifest, summary):
    for suffix, expected in PINNED.items():
        require(sha(DATA / (STRICT_PREFIX + suffix)) == expected, "Frozen STRICT7 authority")
    old = document(DATA / (STRICT_PREFIX + "manifest.json"))
    equal(manifest["input_sha256_before"], old["input_sha256_before"], "Frozen inputs")
    require(set(manifest["code_sha256_before"]) == set(old["code_sha256_before"]) | {
        "scripts/run_empirical_2026_final_batch.py", "scripts/test_empirical_2026_final_batch.py"}, "Exact source set")
    for name, expected in old["code_sha256_before"].items():
        require(manifest["code_sha256_before"].get(name) == expected, "Frozen source manifest")
    require(document(DATA / "strict7_independent_verification.json")["status"] == "PASS", "Original audit")
    frozen_paths = list(DATA.glob(STRICT_PREFIX + "*")) + list(DATA.glob("strict7_*"))
    frozen_paths += list((DATA / "smoke_7d_strict").glob(STRICT_PREFIX + "*"))
    frozen_names = {p.relative_to(ROOT).as_posix() for p in frozen_paths} | set(old["code_sha256_before"])
    require(set(manifest["frozen_strict7_sha256_before"]) == frozen_names, "Complete frozen STRICT7 inventory")
    paths = {Path(__file__): sha(Path(__file__))}
    for kind in ("input", "code", "frozen_strict7"):
        before, after = manifest[kind + "_sha256_before"], summary[kind + "_sha256_after"]
        equal(before, after, kind + " before/after")
        for name, expected in before.items():
            base = (DATA if name.startswith("empirical_2026_") else source_dir) if kind == "input" else ROOT
            path = base / name
            require(path.resolve().is_relative_to(base.resolve()), "Identity path containment")
            require(sha(path) == expected, kind + " current identity")
            paths[path] = expected
    old_outputs = document(DATA / (STRICT_PREFIX + "artifact_hashes.json"))["sha256"]
    for name, expected in old_outputs.items():
        require(sha(DATA / name) == expected, "Original artifact identity")
    hashes_path = DATA / (prefix + "artifact_hashes.json")
    hashes = document(hashes_path)["sha256"]
    suffixes = ("manifest.json", "summary.json", "folds.csv", "predictions.csv", "failures.csv",
                "warnings.csv", "product_metrics.csv", "coverage.csv")
    require(set(hashes) == {prefix + suffix for suffix in suffixes}, "Exact artifact set")
    for name, expected in hashes.items():
        require(sha(DATA / name) == expected, "Artifact hash")
        paths[DATA / name] = expected
    hashes[hashes_path.name] = sha(hashes_path)
    paths[hashes_path] = hashes[hashes_path.name]
    return old, paths, hashes


def inspect_fold(row, code, point, origin, horizon, calendar):
    train = point["values"][:max(0, origin - point["offset"])]
    actual = point["values"][origin - point["offset"]:origin - point["offset"] + horizon] if origin >= point["offset"] else []
    positives, constant = sum(v > 0 for v in train), bool(train) and min(train) == max(train)
    original = len(train) >= 60 and positives >= 12
    model_eligible = original and not (row["model"] == "arima" and constant)
    expected = {"product_code": code, "origin": origin, "origin_date": calendar[origin - 1],
        "train_start": point["dates"][0] if train else None, "train_size": len(train),
        "positive_train_days": positives, "original_train_eligible": original,
        "model_train_eligible": model_eligible, "train_constant": constant, "horizon": horizon,
        "all_zero_observed_test": bool(actual) and not any(actual)}
    csv_equal({k: row[k] for k in expected}, expected, "Fold structure")
    equal(json.loads(row["target_dates"]), calendar[origin:origin + horizon], "Target dates")
    equal(json.loads(row["actual"]), actual, "Observed targets")
    status, reason = row["status"], row["reason"]
    require(status in STATUSES, "Known status")
    rejected = ("insufficient_train_observations" if len(train) < 60 else
                "insufficient_train_positive_observations" if positives < 12 else
                "model_train_requirements" if not model_eligible else None)
    if rejected:
        require(status == "INELIGIBLE" and reason == rejected, "TRAIN-only rejection")
    else:
        allowed = {"SUCCESS": ("",), "MODEL_UNAVAILABLE": ("model_unavailable",),
                   "ERROR": ("invalid_prediction_values", "nonfinite_metrics", "model_execution_error")}
        require(status in allowed and reason in allowed[status], "Eligible status and reason")
    predicted = json.loads(row["predicted"])
    if status == "SUCCESS":
        require(len(actual) == horizon and len(predicted) == horizon, "Complete successful targets")
        require(all(math.isfinite(v) and v >= 0 for v in predicted), "Finite nonnegative forecast")
        measured = measure(list(zip(actual, predicted)))
        csv_equal({k: row[k] for k in (*METRICS, "mape_n", "mape_excluded")},
                  {k: measured[k] for k in (*METRICS, "mape_n", "mape_excluded")}, "Fold metric")
    else:
        require(predicted == [] and all(row[k] == "" for k in (*METRICS, "mape_n", "mape_excluded")),
                "Unsuccessful prediction and metric NA")
    return actual, predicted, original, model_eligible, constant


def inspect_streams(prefix, calendar, panel, horizon):
    streams = {suffix: iter(records(DATA / (prefix + suffix + ".csv")))
               for suffix in ("folds", "predictions", "failures", "product_metrics")}
    counts, products, causes = defaultdict(Counter), defaultdict(set), Counter()
    product_rows, common_pairs, totals, zeros = [], {}, Counter(), Counter()
    for code, point in panel.items():
        pairs, common, zero_origins = {m: [] for m in MODELS}, 0, []
        for origin in range(60, 217 - horizon, horizon):
            group = [next(streams["folds"], None) for _ in MODELS]
            require(all(row is not None for row in group), "Missing fold rows")
            require([r["model"] for r in group] == list(MODELS), "Complete ordered model registry")
            shared = all(row["status"] == "SUCCESS" for row in group)
            common += shared
            for row in group:
                model, status = row["model"], row["status"]
                actual, predicted, original, eligible, constant = inspect_fold(row, code, point, origin, horizon, calendar)
                require(row["common_support"] == str(shared), "Common SUCCESS intersection")
                if shared:
                    pairs[model].extend(zip(actual, predicted))
                count = counts[model]
                count.update({"panel_opportunities": 1, "original_eligible_opportunities": original,
                    "model_eligible_opportunities": eligible, status: 1, "eligible_" + status: original,
                    "common_support_folds": shared, "eligible_constant_train": original and constant,
                    "constant_train_ineligible": original and constant and status == "INELIGIBLE"})
                products[model, status].add(code)
                if row["reason"]:
                    key = model, status, row["reason"]
                    causes[key] += 1
                    products[key].add(code)
                if status != "SUCCESS":
                    equal(next(streams["failures"], None), row, "Failure ledger")
                    totals["failure_and_ineligible_rows"] += 1
                for index, target in enumerate(calendar[origin:origin + horizon]):
                    expected = {"product_code": code, "origin": origin, "origin_date": calendar[origin - 1],
                        "model": model, "target_index": index + 1, "target_date": target,
                        "actual": actual[index] if actual else None,
                        "predicted": predicted[index] if predicted else None,
                        "status": status, "reason": row["reason"], "common_support": shared}
                    csv_equal(next(streams["predictions"], None), expected, "Prediction pair")
                    totals["prediction_rows"] += 1
                totals["fold_rows"] += 1
                if model == MODELS[0]:
                    totals["left_censored_test_product_origins"] += not actual
                    if actual and not any(actual):
                        zeros["observed_product_origins"] += 1
                        zeros["original_eligible_product_origins"] += original
                        zeros["common_product_origins"] += shared
                        zero_origins.append(shared)
        zeros["publicable_common_product_origins"] += sum(zero_origins) if common >= 3 else 0
        positive = sum(v > 0 for v in point["values"])
        size = len(point["values"])
        zero_fraction = (size - positive) / size
        rows = [{"product_code": code, "model": model, "is_service": point["service"],
                 "valid_common_folds": common, "publishable": common >= 3, "observed_days": size,
                 "positive_days": positive, "zero_proportion": zero_fraction, "adi_observed": size / positive,
                 "stratum": STRATA[sum(zero_fraction >= edge for edge in (.5, .8, .95))], **measure(pairs[model])}
                for model in MODELS]
        for metric in ("mae", "rmse", "wape"):
            best = leaders(rows, metric) if common >= 3 else []
            for row in rows:
                row["lowest_" + metric] = row["model"] in best
                row["lowest_" + metric + "_tie_size"] = len(best)
        for row in rows:
            csv_equal(next(streams["product_metrics"], None), row, "Product common metrics and descriptors")
        product_rows.extend(rows)
        common_pairs[code] = pairs
    for stream in streams.values():
        require(next(stream, None) is None, "Unexpected additional CSV row")
    fields = ("panel_opportunities", "original_eligible_opportunities", "model_eligible_opportunities",
              *STATUSES, *("eligible_" + s for s in STATUSES), "common_support_folds",
              "eligible_constant_train", "constant_train_ineligible")
    coverage = [{"model": m, **{key: counts[m][key] for key in fields},
                "success_rate_original_eligible": counts[m]["eligible_SUCCESS"] / counts[m]["original_eligible_opportunities"]
                    if counts[m]["original_eligible_opportunities"] else None,
                **{s + "_products": len(products[m, s]) for s in STATUSES}} for m in MODELS]
    causes = [{"model": m, "status": s, "reason": r, "opportunities": n, "products": len(products[m, s, r])}
              for (m, s, r), n in sorted(causes.items())]
    zero_keys = ("observed_product_origins", "original_eligible_product_origins", "common_product_origins",
                 "publicable_common_product_origins")
    return product_rows, common_pairs, coverage, causes, totals, {key: zeros[key] for key in zero_keys}


def aggregates(rows, pairs):
    first = [row for row in rows if row["model"] == MODELS[0]]
    distribution = Counter(row["valid_common_folds"] for row in first)
    publicable = {row["product_code"] for row in first if row["publishable"]}
    def pooled(codes):
        return {m: measure([pair for code in pairs if code in codes for pair in pairs[code][m]]) for m in MODELS}
    strata = {}
    for label in STRATA:
        selected = [row for row in rows if row["stratum"] == label]
        codes = {row["product_code"] for row in selected}
        strata[label] = {"cohort_products": len(codes), "publishable_products": len(codes & publicable),
                        "macro": macro(selected), "pooled_publishable": pooled(codes & publicable),
                        "comparison": comparisons(selected)}
    return {"products_processed": len(first), "products_ge_3_common_folds": len(publicable),
        "products_1_or_2_common_folds": distribution[1] + distribution[2], "products_0_common_folds": distribution[0],
        "common_fold_distribution": {str(k): v for k, v in sorted(distribution.items())},
        "common_product_origins": sum(row["valid_common_folds"] for row in first),
        "publicable_common_product_origins": sum(row["valid_common_folds"] for row in first if row["publishable"]),
        "macro_publishable": macro(rows), "pooled_publishable_same_pairs": pooled(publicable),
        "pooled_all_common_including_1_or_2_folds": pooled(set(pairs)),
        "descriptive_comparison": comparisons(rows), "intermittency_strata": strata}


def audit(experiment, source_dir):
    started = time.perf_counter()
    cohort, horizon, size = EXPERIMENTS[experiment]
    prefix = f"empirical_2026_backtest_{horizon}d_{cohort.lower()}_"
    output = DATA / (prefix + "independent_verification.json")
    require(not output.exists(), "Audit already exists; never overwrite")
    manifest, summary = [document(DATA / (prefix + s + ".json")) for s in ("manifest", "summary")]
    require(manifest["status"] == "STARTED" and summary["status"] == "COMPLETE", "Completed experiment required")
    require(not (DATA / (prefix + "diagnostic.json")).exists(), "Experimental diagnostic present")
    old, paths, output_hashes = inspect_identity(prefix, source_dir, manifest, summary)
    expected_config = dict(old["configuration"])
    expected_config.update(cohort=cohort, horizon=horizon, step=horizon, smoke_selection=None,
        input_series=f"empirical_2026_series_{cohort.lower()}.csv", cohort_products=size,
        limitations=old["configuration"]["limitations"][:-2] + [
            "This run evaluates only its declared cohort and horizon; cohorts are never pooled.",
            f"Complete shared tests leave {(216 - 60) % horizon} final operational dates untested.",
            "No tuning, product selection, replenishment or purchasing is performed."])
    equal(manifest["configuration"], expected_config, "Frozen protocol configuration")
    equal(summary["configuration"], expected_config, "Summary protocol configuration")
    for key in ("versions", "python", "platform"):
        equal(manifest[key], old[key], "Frozen environment")
    origins = list(range(60, 217 - horizon, horizon))
    equal(manifest["shared_origins"], origins, "Nonoverlapping expanding origin grid")
    for key, n in (("selected_products", size), ("selected_product_origins", size * len(origins))):
        equal(manifest[key], n, "Complete selected cohort")
    for key, n in (("generated_product_origins", size * len(origins)), ("generated_model_cells", size * len(origins) * 6)):
        equal(summary[key], n, "Complete generated grid")
    require(math.isfinite(summary["elapsed_seconds"]) and summary["elapsed_seconds"] > 0, "Elapsed time")
    require(datetime.fromisoformat(summary["finished_utc"]) >= datetime.fromisoformat(manifest["started_utc"]), "Run timestamps")
    calendar, panel = load_panel(cohort, size)
    rows, pairs, coverage, causes, totals, zeros = inspect_streams(prefix, calendar, panel, horizon)
    for observed, expected in zip(records(DATA / (prefix + "coverage.csv")), coverage, strict=True):
        csv_equal(observed, expected, "Coverage CSV")
    equal(summary["coverage"], coverage, "Coverage JSON")
    equal(summary["failure_and_ineligibility_causes"], causes, "Failure causes")
    equal(summary["all_zero_tests"], zeros, "All-zero tests")
    recomputed = aggregates(rows, pairs)
    for key, value in recomputed.items():
        equal(summary[key], value, "Summary " + key)
    warning_counts, seen_warnings = Counter(), set()
    for row in records(DATA / (prefix + "warnings.csv")):
        key = row["product_code"], row["category"], row["message"]
        require(row["product_code"] in panel and key not in seen_warnings and int(row["count"]) > 0, "Warning ledger")
        seen_warnings.add(key)
        warning_counts[row["category"] + ": " + row["message"]] += int(row["count"])
    equal(summary["warnings"], dict(warning_counts), "Warnings summary")
    for path, expected in paths.items():
        require(sha(path) == expected, "Identity changed during audit")
    checks = ("all_train_only_eligibility", "model_requirements", "shared_origin_target_actual", "every_prediction_pair",
              "common_support", "fold_metrics_and_NA", "concatenated_product_metrics", "macro_medians_quartiles",
              "pooled_identical_common_pairs", "descriptive_minima_ties_and_baseline_comparison",
              "intermittency_descriptors_and_aggregates", "status_denominators", "failure_ledger", "warning_ledger",
              "all_zero_and_left_censored_tests", "all_output_hashes", "original_derived_source_identity_before_after",
              "frozen_STRICT7_preserved", "frozen_configuration_and_environment", "elapsed_time_and_timestamps")
    result = {"status": "PASS", "experiment": experiment, "verification": "Independent stdlib streamed readback; no model reruns or application imports",
        "checks": {check: True for check in checks}, "products": size, **totals, "product_metric_rows": len(rows),
        "common_product_origins": recomputed["common_product_origins"], "all_zero_tests": zeros,
        "common_pairs_per_model": recomputed["common_product_origins"] * horizon,
        "publicable_common_pairs_per_model": recomputed["publicable_common_product_origins"] * horizon,
        "products_ge_3_common_folds": recomputed["products_ge_3_common_folds"], "warning_rows": len(seen_warnings),
        "output_sha256": output_hashes, "audit_source_sha256": sha(Path(__file__)),
        "elapsed_seconds": time.perf_counter() - started, "tolerance": {"relative": 1e-9, "absolute": 1e-10}}
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"experiment": experiment, "status": "PASS", "fold_rows": totals["fold_rows"],
                      "prediction_rows": totals["prediction_rows"], "audit_sha256": sha(output)}), flush=True)


def self_test():
    equal(measure([]), dict(n=0, mae=None, rmse=None, bias=None, wape=None, mape=None, mape_n=0, mape_excluded=0))
    equal(measure([(0, 2), (2, 0)]), dict(n=2, mae=2.0, rmse=2.0, bias=0.0, wape=200.0, mape=100.0, mape_n=1, mape_excluded=1))
    require(measure([(0, 1)])["wape"] is None and measure([(0, 1)])["mape"] is None, "Zero denominator self-check")
    equal(quantile([0, 10, 20, 30], .25), 7.5)
    equal(quantile([0, 10, 20, 30], .75), 22.5)
    require(leaders([{"model": "a", "mae": 1.0}, {"model": "b", "mae": 1 + 1e-10}], "mae") == ["a", "b"], "Tie self-check")
    require(leaders([{"model": "a", "wape": None}], "wape") == [], "Undefined leaders self-check")
    print("Independent metric/NA/type-7/tie self-checks PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--experiment", choices=EXPERIMENTS)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        require(args.source_dir is not None, "--source-dir is required")
        for selected in ([args.experiment] if args.experiment else EXPERIMENTS):
            audit(selected, args.source_dir)
