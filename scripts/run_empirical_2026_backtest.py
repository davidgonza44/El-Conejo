"""Frozen offline STRICT 7/7 experiment. No model edits, tuning, DB or fallback.

Set PYTHONHASHSEED=0 before launch. --smoke executes the identical evaluation and
reporting path on the first two TRAIN-eligible products and their first two
eligible shared origins; it must never be used to change the protocol.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter
from contextlib import ExitStack
import csv
from datetime import date, datetime, timezone
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import warnings

# Established offline import pattern: load service modules, never create_app().
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[variable] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def offline_guard(event, args):
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise RuntimeError("Network/database access prohibited in offline experiment")


sys.addaudithook(offline_guard)
from app.services import empirical_backtest_service as empirical
from app.services import forecast_service as forecast, forecast_advanced_service as advanced
from app.services.predictive_data_service import TimeSeries
import empirical_2026_reporting as reporting

PREFIX = "empirical_2026_backtest_7d_strict_"
BUILD_REPORT_HASH = "087bda535bc60f49f773d026434e10df5adcf5ce59fbc8669ffc861dd9ce82f8"
INPUT_DIR = ROOT / "artifacts/empirical_2026"
SOURCE_NAMES = ("productos_2026.csv", "movimientos_detalle_2026.csv",
                "movimientos_detalle_2026_auditoria_18092026.csv", "demanda_limpia_2026.csv",
                "existencias_2026_final.csv", "auditoria_elegibilidad_semantica_2026.md")
STATUSES = ("SUCCESS", "INELIGIBLE", "MODEL_UNAVAILABLE", "ERROR")
FOLD_FIELDS = ("product_code", "model", "origin", "origin_date", "train_start", "train_size",
               "positive_train_days", "original_train_eligible", "model_train_eligible", "train_constant", "horizon",
               "status", "reason", "common_support", "all_zero_observed_test", "target_dates",
               "actual", "predicted", "mae", "rmse", "bias", "wape", "mape", "mape_n", "mape_excluded")
PAIR_FIELDS = ("product_code", "origin", "origin_date", "model", "target_index", "target_date",
               "actual", "predicted", "status", "reason", "common_support")
LIMITATIONS = [
    "Common folds >=3 is a predeclared methodological decision, not a statistical guarantee.",
    "Macro MAE/RMSE and Bias mix product scales: cross-product interpretation is limited.",
    "Pooled WAPE is weighted by recorded quantities; pooled MAE/RMSE mix scales.",
    "Neither pooled totals nor metrics are a homogeneous economic or physical catalogue magnitude.",
    "MAPE excludes actual=0; WAPE/MAPE remain NA (JSON null/CSV empty) for all-zero actuals.",
    "Bias = prediction minus actual; positive means overprediction.",
    "Service erases internal exception messages. model_unavailable/error causes cannot be decomposed reliably.",
    "ARIMA internally discards nonconvergent/numerically invalid orders; emitted warnings do not count discarded fits.",
    "An ARIMA SUCCESS does not establish convergence of every candidate order; no orders were rerun or changed.",
    "Strata use each full observed suffix after evaluation, including later dates, for description only, never eligibility.",
    "No 30-day, SENSITIVITY, QUARANTINE, replenishment or purchasing experiment was executed.",
    "Seven-day shared tests leave the final two operational dates outside the complete-test grid.",
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def write_json(path, payload):
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def integrity_inputs(source_dir):
    report_path = INPUT_DIR / "empirical_2026_build_report.json"
    require(digest(report_path) == BUILD_REPORT_HASH, "Build report SHA-256 changed; STOP")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    require(report["READY_FOR_BACKTEST"] is True, "Build not ready")
    require(report["source_sha256_before"] == report["source_sha256_after"], "Construction source mismatch")
    require(set(report["source_sha256_before"]) == set(SOURCE_NAMES), "Unexpected construction source set")
    require(digest(ROOT / "scripts/build_empirical_2026.py") == report["builder_sha256"], "Builder SHA-256 changed")
    require(digest(ROOT / "app/services/demand_data_service.py") == report["operational_helper_sha256"],
            "Operational helper SHA-256 changed")
    paths = {name: source_dir / name for name in SOURCE_NAMES}
    expected = dict(report["source_sha256_before"])
    for name, sha in report["artifact_sha256"].items():
        require(Path(name).name == name and name.startswith("empirical_2026_"), "Invalid artifact filename")
        paths[name], expected[name] = INPUT_DIR / name, sha
    paths[report_path.name], expected[report_path.name] = report_path, BUILD_REPORT_HASH
    for name, path in paths.items():
        require(not path.is_symlink() and digest(path) == expected[name], "Input SHA-256 changed: " + name)
    require(report["cohort_counts"]["STRICT"] == 2087, "Unexpected STRICT size")
    return report, paths, expected


def source_identity():
    paths = {"scripts/run_empirical_2026_backtest.py": Path(__file__).resolve(),
             "scripts/empirical_2026_reporting.py": Path(reporting.__file__).resolve()}
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if name == "app" or name.startswith("app."):
            if path and Path(path).resolve().is_relative_to(ROOT):
                resolved = Path(path).resolve()
                paths[resolved.relative_to(ROOT).as_posix()] = resolved
    return paths, {name: digest(path) for name, path in paths.items()}


def load_panel():
    calendar_rows = list(read_csv(INPUT_DIR / "empirical_2026_operational_calendar.csv"))
    calendar = [date.fromisoformat(row["fecha_real"]) for row in calendar_rows]
    require(len(calendar) == 216 and calendar == sorted(set(calendar)), "Invalid operational calendar")
    require([int(row["operational_index"]) for row in calendar_rows] == list(range(1, 217)), "Calendar indices")
    manifest = {row["codigo_producto"]: row for row in read_csv(INPUT_DIR / "empirical_2026_cohort_manifest.csv")
                if row["cohorte"] == "STRICT"}
    require(len(manifest) == 2087, "STRICT manifest must have 2087 products")
    series = {code: TimeSeries(index, "daily", time_unit="operational_day",
                              is_service=row["is_service"] == "true")
              for index, (code, row) in enumerate(sorted(manifest.items()), 1)}
    for row in read_csv(INPUT_DIR / "empirical_2026_series_strict.csv"):
        code = row["codigo_producto"]
        require(code in series and row["cohorte"] == "STRICT", "Non-STRICT series record")
        point, day = series[code], date.fromisoformat(row["fecha_real"])
        value = float(row["demanda_observada"])
        require(math.isfinite(value) and value >= 0, "Invalid observed quantity")
        require(row["is_service"] == manifest[code]["is_service"], "Service metadata mismatch")
        require(int(row["product_operational_index"]) == len(point.values) + 1, "Product index mismatch")
        require(calendar[int(row["operational_index"]) - 1] == day, "Global index mismatch")
        point.starts.append(day)
        point.values.append(value)
    for point in series.values():
        require(point.values and point.values[0] > 0, "First observed demand must be positive")
        offset = bisect_left(calendar, point.starts[0])
        require(point.starts == calendar[offset:], "Observed suffix/left censoring violation")
    return calendar, series


def selected_panel(series, calendar, smoke):
    origins = empirical.operational_origins(calendar, horizon=7, step=7, initial_origin=60)
    if not smoke:
        return [(code, point, origins) for code, point in series.items()]
    selected = []
    for code, point in series.items():
        eligible = [origin for origin in origins if empirical.train_only_eligibility(
            point.values[:bisect_left(point.starts, calendar[origin])]) is None]
        if eligible:
            selected.append((code, point, eligible[:2]))
        if len(selected) == 2:
            break
    require(len(selected) == 2, "Smoke requires two TRAIN-eligible products")
    return selected


def guard_output(output):
    require(output.resolve().is_relative_to(INPUT_DIR.resolve()), "Output outside protected artifact root")
    require(not output.is_symlink(), "Output symlink refused")
    probe = output / (PREFIX + "manifest.json")
    ignored = subprocess.run(["git", "check-ignore", "-q", "--", str(probe.relative_to(ROOT))],
                             cwd=ROOT, check=False, capture_output=True)
    require(ignored.returncode == 0, "Output is not Git-ignored")
    rules = [line.strip() for line in (ROOT / ".repomixignore").read_text(encoding="utf-8").splitlines()]
    require("artifacts/empirical_2026/**" in rules and not any(line.startswith("!") for line in rules),
            "Repomix privacy boundary unavailable")
    require(not output.exists() or not any(output.glob(PREFIX + "*")), "Existing run artifacts; STOP, never overwrite")
    output.mkdir(parents=True, exist_ok=True)


def csv_writer(stack, output, suffix, fields):
    handle = stack.enter_context((output / (PREFIX + suffix + ".csv")).open("x", encoding="utf-8", newline=""))
    writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    return writer, handle


def serialize_fold(row, point, common):
    measured = row.metrics.to_dict() if row.metrics else {}
    train = point.values[:row.train_size]
    result = {"product_code": row.product_code, "model": row.model, "origin": row.origin,
              "origin_date": str(row.origin_date), "train_start": str(point.starts[0]) if row.train_size else None,
              "train_size": row.train_size, "positive_train_days": row.positives_train,
              "original_train_eligible": row.train_size >= 60 and row.positives_train >= 12,
              "model_train_eligible": row.train_eligible, "train_constant": bool(train) and max(train) == min(train),
              "horizon": row.horizon, "status": row.status,
              "reason": row.reason, "common_support": common,
              "all_zero_observed_test": bool(row.actual) and not any(row.actual),
              "target_dates": json.dumps(list(map(str, row.target_dates))),
              "actual": json.dumps(row.actual), "predicted": json.dumps(row.predicted)}
    result.update({key: measured.get(key) for key in FOLD_FIELDS if key not in result})
    return result


def emit_evaluation(evaluation, point, writers, coverage):
    common = set(evaluation.common_origins)
    for row in evaluation.rows:
        shared = row.origin in common
        require(row.status in STATUSES, "Unknown evaluation status")
        require(row.status != empirical.SUCCESS or len(row.predicted) == 7, "Incomplete SUCCESS")
        record = serialize_fold(row, point, shared)
        writers["folds"].writerow(record)
        if row.status != empirical.SUCCESS:
            writers["failures"].writerow(record)
        for index, day in enumerate(row.target_dates):
            writers["predictions"].writerow({"product_code": row.product_code, "origin": row.origin,
                "origin_date": str(row.origin_date), "model": row.model, "target_index": index + 1,
                "target_date": str(day), "actual": row.actual[index] if len(row.actual) == 7 else None,
                "predicted": row.predicted[index] if row.status == empirical.SUCCESS else None,
                "status": row.status, "reason": row.reason, "common_support": shared})
        coverage.add(row, shared, record["train_constant"])


def evaluate_panel(panel, calendar, output):
    products, evaluations, warning_counts = [], [], Counter()
    coverage = reporting.Coverage()
    with ExitStack() as stack:
        writers, handles = {}, []
        for suffix, fields in (("folds", FOLD_FIELDS), ("failures", FOLD_FIELDS), ("predictions", PAIR_FIELDS),
                ("warnings", ("product_code", "category", "message", "count"))):
            writers[suffix], handle = csv_writer(stack, output, suffix, fields)
            handles.append(handle)
        start = time.perf_counter()
        for index, (code, point, origins) in enumerate(panel, 1):
            options = {} if origins == empirical.operational_origins(calendar, horizon=7, step=7) else {
                "explicit_origins": origins}
            with warnings.catch_warnings(record=True) as recorded:
                warnings.simplefilter("always")
                evaluation = empirical.evaluate_operational(point, operational_calendar=calendar,
                    horizon=7, step=7, initial_origin=60, candidates=reporting.MODELS, product_code=code, **options)
            counted = Counter((item.category.__name__, str(item.message)) for item in recorded)
            for (category, message), count in counted.items():
                writers["warnings"].writerow({"product_code": code, "category": category,
                                              "message": message, "count": count})
                warning_counts[category + ": " + message] += count
            emit_evaluation(evaluation, point, writers, coverage)
            products.extend(reporting.product_rows(evaluation, point))
            # ponytail: retain only common rows for exact existing pooled API; no new metric accumulator.
            common = set(evaluation.common_origins)
            evaluation.rows = [row for row in evaluation.rows if row.origin in common]
            evaluation.origins = sorted(common)
            if common:
                evaluations.append(evaluation)
            for handle in handles:
                handle.flush()
            print(json.dumps({"products_completed": index, "products_total": len(panel),
                              "elapsed_seconds": round(time.perf_counter() - start, 3)}), flush=True)
    return products, evaluations, coverage, warning_counts


def configuration(smoke):
    require(tuple(forecast.MODELS) == reporting.MODELS, "Model registry changed")
    require((empirical.MIN_TRAIN, empirical.MIN_POSITIVE, empirical.MIN_COMMON_FOLDS) == (60, 12, 3),
            "Predeclared technical/publication rules changed")
    return {"cohort": "STRICT", "horizon": 7, "step": 7, "time_unit": "operational_day",
            "window": "expanding", "initial_origin": 60, "models": list(reporting.MODELS),
            "min_train": 60, "min_positive_train": 12, "min_publicable_common_folds": 3,
            "smoke": smoke, "smoke_selection": "first two sorted TRAIN-eligible products; first two eligible shared origins",
            "arima_orders_unchanged": advanced.ARIMA_ORDERS,
            "arima_selection": "existing converged finite-AIC minimum on TRAIN only",
            "rf_unchanged": {"n_estimators": 50, "max_depth": 6, "random_state": 42, "n_jobs": 1},
            "model_requirements": advanced.MODEL_REQUIREMENTS, "moving_average_window": forecast.WINDOW,
            "ties": {"relative_tolerance": 1e-9, "absolute_tolerance": 1e-12},
            "strata": {"descriptor": "zero days / days in full observed product suffix",
                       "boundaries": "[0,0.50), [0.50,0.80), [0.80,0.95), [0.95,1]",
                       "adi": "observed_days / positive_days", "eligibility_effect": "none"},
            "quantiles": "linear interpolation at (n-1)*q; Hyndman-Fan type 7",
            "null_encoding": {"json": "null", "csv": "empty field; never zero"},
            "threads": 1, "pythonhashseed": os.environ.get("PYTHONHASHSEED"),
            "limitations": LIMITATIONS}


def run(source_dir, smoke=False):
    started, clock = datetime.now(timezone.utc).isoformat(), time.perf_counter()
    config = configuration(smoke)
    _, input_paths, before = integrity_inputs(source_dir)
    code_paths, code_before = source_identity()
    calendar, series = load_panel()
    panel = selected_panel(series, calendar, smoke)
    output = INPUT_DIR / "smoke_7d_strict" if smoke else INPUT_DIR
    guard_output(output)
    manifest = {"configuration": config, "started_utc": started, "status": "STARTED",
                "input_sha256_before": before, "code_sha256_before": code_before,
                "versions": {name: version(name) for name in ("numpy", "scipy", "pandas", "scikit-learn", "statsmodels")},
                "python": platform.python_version(), "platform": platform.system() + " " + platform.machine(),
                "selected_products": len(panel), "selected_product_origins": sum(len(item[2]) for item in panel),
                "shared_origins": empirical.operational_origins(calendar, horizon=7, step=7),
                "product_id": "1-based sorted STRICT code; local audit identifier, not database identity"}
    write_json(output / (PREFIX + "manifest.json"), manifest)
    try:
        products, evaluations, coverage, observed_warnings = evaluate_panel(panel, calendar, output)
        summary = reporting.summarize(products, evaluations, coverage, empirical)
        after = {name: digest(path) for name, path in input_paths.items()}
        code_after = {name: digest(path) for name, path in code_paths.items()}
        require(before == after, "Input SHA-256 changed during experiment; STOP")
        require(code_before == code_after, "Source code changed during experiment; STOP")
        with ExitStack() as stack:
            for suffix, rows in (("product_metrics", products), ("coverage", coverage.rows())):
                writer, _ = csv_writer(stack, output, suffix, tuple(rows[0]))
                writer.writerows(rows)
        summary.update({"status": "COMPLETE", "configuration": config, "input_sha256_after": after,
                        "code_sha256_after": code_after, "warnings": dict(observed_warnings),
                        "elapsed_seconds": time.perf_counter() - clock,
                        "finished_utc": datetime.now(timezone.utc).isoformat(),
                        "generated_product_origins": sum(len(item[2]) for item in panel),
                        "generated_model_cells": sum(len(item[2]) * 6 for item in panel)})
        write_json(output / (PREFIX + "summary.json"), summary)
    except BaseException as exc:
        write_json(output / (PREFIX + "diagnostic.json"), {"status": "FAILED", "exception_type": type(exc).__name__,
            "message": str(exc), "elapsed_seconds": time.perf_counter() - clock,
            "input_sha256_after_failure": {name: digest(path) for name, path in input_paths.items()},
            "code_sha256_after_failure": {name: digest(path) for name, path in code_paths.items()},
            "partial_artifacts": "Retained, not completed results. No retry or parameter change."})
        raise
    output_hashes = {path.name: digest(path) for path in sorted(output.glob(PREFIX + "*")) if path.is_file()}
    write_json(output / (PREFIX + "artifact_hashes.json"), {"sha256": output_hashes,
                "self_hash": "Not recursive; hash this manifest externally."})
    print(json.dumps({"status": "COMPLETE", "smoke": smoke, "products_processed": len(panel),
                      "elapsed_seconds": summary["elapsed_seconds"]}), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True, help="Original source directory (hash-only access)")
    parser.add_argument("--smoke", action="store_true", help="Technical two-product/two-origin smoke, not protocol tuning")
    arguments = parser.parse_args()
    run(arguments.source_dir, arguments.smoke)
