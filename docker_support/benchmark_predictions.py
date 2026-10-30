"""Read-only DEMO 5001 benchmark; outputs synthetic numeric evidence under /tmp.

Run inside the already validated DEMO app container, never against a host database.
No empirical runner, sealed result, user account or business write is involved.
Cold/hot/second/persisted refer to isolated benchmark prediction caches, not HTTP.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager, nullcontext
from functools import wraps
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import statistics
import tempfile
import time
import warnings

from docker_support.guard import validate_demo, validate_demo_url


class Profile:
    """Temporary instrumentation; never alter estimator arguments or warning policy."""

    def __init__(self):
        self.context = {}
        self.events = []
        self.restores = []

    @contextmanager
    def scope(self, **labels):
        previous = self.context
        self.context = {**previous, **labels}
        try:
            yield
        finally:
            self.context = previous

    def measure(self, stage, function, *args, **kwargs):
        wall, cpu = time.perf_counter(), time.process_time()
        error, notices = None, []
        labels = dict(self.context)
        try:
            with warnings.catch_warnings(record=True) as notices:
                return function(*args, **kwargs)
        except BaseException as caught:
            error = type(caught).__name__
            raise
        finally:
            self.events.append({**labels, "stage": stage,
                                "seconds": time.perf_counter() - wall,
                                "cpu_seconds": time.process_time() - cpu,
                                "error": error,
                                "warnings": dict(Counter(type(w.message).__name__ for w in notices))})

    def patch(self, target, name, stage, labeler=None):
        original = getattr(target, name)

        @wraps(original)
        def measured(*args, **kwargs):
            labels = labeler(args, kwargs) if labeler else {}
            with self.scope(**labels):
                return self.measure(stage, original, *args, **kwargs)

        self.restores.append((target, name, original))
        setattr(target, name, measured)

    def install(self):
        from app.services import forecast_advanced_service as advanced
        from app.services import forecast_service as forecast
        from app.services import predictive_cache_service as cache
        from app.services import predictive_metrics_service as metrics
        from app.services import experimental_2025_models as experimental
        from statsmodels.tsa.arima.model import ARIMAResults

        def prediction_labels(args, _kwargs):
            return {"method": args[2], "origin": len(args[0]),
                    "fit_kind": "final" if len(args[0]) == self.context.get("periods") else "fold"}

        def experimental_labels(args, _kwargs):
            return {"method": args[0], "origin": len(args[1]),
                    "fit_kind": "final" if len(args[1]) == self.context.get("periods") else "fold"}

        self.patch(forecast, "predict", "fit_predict", prediction_labels)
        self.patch(advanced.ARIMA, "fit", "fit", lambda args, _: {"order": list(args[0].order)})
        self.patch(ARIMAResults, "forecast", "predict")
        self.patch(advanced.RandomForestRegressor, "fit", "fit")
        self.patch(advanced.RandomForestRegressor, "predict", "predict")
        self.patch(advanced, "supervised_rows", "features")
        self.patch(advanced, "rf_features", "feature_row")
        self.patch(metrics, "compute", "metrics")
        self.patch(experimental, "score_forecast", "scaled_metrics")
        self.patch(experimental, "forecast_candidate", "experimental_candidate", experimental_labels)
        self.patch(experimental, "_point", "experimental_point", experimental_labels)
        self.patch(cache._Disk, "read", "cache_read")
        self.patch(cache._Disk, "write", "cache_write")
        if hasattr(cache._Disk, "write_many"):
            self.patch(cache._Disk, "write_many", "cache_transaction", lambda args, _: {
                "rows": len(args[1]), "bytes": sum(len(json.dumps(v)) for v in args[1].values())})

    def restore(self):
        for target, name, original in reversed(self.restores):
            setattr(target, name, original)

    def summary(self):
        grouped = defaultdict(list)
        for event in self.events:
            key = tuple(event.get(k) for k in ("implementation", "method", "pass", "stage", "fit_kind"))
            grouped[key].append(event)
        result = []
        for key, events in grouped.items():
            slowest = max(events, key=lambda e: e["seconds"])
            result.append({**dict(zip(("implementation", "method", "pass", "stage", "fit_kind"), key)),
                           "calls": len(events), "seconds_total": sum(e["seconds"] for e in events),
                           "seconds_min": min(e["seconds"] for e in events),
                           "seconds_mean": statistics.mean(e["seconds"] for e in events),
                           "seconds_max": slowest["seconds"],
                           "slowest": {k: slowest.get(k) for k in ("product_id", "horizon", "origin", "order")},
                           "cpu_seconds": sum(e["cpu_seconds"] for e in events),
                           "errors": dict(Counter(e["error"] for e in events if e["error"])),
                           "warnings": dict(sum((Counter(e["warnings"]) for e in events), Counter()))})
        return result


def _materialize(profile, product_limit, as_of=None):
    """Validate configuration before app creation and target again before connecting."""
    validate_demo()
    from sqlalchemy.engine import make_url
    from app.config import Config

    validate_demo_url(make_url(Config.SQLALCHEMY_DATABASE_URI))
    from app import create_app
    from app.extensions import db
    from app.models import Product
    from app.services import predictive_data_service as data
    from docker_support.demo import require_ready
    from sqlalchemy import event

    app = create_app()
    query_count = [0]

    def count_query(*_args):
        query_count[0] += 1

    with app.app_context():
        validate_demo_url(db.engine.url)
        with db.engine.connect() as connection:
            require_ready(connection)
        event.listen(db.engine, "before_cursor_execute", count_query)
        try:
            series_map = profile.measure("series_preparation", data.load_series, today=as_of)
            products = profile.measure("products_read", lambda: Product.query.with_entities(
                Product.id, Product.is_active).order_by(Product.id).all())
        finally:
            event.remove(db.engine, "before_cursor_execute", count_query)
            db.session.remove()
    statuses, selected = Counter(), []
    for product in products:
        series = series_map.get(product.id)
        status = data.classify_eligibility(series, bool(product.is_active)).status if series else data.NO_DEMAND
        statuses[status] += 1
        if status == data.ELIGIBLE:
            selected.append(series)
    eligible = len(selected)
    if product_limit:
        selected = selected[:product_limit]
    fingerprint = hashlib.sha256(json.dumps([(s.product_id, s.values,
        [d.isoformat() for d in s.starts]) for s in selected], separators=(",", ":")).encode()).hexdigest()
    return selected, {"catalog_products": len(products), "eligible_products": eligible,
                      "measured_products": len(selected), "eligibility_counts": dict(statuses),
                      "limited_explicitly": bool(product_limit), "data_fingerprint": fingerprint,
                      "db_queries_materialization": query_count[0]}


def _score_folds(series, result):
    from app.services import experimental_2025_models as experimental

    rows = []
    for fold in result.folds:
        score = experimental.score_forecast(series.values[:fold.origin], fold.actual, fold.predicted,
                                           training_dates=series.starts[:fold.origin])
        rows.append({"origin": fold.origin, "predicted": fold.predicted, "metrics": score})
    return {"metrics": result.metrics.to_dict(), "folds": rows,
            "mase_macro": statistics.mean(r["metrics"]["mase"] for r in rows
                                          if r["metrics"]["mase"] is not None)
            if any(r["metrics"]["mase"] is not None for r in rows) else None,
            "rmsse_macro": statistics.mean(r["metrics"]["rmsse"] for r in rows
                                           if r["metrics"]["rmsse"] is not None)
            if any(r["metrics"]["rmsse"] is not None for r in rows) else None}


def _live_case(profile, series, horizon, method, cache, pass_name):
    from app.services import predictive_analysis_service as analysis
    from app.services import predictive_cache_service as cache_service

    row = {"implementation": "live", "product_id": series.product_id,
           "horizon": horizon, "method": method, "requested_method": method, "pass": pass_name}
    before = cache.hits, cache.computed
    started = time.perf_counter()

    def compute_block():
        with cache.batch() if hasattr(cache, "batch") else nullcontext():
            return analysis.prediction_block(series, horizon, method,
                predictor=cache_service.cached_predictor(cache, series.product_id))

    with profile.scope(**row, periods=series.periods):
        forecast, evaluation = profile.measure("pipeline", compute_block)
        row.update(seconds=time.perf_counter() - started, status="ready" if forecast else "unavailable",
                   forecast=forecast.path if forecast else None,
                   selected=forecast.model if forecast else None,
                   backtests={name: _score_folds(series, result) for name, result in evaluation.results.items()}
                   if evaluation else {}, cache_hits=cache.hits - before[0], cache_misses=cache.computed - before[1])
    return row


def _experimental_case(profile, series, horizon, method):
    from app.services import backtest_service as backtest
    from app.services import experimental_2025_models as experimental

    row = {"implementation": "experimental", "product_id": series.product_id,
           "horizon": horizon, "method": method, "pass": "cold"}
    origins = backtest.origins(series.periods, horizon, experimental.MIN_TRAIN)
    started = time.perf_counter()
    with profile.scope(**row, periods=series.periods):
        folds, blocked = [], []
        for origin in origins:
            result = experimental.forecast_candidate(method, series.values[:origin], series.starts[:origin], horizon)
            if result["available"]:
                folds.append(backtest.Fold(origin, series.values[origin:origin + horizon], result["forecast"]))
            else:
                blocked.append({"origin": origin, "reason": result["unavailable_reason"]})
        final = experimental.forecast_candidate(method, series.values, series.starts, horizon)
        scored = _score_folds(series, backtest.BacktestResult(method, horizon, folds))
    row.update(seconds=time.perf_counter() - started, status="ready" if final["available"] else "unavailable",
               forecast=final["forecast"], selected=method if final["available"] else None,
               unavailable_reason=final["unavailable_reason"], blocked_folds=blocked,
               requested_origins=origins, backtests={method: scored}, cache_hits=0, cache_misses=0,
               comparison_eligible=not blocked)
    return row


def _machine():
    result = {"visible_cpu": os.cpu_count(), "affinity_cpu": len(os.sched_getaffinity(0)),
              "rss_peak_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    for name in ("cpu.max", "memory.max"):
        path = Path("/sys/fs/cgroup") / name
        result[name] = path.read_text().strip() if path.is_file() else None
    return result


def _save(output, report, profile):
    report["profile"] = profile.summary()
    report["events"] = [e for e in profile.events if e["stage"] in (
        "fit", "fit_predict", "cache_transaction", "cache_write", "pipeline")]
    report["machine"] = _machine()
    output.write_text(json.dumps(report, allow_nan=False, indent=2), encoding="utf-8")


def run(args):
    validate_demo()
    output = Path(args.output).resolve()
    if os.name != "posix" or not output.is_relative_to(Path("/tmp")) or output.suffix != ".json":
        raise ValueError("Output must be a JSON file strictly beneath container /tmp")
    if output.exists():
        raise ValueError("Output already exists; refusing to replace benchmark evidence")
    profile = Profile()
    selected, coverage = _materialize(profile, args.products, args.as_of)
    if args.expected_fingerprint and coverage["data_fingerprint"] != args.expected_fingerprint:
        raise ValueError("DEMO inputs differ from the preserved baseline; refusing incomparable fits")
    from app.services import forecast_service as forecast
    from app.services import predictive_cache_service as cache_service
    from app.services import experimental_2025_models as experimental
    from app.services import forecast_advanced_service as advanced

    report = {"phase": args.phase, "coverage": coverage, "records": [], "cache_sizes": {},
              "as_of": args.as_of.isoformat() if args.as_of else None,
              "registry": {"live": list(forecast.MODELS), "experimental": list(experimental.MODELS),
                           "union": sorted(set(forecast.MODELS) | set(experimental.MODELS))},
              "horizons": list(forecast.HORIZONS_DAYS), "max_folds_unchanged": 12,
              "arima_orders": list(advanced.ARIMA_ORDERS),
              "metric_policy": "WAPE/MAE/Bias pooled; MASE/RMSSE each TRAIN-only fold plus macro means",
              "timing_policy": "Nested stages overlap; do not add their totals. No HTTP/scheduler inference.",
              "complete": False}
    scratch = Path(tempfile.mkdtemp(prefix="predictive-benchmark-", dir="/tmp"))
    profile.install()
    began_wall, began_cpu = time.perf_counter(), time.process_time()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            for method in (*forecast.MODELS, forecast.AUTO_MODEL):
                directory = scratch / method
                cache = cache_service._Predictions(directory)
                for pass_name in ("cold", "hot", "second", "persisted"):
                    if pass_name == "persisted":
                        cache = cache_service._Predictions(directory)
                    for series in selected:
                        for horizon in forecast.HORIZONS_DAYS:
                            report["records"].append(_live_case(profile, series, horizon, method, cache, pass_name))
                    _save(output, report, profile)
                    print(json.dumps({"method": method, "pass": pass_name, "completed_records": len(report["records"])}), flush=True)
                report["cache_sizes"][method] = sum(p.stat().st_size for p in directory.glob("predictions.sqlite3*"))
            for method in experimental.MODELS:
                for series in selected:
                    for horizon in forecast.HORIZONS_DAYS:
                        report["records"].append(_experimental_case(profile, series, horizon, method))
                _save(output, report, profile)
                print(json.dumps({"method": method, "implementation": "experimental", "completed_records": len(report["records"])}), flush=True)
        report["complete"] = True
    finally:
        profile.restore()
        report.update(seconds_total=time.perf_counter() - began_wall,
                      cpu_seconds_total=time.process_time() - began_cpu)
        _save(output, report, profile)
    print(json.dumps({"complete": report["complete"], "records": len(report["records"]),
                      "seconds_total": report["seconds_total"]}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=("before", "after"))
    parser.add_argument("--output", required=True)
    parser.add_argument("--products", type=int, default=0, help="0 measures ALL eligible DEMO products")
    parser.add_argument("--as-of", type=date.fromisoformat, help="Freeze the same calendar cutoff as baseline")
    parser.add_argument("--expected-fingerprint", help="Refuse model work unless inputs match the baseline")
    args = parser.parse_args()
    if args.products < 0:
        parser.error("--products must be nonnegative")
    run(args)


if __name__ == "__main__":
    main()
