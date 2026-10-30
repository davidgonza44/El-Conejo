"""Bounded, read-only estimator probes inside the validated DEMO 5001 container.

This complements the full before/after benchmark, not an accuracy experiment.
At most three eligible series are selected deterministically by length/density.
Each has its full TRAIN and one common historical TRAIN ending before H90;
every fitted estimator forecasts H7/H30/H90 without fitting again. This isolates
fit options while retaining the six production ARIMA orders, causal features,
RF parameters and train-only metric scaling. No application cache is changed.
Evidence is written only to a new JSON file beneath container /tmp.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import timedelta
import json
import math
import os
from pathlib import Path
import time
import warnings

from threadpoolctl import threadpool_info, threadpool_limits

from docker_support.benchmark_predictions import Profile, _machine, _materialize
from docker_support.guard import validate_demo


def _horizons(series):
    from app.services import forecast_service as forecast

    return {days: math.ceil(days / series.period_days) for days in forecast.HORIZONS_DAYS}


def _representatives(series_list, limit):
    from app.services import forecast_advanced_service as advanced

    candidates = []
    for series in series_list:
        origin = series.periods - max(_horizons(series).values())
        eligible = origin >= series.periods // 2 and all(
            advanced.eligible(series.values[:origin], model, series.granularity,
                              series.starts[:origin]) for model in advanced.MODEL_REQUIREMENTS)
        if eligible:
            candidates.append(series)
    candidates.sort(key=lambda s: (s.periods, sum(v > 0 for v in s.values) / s.periods, s.product_id))
    if len(candidates) <= limit:
        return candidates, len(candidates)
    indices = [len(candidates) // 2] if limit == 1 else [0, len(candidates) - 1]
    if limit == 3:
        indices.insert(1, len(candidates) // 2)
    return [candidates[index] for index in indices], len(candidates)


def _forecast_rows(series, origin, fitted, predict):
    from app.services import experimental_2025_models as experimental

    rows = []
    for horizon, periods in _horizons(series).items():
        began = time.perf_counter()
        path = predict(fitted, periods)
        row = {"horizon": horizon, "periods": periods, "path": path,
               "predict_seconds": time.perf_counter() - began, "metrics": None}
        if origin < series.periods:
            row["metrics"] = experimental.score_forecast(
                series.values[:origin], series.values[origin:origin + periods], path,
                training_dates=series.starts[:origin])
        rows.append(row)
    longest = rows[-1]["path"]
    for row in rows:
        row["prefix_max_abs_delta"] = max(
            (abs(a - b) for a, b in zip(row["path"], longest)), default=0.0)
    return rows


def _arima_case(series, origin, low_memory, maxiter):
    from app.services import forecast_advanced_service as advanced
    from statsmodels.tools.sm_exceptions import ConvergenceWarning

    started, cpu = time.perf_counter(), time.process_time()
    candidates, best_fit, best_aic, best_order = [], None, math.inf, None
    for order in advanced.ARIMA_ORDERS:
        began = time.perf_counter()
        candidate = {"order": list(order), "aic": None, "converged": False,
                     "iterations": None, "error": None}
        with warnings.catch_warnings(record=True) as notices:
            warnings.simplefilter("always")
            warnings.simplefilter("error", ConvergenceWarning)
            try:
                fitted = advanced.ARIMA(list(series.values[:origin]), order=order).fit(
                    low_memory=low_memory, method_kwargs={"maxiter": maxiter})
                aic = float(fitted.aic)
                converged = bool(fitted.mle_retvals.get("converged", False))
                candidate.update(aic=aic if math.isfinite(aic) else None, converged=converged,
                                 iterations=fitted.mle_retvals.get("iterations"))
                if converged and math.isfinite(aic) and aic < best_aic:
                    best_fit, best_aic, best_order = fitted, aic, list(order)
            except (*advanced.NUMERICAL_ERRORS, ConvergenceWarning) as error:
                candidate["error"] = type(error).__name__
        candidate.update(seconds=time.perf_counter() - began,
                         warnings=dict(Counter(type(w.message).__name__ for w in notices)))
        candidates.append(candidate)
    result = {"method": "arima", "low_memory": low_memory, "maxiter": maxiter,
              "fit_seconds": time.perf_counter() - started, "fits": len(candidates),
              "candidates": candidates, "order": best_order,
              "aic": best_aic if best_fit is not None else None,
              "available": best_fit is not None, "forecasts": []}
    if best_fit is not None:
        try:
            result["forecasts"] = _forecast_rows(series, origin, best_fit, lambda estimator, periods:
                advanced._validated_path(estimator.forecast(steps=periods), periods))
        except (*advanced.NUMERICAL_ERRORS, advanced.ModelUnavailable) as error:
            result.update(available=False, forecast_error=type(error).__name__)
    result["seconds"] = time.perf_counter() - started
    result["cpu_seconds"] = time.process_time() - cpu
    return result


def _rf_case(series, origin, n_jobs, features, targets):
    from app.services import forecast_advanced_service as advanced

    began, cpu = time.perf_counter(), time.process_time()
    estimator = advanced.RandomForestRegressor(
        n_estimators=50, max_depth=6, random_state=42, n_jobs=n_jobs)
    estimator.fit(features, targets)
    fit_seconds = time.perf_counter() - began

    def predict(fitted, periods):
        simulated = list(series.values[:origin])
        for step in range(1, periods + 1):
            day = series.starts[origin - 1] + timedelta(days=series.period_days * step)
            row = advanced.rf_features(simulated, day, series.granularity)
            simulated.append(advanced._validated_path(fitted.predict([row]), 1)[0])
        return simulated[origin:]

    rows = _forecast_rows(series, origin, estimator, predict)
    return {"method": "random_forest", "n_jobs": n_jobs, "fit_seconds": fit_seconds,
            "fits": 1, "available": True, "forecasts": rows,
            "seconds": time.perf_counter() - began, "cpu_seconds": time.process_time() - cpu}


def _delta(reference, candidate):
    if reference is None or candidate is None:
        return None if reference == candidate else "availability_changed"
    return candidate - reference


def _compare(records):
    """Compare alternatives against existing defaults, preserving unavailable states."""
    baselines = {}
    for row in records:
        default = (row["method"] == "arima" and row["thread_limit"] == 8
                   and not row["low_memory"] and row["maxiter"] == 50
                   or row["method"] == "random_forest" and row["n_jobs"] == 1)
        if default:
            baselines[(row["product_id"], row["origin"], row["method"])] = row
    comparisons = []
    for row in records:
        base = baselines[(row["product_id"], row["origin"], row["method"])]
        comparison = {key: row[key] for key in ("product_id", "origin", "fit_kind", "method")}
        comparison.update(option={key: row[key] for key in (
                              "low_memory", "maxiter", "thread_limit", "n_jobs") if key in row},
                          speedup=base["seconds"] / row["seconds"],
                          available_same=base["available"] == row["available"],
                          order_same=base.get("order") == row.get("order"),
                          aic_delta=_delta(base.get("aic"), row.get("aic")), horizons=[])
        base_horizons = {r["horizon"]: r for r in base["forecasts"]}
        for forecast in row["forecasts"]:
            previous = base_horizons.get(forecast["horizon"])
            if previous is None:
                continue
            metric_delta = {key: _delta(previous["metrics"][key], value)
                            for key, value in (forecast["metrics"] or {}).items()
                            if value is None or isinstance(value, (int, float))}
            comparison["horizons"].append({"horizon": forecast["horizon"],
                "path_max_abs_delta": max((abs(a - b) for a, b in zip(
                    previous["path"], forecast["path"])), default=0.0), "metric_delta": metric_delta})
        comparisons.append(comparison)
    return comparisons


def _save(output, report):
    report["comparisons"] = _compare(report["records"])
    report["machine"] = _machine()
    output.write_text(json.dumps(report, allow_nan=False, indent=2), encoding="utf-8")


def run(args):
    validate_demo()
    output = Path(args.output).resolve()
    if os.name != "posix" or not output.is_relative_to(Path("/tmp")) or output.suffix != ".json":
        raise ValueError("Output must be a JSON file strictly beneath container /tmp")
    if output.exists():
        raise ValueError("Output already exists; refusing to replace benchmark evidence")
    all_series, coverage = _materialize(Profile(), 0)
    selected, count = _representatives(all_series, args.products)
    from app.services import forecast_advanced_service as advanced

    report = {"purpose": "bounded estimator options probe; not full-pipeline benchmark",
              "coverage": coverage, "jointly_eligible_with_H90_fold": count,
              "measured_products": len(selected), "selected_product_ids": [s.product_id for s in selected],
              "selection": "length, then positive density, then ID; extremes and/or median",
              "fold_policy": "one common origin ending before longest holdout, shared by all horizons",
              "repetitions": 1, "arima_orders": list(advanced.ARIMA_ORDERS),
              "rf_parameters": {"n_estimators": 50, "max_depth": 6, "random_state": 42},
              "timing_policy": "sequential options; feature preparation shared and separately measured; "
                               "ARIMA explicit BLAS limits 8/1/2/4; low_memory probe at 1; RF BLAS limit 1",
              "threadpools": [{key: item.get(key) for key in (
                  "user_api", "internal_api", "prefix", "num_threads", "version", "threading_layer",
                  "architecture")} for item in threadpool_info()],
              "features": [], "records": [], "complete": False}
    started = time.perf_counter()
    try:
        if not selected:
            raise ValueError("No jointly eligible DEMO series with a complete H90 holdout")
        for series in selected:
            fold_origin = series.periods - max(_horizons(series).values())
            for kind, origin in (("final", series.periods), ("fold", fold_origin)):
                labels = {"product_id": series.product_id, "origin": origin, "fit_kind": kind}
                # Defaults go first so partial evidence always has its comparison baseline.
                options = ((8, False, 50), (1, False, 50), (2, False, 50),
                           (4, False, 50), (1, True, 50))
                for thread_limit, low_memory, maxiter in options:
                    with threadpool_limits(limits=thread_limit):
                        row = _arima_case(series, origin, low_memory, maxiter)
                        row["thread_counts"] = {item["prefix"]: item["num_threads"]
                                                for item in threadpool_info()}
                    row["thread_limit"] = thread_limit
                    report["records"].append({**labels, **row})
                    _save(output, report)
                    print(json.dumps({**labels, "method": "arima", "low_memory": low_memory,
                                      "maxiter": maxiter, "thread_limit": thread_limit,
                                      "seconds": row["seconds"]}), flush=True)
                began = time.perf_counter()
                features, targets = advanced.supervised_rows(
                    series.values[:origin], series.starts[:origin], series.granularity)
                report["features"].append({**labels, "seconds": time.perf_counter() - began,
                                           "rows": len(features)})
                for n_jobs in (1, 2, 4, -1):
                    with threadpool_limits(limits=1):
                        row = _rf_case(series, origin, n_jobs, features, targets)
                        row["thread_counts"] = {item["prefix"]: item["num_threads"]
                                                for item in threadpool_info()}
                    report["records"].append({**labels, **row})
                    _save(output, report)
                    print(json.dumps({**labels, "method": "random_forest", "n_jobs": n_jobs,
                                      "seconds": row["seconds"]}), flush=True)
        report["complete"] = True
    finally:
        report["seconds_total"] = time.perf_counter() - started
        _save(output, report)
    print(json.dumps({"complete": True, "records": len(report["records"]),
                      "seconds_total": report["seconds_total"]}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--products", type=int, choices=(1, 2, 3), default=2)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
