"""Execute only the three final predeclared offline experiments, sequentially.

The completed STRICT 7/7 runner, reports, models and protocol remain immutable.
Set PYTHONHASHSEED=0; use the same Python environment as the frozen experiment.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter
from contextlib import ExitStack
from datetime import date, datetime, timezone
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import time
import warnings

import run_empirical_2026_backtest as frozen

empirical, reporting = frozen.empirical, frozen.reporting
ROOT, INPUT_DIR = frozen.ROOT, frozen.INPUT_DIR
EXPERIMENTS = (("STRICT", 30), ("SENSITIVITY", 7), ("SENSITIVITY", 30))
COHORT_SIZES = {"STRICT": 2087, "SENSITIVITY": 45}
STRICT7_MANIFEST_SHA = "b305a68a69a306320d161f66865b1bf2b0890bcc1499f3ef804f35e7fa8efedb"
STRICT7_HASHES_SHA = "f5fb56cbe04afaee0f08cddc6aade27cd54c68d1a61c134a0bdc8d91ede4672d"
PACKAGES = ("numpy", "scipy", "pandas", "scikit-learn", "statsmodels")


def prefix(cohort, horizon):
    frozen.require((cohort, horizon) in EXPERIMENTS, "Experiment is not in the final predeclared batch")
    return f"empirical_2026_backtest_{horizon}d_{cohort.lower()}_"


def configuration(cohort, horizon):
    prefix(cohort, horizon)
    config = frozen.configuration(False)
    config.update({"cohort": cohort, "horizon": horizon, "step": horizon,
                   "input_series": f"empirical_2026_series_{cohort.lower()}.csv",
                   "smoke_selection": None, "cohort_products": COHORT_SIZES[cohort],
                   "limitations": frozen.LIMITATIONS[:-2] + [
                       "This run evaluates only its declared cohort and horizon; cohorts are never pooled.",
                       f"Complete shared tests leave {(216 - 60) % horizon} final operational dates untested.",
                       "No tuning, product selection, replenishment or purchasing is performed."]})
    return config


def frozen_snapshot():
    """Validate original authority, then retain every old full/smoke output byte."""
    manifest_path = INPUT_DIR / (frozen.PREFIX + "manifest.json")
    hashes_path = INPUT_DIR / (frozen.PREFIX + "artifact_hashes.json")
    frozen.require(frozen.digest(manifest_path) == STRICT7_MANIFEST_SHA, "Frozen STRICT7 manifest changed")
    frozen.require(frozen.digest(hashes_path) == STRICT7_HASHES_SHA, "Frozen STRICT7 hash manifest changed")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    hashes = json.loads(hashes_path.read_text(encoding="utf-8"))["sha256"]
    paths = {name: ROOT / name for name in manifest["code_sha256_before"]}
    for name, expected in manifest["code_sha256_before"].items():
        frozen.require(frozen.digest(paths[name]) == expected, "Frozen source changed: " + name)
    for name, expected in hashes.items():
        frozen.require(Path(name).name == name and name.startswith(frozen.PREFIX), "Invalid frozen artifact name")
        frozen.require(frozen.digest(INPUT_DIR / name) == expected, "Frozen STRICT7 artifact changed: " + name)
    outputs = list(INPUT_DIR.glob(frozen.PREFIX + "*")) + list(INPUT_DIR.glob("strict7_*"))
    smoke = INPUT_DIR / "smoke_7d_strict"
    if smoke.exists():
        outputs.extend(smoke.glob(frozen.PREFIX + "*"))
    for path in outputs:
        frozen.require(path.is_file() and not path.is_symlink(), "Invalid frozen output path")
        paths[path.relative_to(ROOT).as_posix()] = path
    return paths, {name: frozen.digest(path) for name, path in paths.items()}, manifest


def source_identity():
    paths, _ = frozen.source_identity()
    for name in ("run_empirical_2026_final_batch.py", "test_empirical_2026_final_batch.py"):
        path = ROOT / "scripts" / name
        paths[path.relative_to(ROOT).as_posix()] = path
    return paths, {name: frozen.digest(path) for name, path in paths.items()}


def load_panel(cohort):
    calendar_rows = list(frozen.read_csv(INPUT_DIR / "empirical_2026_operational_calendar.csv"))
    calendar = [date.fromisoformat(row["fecha_real"]) for row in calendar_rows]
    frozen.require(len(calendar) == 216 and calendar == sorted(set(calendar)), "Invalid operational calendar")
    frozen.require([int(row["operational_index"]) for row in calendar_rows] == list(range(1, 217)), "Calendar indices")
    manifest = {row["codigo_producto"]: row for row in frozen.read_csv(INPUT_DIR / "empirical_2026_cohort_manifest.csv")
                if row["cohorte"] == cohort}
    frozen.require(len(manifest) == COHORT_SIZES[cohort], "Unexpected cohort size")
    series = {code: frozen.TimeSeries(index, "daily", time_unit="operational_day", is_service=row["is_service"] == "true")
              for index, (code, row) in enumerate(sorted(manifest.items()), 1)}
    for row in frozen.read_csv(INPUT_DIR / f"empirical_2026_series_{cohort.lower()}.csv"):
        code = row["codigo_producto"]
        frozen.require(code in series and row["cohorte"] == cohort, "Wrong cohort in series input")
        point, day = series[code], date.fromisoformat(row["fecha_real"])
        value = float(row["demanda_observada"])
        frozen.require(math.isfinite(value) and value >= 0, "Invalid observed quantity")
        frozen.require(row["is_service"] == manifest[code]["is_service"], "Service metadata mismatch")
        frozen.require(int(row["product_operational_index"]) == len(point.values) + 1, "Product index mismatch")
        frozen.require(calendar[int(row["operational_index"]) - 1] == day, "Global index mismatch")
        point.starts.append(day)
        point.values.append(value)
    for point in series.values():
        frozen.require(point.values and point.values[0] > 0, "First observed demand must be positive")
        offset = bisect_left(calendar, point.starts[0])
        frozen.require(point.starts == calendar[offset:], "Observed suffix/left censoring violation")
    return calendar, series


def guard_output(run_prefix):
    frozen.require(not INPUT_DIR.is_symlink(), "Output symlink refused")
    probe = INPUT_DIR / (run_prefix + "manifest.json")
    ignored = subprocess.run(["git", "check-ignore", "-q", "--", str(probe.relative_to(ROOT))],
                             cwd=ROOT, check=False, capture_output=True)
    frozen.require(ignored.returncode == 0, "Output is not Git-ignored")
    rules = [line.strip() for line in (ROOT / ".repomixignore").read_text(encoding="utf-8").splitlines()]
    frozen.require("artifacts/empirical_2026/**" in rules and not any(line.startswith("!") for line in rules),
                   "Repomix privacy boundary unavailable")
    frozen.require(not any(INPUT_DIR.glob(run_prefix + "*")), "Existing run artifacts; STOP, never overwrite")


def csv_writer(stack, run_prefix, suffix, fields):
    handle = stack.enter_context((INPUT_DIR / (run_prefix + suffix + ".csv")).open("x", encoding="utf-8", newline=""))
    writer = frozen.csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    return writer, handle


def emit_evaluation(evaluation, point, writers, coverage):
    common = set(evaluation.common_origins)
    for row in evaluation.rows:
        shared = row.origin in common
        frozen.require(row.status in frozen.STATUSES, "Unknown evaluation status")
        frozen.require(row.status != empirical.SUCCESS or len(row.predicted) == row.horizon, "Incomplete SUCCESS")
        record = frozen.serialize_fold(row, point, shared)
        writers["folds"].writerow(record)
        if row.status != empirical.SUCCESS:
            writers["failures"].writerow(record)
        for index, day in enumerate(row.target_dates):
            writers["predictions"].writerow({"product_code": row.product_code, "origin": row.origin,
                "origin_date": str(row.origin_date), "model": row.model, "target_index": index + 1,
                "target_date": str(day), "actual": row.actual[index] if len(row.actual) == row.horizon else None,
                "predicted": row.predicted[index] if row.status == empirical.SUCCESS else None,
                "status": row.status, "reason": row.reason, "common_support": shared})
        coverage.add(row, shared, record["train_constant"])


def zero_tests(evaluation):
    common = set(evaluation.common_origins)
    counts = Counter()
    for row in evaluation.rows:
        if row.model != reporting.MODELS[0] or len(row.actual) != row.horizon or any(row.actual):
            continue
        counts["observed_product_origins"] += 1
        counts["original_eligible_product_origins"] += row.train_size >= 60 and row.positives_train >= 12
        counts["common_product_origins"] += row.origin in common
        counts["publicable_common_product_origins"] += row.origin in common and len(common) >= 3
    return counts


def evaluate_panel(series, calendar, horizon, run_prefix):
    products, evaluations, warning_counts, zeros = [], [], Counter(), Counter()
    coverage = reporting.Coverage()
    with ExitStack() as stack:
        writers, handles = {}, []
        for suffix, fields in (("folds", frozen.FOLD_FIELDS), ("failures", frozen.FOLD_FIELDS),
                               ("predictions", frozen.PAIR_FIELDS), ("warnings", ("product_code", "category", "message", "count"))):
            writers[suffix], handle = csv_writer(stack, run_prefix, suffix, fields)
            handles.append(handle)
        start = time.perf_counter()
        for index, (code, point) in enumerate(series.items(), 1):
            with warnings.catch_warnings(record=True) as recorded:
                warnings.simplefilter("always")
                evaluation = empirical.evaluate_operational(point, operational_calendar=calendar,
                    horizon=horizon, step=horizon, initial_origin=60, candidates=reporting.MODELS, product_code=code)
            counted = Counter((item.category.__name__, str(item.message)) for item in recorded)
            for (category, message), count in counted.items():
                writers["warnings"].writerow({"product_code": code, "category": category, "message": message, "count": count})
                warning_counts[category + ": " + message] += count
            emit_evaluation(evaluation, point, writers, coverage)
            products.extend(reporting.product_rows(evaluation, point))
            zeros.update(zero_tests(evaluation))
            common = set(evaluation.common_origins)
            evaluation.rows = [row for row in evaluation.rows if row.origin in common]
            evaluation.origins = sorted(common)
            if common:
                evaluations.append(evaluation)
            for handle in handles:
                handle.flush()
            print(json.dumps({"experiment": run_prefix, "products_completed": index, "products_total": len(series),
                              "elapsed_seconds": round(time.perf_counter() - start, 3)}), flush=True)
    return products, evaluations, coverage, warning_counts, zeros


def identities_after(input_paths, code_paths, old_paths):
    return {"input_sha256_after": {name: frozen.digest(path) for name, path in input_paths.items()},
            "code_sha256_after": {name: frozen.digest(path) for name, path in code_paths.items()},
            "frozen_strict7_sha256_after": {name: frozen.digest(path) for name, path in old_paths.items()}}


def run(source_dir, cohort, horizon):
    started, clock = datetime.now(timezone.utc).isoformat(), time.perf_counter()
    run_prefix, config = prefix(cohort, horizon), configuration(cohort, horizon)
    frozen.require(os.environ.get("PYTHONHASHSEED") == "0", "Launch with PYTHONHASHSEED=0")
    old_paths, old_before, old_manifest = frozen_snapshot()
    versions = {name: version(name) for name in PACKAGES}
    frozen.require(versions == old_manifest["versions"] and platform.python_version() == old_manifest["python"],
                   "Python/dependency versions differ from frozen STRICT7")
    _, input_paths, before = frozen.integrity_inputs(source_dir)
    code_paths, code_before = source_identity()
    calendar, series = load_panel(cohort)
    origins = empirical.operational_origins(calendar, horizon=horizon, step=horizon, initial_origin=60)
    guard_output(run_prefix)
    manifest = {"configuration": config, "started_utc": started, "status": "STARTED",
                "input_sha256_before": before, "code_sha256_before": code_before,
                "frozen_strict7_sha256_before": old_before, "versions": versions,
                "python": platform.python_version(), "platform": platform.system() + " " + platform.machine(),
                "selected_products": len(series), "selected_product_origins": len(series) * len(origins),
                "shared_origins": origins, "product_id": "1-based sorted cohort code; local audit identifier, not database identity"}
    frozen.write_json(INPUT_DIR / (run_prefix + "manifest.json"), manifest)
    try:
        products, evaluations, coverage, observed_warnings, zeros = evaluate_panel(series, calendar, horizon, run_prefix)
        summary = reporting.summarize(products, evaluations, coverage, empirical)
        after = identities_after(input_paths, code_paths, old_paths)
        frozen.require(before == after["input_sha256_after"], "Input SHA-256 changed during experiment; STOP")
        frozen.require(code_before == after["code_sha256_after"], "Source code changed during experiment; STOP")
        frozen.require(old_before == after["frozen_strict7_sha256_after"], "Frozen STRICT7 changed during experiment; STOP")
        with ExitStack() as stack:
            for suffix, rows in (("product_metrics", products), ("coverage", coverage.rows())):
                writer, _ = csv_writer(stack, run_prefix, suffix, tuple(rows[0]))
                writer.writerows(rows)
        summary.update({"status": "COMPLETE", "configuration": config, **after, "warnings": dict(observed_warnings),
                        "all_zero_tests": {key: zeros[key] for key in ("observed_product_origins", "original_eligible_product_origins",
                            "common_product_origins", "publicable_common_product_origins")},
                        "elapsed_seconds": time.perf_counter() - clock, "finished_utc": datetime.now(timezone.utc).isoformat(),
                        "generated_product_origins": len(series) * len(origins),
                        "generated_model_cells": len(series) * len(origins) * len(reporting.MODELS)})
        frozen.write_json(INPUT_DIR / (run_prefix + "summary.json"), summary)
        output_hashes = {path.name: frozen.digest(path) for path in sorted(INPUT_DIR.glob(run_prefix + "*")) if path.is_file()}
        frozen.write_json(INPUT_DIR / (run_prefix + "artifact_hashes.json"), {"sha256": output_hashes,
                          "self_hash": "Not recursive; hash this manifest externally."})
    except BaseException as exc:
        frozen.write_json(INPUT_DIR / (run_prefix + "diagnostic.json"), {"status": "FAILED", "exception_type": type(exc).__name__,
            "message": str(exc), "elapsed_seconds": time.perf_counter() - clock,
            **identities_after(input_paths, code_paths, old_paths),
            "partial_artifacts": "INVALID incomplete run; not experimental results. No automatic retry or parameter change."})
        raise
    print(json.dumps({"status": "COMPLETE", "experiment": run_prefix, "products_processed": len(series),
                      "elapsed_seconds": summary["elapsed_seconds"]}), flush=True)
    return summary


def run_batch(source_dir):
    for cohort, horizon in EXPERIMENTS:
        guard_output(prefix(cohort, horizon))
    for cohort, horizon in EXPERIMENTS:
        run(source_dir, cohort, horizon)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True, help="Original source directory (hash-only access)")
    arguments = parser.parse_args()
    run_batch(arguments.source_dir)
