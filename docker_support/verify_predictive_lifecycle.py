"""Isolated LIVE lifecycle checks using read-only canonical DEMO series.

Only private temporary cache managers are exercised. Synthetic mutations and
fault injection stay in this process; no HTTP, Gunicorn, business write, dataset,
sealed result, application cache or user account is changed.
"""
import argparse
from copy import deepcopy
from functools import wraps
import json
import os
from pathlib import Path
import tempfile
from threading import Event
import time
from unittest.mock import patch

from docker_support.guard import validate_demo


def same_key_and_invalidation(series, root):
    from app.services import forecast_service as forecast, forecast_advanced_service as advanced
    from app.services import predictive_cache_service as cache, predictive_job_service as jobs

    original = forecast.predict
    original_fit = advanced.ARIMA.fit
    counts = {"predict": 0, "arima_fit": 0}
    entered, release = Event(), Event()
    managers = []

    @wraps(original)
    def counted(*args, **kwargs):
        counts["predict"] += 1
        if not entered.is_set():
            entered.set()
            if not release.wait(15):
                raise TimeoutError("Synthetic test release deadline")
        return original(*args, **kwargs)

    @wraps(original_fit)
    def counted_fit(*args, **kwargs):
        counts["arima_fit"] += 1
        return original_fit(*args, **kwargs)

    def manager():
        instance = jobs.Jobs(cache._Predictions(root / "shared"))
        managers.append(instance)
        return instance

    fingerprint = cache.digest((series.values, [day.isoformat() for day in series.starts]))
    try:
        with patch.object(forecast, "predict", counted), patch.object(advanced.ARIMA, "fit", counted_fit):
            specs = [(series, horizon, "mean") for horizon in (7, 30, 90)]
            token = jobs.prediction_token(specs)
            first, second = manager(), manager()
            assert first.request(specs)[0] == "pending"
            assert entered.wait(10)
            assert second.request(specs)[0] in ("pending", "running")
            baseline_calls = counts["predict"]
            for _ in range(40):
                for observer in (first, second):
                    state = observer.status(token)
                    assert state["status"] in ("pending", "running") and not state["terminal"]
            assert counts["predict"] == baseline_calls
            assert second.started == 0 and second.queue.empty()
            release.set()
            assert first.wait(30) and second.wait(30)
            status, blocks = first.request(specs)
            assert status == "ready" and second.request(specs) == (status, blocks)
            assert first.started == 1 and second.started == 0
            completed_calls = counts["predict"]
            restarted = manager()
            assert restarted.request(specs) == ("ready", blocks)
            assert restarted.cache.computed == 0 and restarted.started == 0
            assert counts["predict"] == completed_calls
            mutated = deepcopy(series)
            mutated.values[-1] += 1.0
            changed_specs = [(mutated, horizon, "mean") for horizon in (7, 30, 90)]
            assert jobs.prediction_token(changed_specs) != token
            assert restarted.request(changed_specs)[0] == "pending"
            assert restarted.wait(30)
            status, changed = restarted.request(changed_specs)
            assert status == "ready" and restarted.cache.computed > 0
            assert all(changed[(series.product_id, h)][0].total != blocks[(series.product_id, h)][0].total
                       for h in (7, 30, 90))
            assert restarted.request(specs) == ("ready", blocks)
            assert fingerprint == cache.digest((series.values, [day.isoformat() for day in series.starts]))
            assert counts["arima_fit"] == 0
            return {"status_polls_without_prediction": 80, "initial_predict_calls": baseline_calls,
                    "completed_predict_calls": completed_calls, "total_predict_calls": counts["predict"],
                    "arima_fits": counts["arima_fit"], "original_jobs": first.started + second.started,
                    "invalidation_jobs": restarted.started, "restored_original_exact": True,
                    "persisted_recompute_count": 0, "horizons": [7, 30, 90]}
    finally:
        release.set()
        for instance in managers:
            instance.wait(30)
            instance.shutdown(5)


def failures_and_deadlines(series, root):
    from app.services import forecast_service as forecast
    from app.services import predictive_cache_service as cache, predictive_job_service as jobs

    result = {}
    for expected in ("error", "unavailable"):
        instance = jobs.Jobs(cache._Predictions(root / expected))

        def fail(*args, **kwargs):
            if expected == "unavailable":
                raise forecast.ModelUnavailable("Controlled local unavailable model")
            raise RuntimeError("Controlled local computation failure")

        try:
            with patch.object(forecast, "predict", fail):
                specs = [(series, 7, "mean")]
                token = jobs.prediction_token(specs)
                assert instance.request(specs)[0] == "pending"
                assert instance.wait(20)
                status = instance.status(token)
                assert status["status"] == expected and status["terminal"]
                assert instance.request(specs)[0] == expected
                assert not instance.pending and instance.queue.empty()
                result[expected] = {"terminal": True, "jobs": instance.started, "pending": 0}
        finally:
            instance.shutdown(5)
    instance = jobs.Jobs(cache._Predictions(root / "deadlines"), timeout=1)
    unknown = instance.status(cache.digest("unknown-synthetic-key"))
    assert unknown["status"] == "expired" and unknown["terminal"]
    for status in ("pending", "running"):
        token = cache.digest(("stale-synthetic-key", status))
        instance.cache.disk.write_job(token, status)
        with patch.object(jobs.time, "time", return_value=time.time() + 2):
            expired = instance.status(token)
        assert expired["status"] == "error" and expired["terminal"] and expired["timed_out"]
    assert instance.started == 0
    result["deadline"] = {"pending_terminal": True, "running_terminal": True, "unknown_expired": True,
                          "jobs_started": instance.started}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if validate_demo() != "conejo_demo_8b867d4b":
        raise RuntimeError("Only the explicitly authorized DEMO database is allowed")
    output = Path(args.output).resolve()
    if os.name != "posix" or not output.is_relative_to(Path("/tmp")) or output.suffix != ".json":
        raise ValueError("Report must be JSON strictly beneath container /tmp")
    if output.exists():
        raise ValueError("Existing evidence will not be replaced")
    # Existing read-only loader validates the actual target and require_ready
    # before materializing canonical demand and closing its business session.
    from docker_support.benchmark_predictions import Profile, _materialize

    series, coverage = _materialize(Profile(), 1)
    if not series:
        raise RuntimeError("An eligible DEMO series is required")
    report = {"product_id": series[0].product_id, "horizons": [7, 30, 90], "complete": False}
    try:
        with tempfile.TemporaryDirectory(prefix="predictive-lifecycle-", dir="/tmp") as directory:
            report["reuse_and_invalidation"] = same_key_and_invalidation(series[0], Path(directory))
            report["failures"] = failures_and_deadlines(series[0], Path(directory))
        report["complete"] = True
        report["passed"] = True
    except Exception as error:
        report["passed"] = False
        report["error_type"] = type(error).__name__
        raise
    finally:
        output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"complete": True, "passed": True}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"failed": True, "error_type": type(error).__name__}), flush=True)
        raise SystemExit(1) from None
