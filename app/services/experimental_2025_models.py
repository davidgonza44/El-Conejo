"""Pure candidates for EVALUACIÓN EXPERIMENTAL 2025 and explicit transfer views.

No registry, DB, stock, order or production selector is imported. Configuration
is fixed before targets; rolling residual intervals remain experimental.
"""
from datetime import date, timedelta
from math import ceil, fsum, isfinite, sqrt
from statistics import mean
from types import SimpleNamespace

from . import experimental_adida_service as adida_service
from . import experimental_demand_service as demand_service
from . import scaled_error_service

BASELINES = ("naive", "mean", "moving_average", "linear_trend")
MODELS = ("zero", *BASELINES, "croston", "sba", "tsb", "two_part", "adida")
MIN_TRAIN = 14
NOMINAL_COVERAGE = 0.8
MIN_CALIBRATION = 9
MAX_CALIBRATION = 12


def _values(values):
    copied = list(values)
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not isfinite(value) or value < 0 for value in copied):
        raise ValueError("Quantities must be finite, nonnegative observations")
    return [float(value) for value in copied]


def _dates(starts, count):
    copied = list(starts)
    if len(copied) != count or any(type(day) is not date for day in copied):
        raise ValueError("One datetime.date per training observation is required")
    if any(right <= left for left, right in zip(copied, copied[1:])):
        raise ValueError("Training dates must be unique and chronological")
    return copied


def _continuous(starts):
    return all(right - left == timedelta(days=1) for left, right in zip(starts, starts[1:]))


def _baseline(model, history, horizon):
    # Same arithmetic as forecast_service's native daily baselines, without its
    # advanced-model/application imports. Seven is a predeclared window.
    if model == "zero":
        return [0.0] * horizon
    if model == "naive":
        return [history[-1]] * horizon
    if model in ("mean", "moving_average") or len(history) < 3:
        selected = history[-7:] if model == "moving_average" else history
        return [mean(selected)] * horizon
    n = len(history)
    center, y_mean = (n - 1) / 2, mean(history)
    slope = fsum((index - center) * (value - y_mean)
                 for index, value in enumerate(history)) / fsum((index - center) ** 2 for index in range(n))
    return [max(0.0, y_mean + slope * (n + offset - center)) for offset in range(horizon)]


def _intermittent(model, history, horizon, alpha, beta):
    first = next(index for index, value in enumerate(history) if value > 0)
    magnitude, interval, gap = history[first], float(first + 1), 1
    probability = 1.0 / (first + 1)
    for value in history[first + 1:]:
        probability += beta * ((1.0 if value > 0 else 0.0) - probability)
        if value > 0:
            magnitude += alpha * (value - magnitude)
            interval += alpha * (gap - interval)
            gap = 1
        else:
            gap += 1
    rate = probability * magnitude if model == "tsb" else magnitude / interval
    if model == "sba":
        rate *= 1 - alpha / 2
    return [max(0.0, rate)] * horizon


def _uncertainty(reason, count=0):
    return {"available": False, "method": "chronological_rolling_absolute_residual_quantile",
            "nominal_coverage": NOMINAL_COVERAGE, "calibration_count": count,
            "lower_bound": None, "upper_bound": None, "unavailable_reason": reason,
            "coverage_claim": "nominal_only_until_external_observed_coverage"}


def _empty(model, horizon, time_unit, configuration):
    return {"model": model, "available": False, "forecast": [], "forecast_total": None,
            "two_part": None, "uncertainty": _uncertainty("forecast_unavailable"),
            "policy": {"status": "abstain", "reason": []}, "unavailable_reason": None,
            "horizon": horizon, "time_unit": time_unit, "configuration": configuration}


def _blocked(result, reason):
    result.update(unavailable_reason=reason, policy={"status": "abstain", "reason": [reason]})
    result["uncertainty"] = _uncertainty(reason)
    result["prediction_policy"] = result["policy"]
    return result


def _special(model, values, starts, horizon, time_unit, configuration):
    if time_unit == "operational_day":
        return _operational_special(model, values, horizon, configuration)
    if model == "adida":
        candidate = adida_service.adida(values, aggregation_size=configuration["aggregation_size"],
                                       horizon=horizon, starts=starts,
                                       forecast_origin=starts[-1] + timedelta(days=1))
        reason = candidate["unavailable_reason"]
        return candidate["forecast"], None, candidate, reason["code"] if reason else None
    series = SimpleNamespace(values=values, starts=starts, granularity="daily", time_unit=time_unit)
    candidate = demand_service.analyze(series, horizon, SimpleNamespace(status="ELIGIBLE"))
    two_part = candidate["two_part"]
    expected = two_part["unconditional_expected_quantity"]
    forecast = [expected / horizon] * horizon if two_part["available"] else []
    return forecast, two_part, candidate, two_part["unavailable_reason"]


def _operational_special(model, values, horizon, configuration):
    """Index blocks on an explicitly supplied cadence; never rewrite dates.

    This is an additive experimental extension. A synthetic activity calendar
    declares exposure assumptions; it does not establish real opening days.
    """
    if model == "adida":
        bucket = configuration["aggregation_size"]
        if isinstance(bucket, bool) or not isinstance(bucket, int) or bucket < 1:
            return [], None, None, "invalid_aggregation_size"
        if len(values) < bucket:
            return [], None, None, "insufficient_history"
        offset = len(values) % bucket
        totals = [fsum(values[index:index + bucket]) for index in range(offset, len(values), bucket)]
        forecast = [totals[-1] / bucket] * horizon
        return forecast, None, {"aggregation_size": bucket, "horizon_unit": "operational_day",
                               "base_method": "naive_last_aggregate", "disaggregation_method": "uniform",
                               "discarded_initial_observations": offset,
                               "training_block_totals": totals, "forecast": forecast}, None
    profile = demand_service.demand_profile(SimpleNamespace(values=values, granularity="daily",
                                                            time_unit="operational_day"))
    if profile["positive_count"] < 2:
        return [], None, None, "insufficient_positive_events"
    if profile["zero_rate_segment"] == "extremely_intermittent":
        return [], None, None, "extreme_intermittency"
    blocks = demand_service.complete_blocks(values, horizon)
    split = max(demand_service.MIN_FIT_BLOCKS, len(blocks) // 2)
    fit, calibration = blocks[:split], blocks[split:]
    positive = [total for _, _, total in fit if total > 0]
    if len(fit) < demand_service.MIN_FIT_BLOCKS:
        return [], None, None, "insufficient_horizon_blocks"
    if len(positive) < demand_service.MIN_POSITIVE_BLOCKS:
        return [], None, None, "insufficient_positive_blocks"
    probability, magnitude = len(positive) / len(fit), mean(positive)
    expected = probability * magnitude
    two_part = {"available": True, "horizon": horizon, "horizon_unit": "operational_day",
                "occurrence_probability": probability, "positive_demand_expectation": magnitude,
                "unconditional_expected_quantity": expected, "unavailable_reason": None}
    uncertainty = _uncertainty("insufficient_independent_calibration", len(calibration))
    uncertainty["method"] = demand_service.INTERVAL_METHOD
    if len(calibration) >= MIN_CALIBRATION:
        residuals = sorted(abs(total - expected) for _, _, total in calibration)
        radius = residuals[ceil((len(calibration) + 1) * NOMINAL_COVERAGE) - 1]
        uncertainty.update(available=True, lower_bound=max(0.0, expected - radius),
                           upper_bound=expected + radius, unavailable_reason=None)
    return [expected / horizon] * horizon, two_part, {"uncertainty": uncertainty}, None


def _point(model, values, starts, horizon, time_unit, configuration):
    if model in ("two_part", "adida"):
        return _special(model, values, starts, horizon, time_unit, configuration)
    if model in ("croston", "sba", "tsb"):
        if sum(value > 0 for value in values) < 2:
            return [], None, None, "insufficient_positive_events"
        path = _intermittent(model, values, horizon, configuration["alpha"], configuration["beta"])
    else:
        path = _baseline(model, values, horizon)
    return path, None, None, None


def _interval(model, values, starts, horizon, time_unit, configuration, total):
    """Every residual uses a forecast preceding its distinct training target block.

    Bounds are a rolling empirical heuristic. Dependence, drift and refitting
    prevent an automatic finite-sample coverage guarantee; evaluation measures it.
    """
    residuals = []
    earliest = max(MIN_TRAIN, len(values) - MAX_CALIBRATION * horizon)
    for origin in range(earliest, len(values) - horizon + 1, horizon):
        forecast, _, _, reason = _point(model, values[:origin], starts[:origin], horizon,
                                        time_unit, configuration)
        if not reason:
            residuals.append(abs(fsum(values[origin:origin + horizon]) - fsum(forecast)))
    count = len(residuals)
    result = _uncertainty("insufficient_chronological_calibration", count)
    if count >= MIN_CALIBRATION:
        rank = ceil((count + 1) * NOMINAL_COVERAGE)
        radius = sorted(residuals)[rank - 1]
        result.update(available=True, lower_bound=max(0.0, total - radius),
                      upper_bound=total + radius, unavailable_reason=None)
    return result


def forecast_candidate(model, train, dates, horizon, *, time_unit="calendar_day",
                       aggregation_size=7, alpha=0.1, beta=0.1, with_uncertainty=True):
    """Fixed candidate; train and dates contain exclusively pre-origin history.

    Operational cadence is explicit; its additive two-part/ADIDA index extension
    does not alter the existing calendar-only services or claim calendar days.
    """
    if model not in MODELS:
        raise ValueError("Unknown experimental candidate")
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
        raise ValueError("A positive integer horizon is required")
    if time_unit not in ("calendar_day", "operational_day"):
        raise ValueError("An explicit supported time unit is required")
    if not (0 < alpha <= 1 and 0 < beta <= 1):
        raise ValueError("alpha and beta must be in (0, 1]")
    configuration = {"alpha": alpha, "beta": beta, "moving_window": 7,
                     "aggregation_size": aggregation_size, "configuration_selected_on_targets": False,
                     "cadence_policy": "explicit_operational_calendar_or_synthetic_scenario"
                     if time_unit == "operational_day" else "contiguous_observed_or_explicit_synthetic_calendar"}
    result = _empty(model, horizon, time_unit, configuration)
    try:
        values = _values(train)
        starts = _dates(dates, len(values))
    except (TypeError, ValueError, OverflowError):
        return _blocked(result, "invalid_history")
    result["training_count"] = len(values)
    if len(values) < MIN_TRAIN:
        return _blocked(result, "insufficient_training")
    if time_unit == "calendar_day" and not _continuous(starts):
        return _blocked(result, "unobserved_calendar_gap")
    try:
        return _complete(result, values, starts, horizon, time_unit, configuration, with_uncertainty)
    except (OverflowError, ValueError, IndexError):
        return _blocked(result, "numeric_or_date_range")


def _complete(result, values, starts, horizon, time_unit, configuration, with_uncertainty):
    model = result["model"]
    forecast, two_part, special, reason = _point(model, values, starts, horizon, time_unit, configuration)
    if reason:
        return _blocked(result, reason)
    total = fsum(forecast)
    if not all(isfinite(value) for value in forecast + [total]):
        return _blocked(result, "numeric_range")
    result.update(available=True, forecast=forecast, forecast_total=total, two_part=two_part)
    if model == "adida":
        result["adida"] = special
    uncertainty = (special["uncertainty"] if model == "two_part" else
                   _interval(model, values, starts, horizon, time_unit, configuration, total)
                   if with_uncertainty else _uncertainty("not_requested"))
    result["uncertainty"] = uncertainty
    reasons = [] if uncertainty["available"] else [uncertainty["unavailable_reason"]]
    if sum(value > 0 for value in values) < 2:
        reasons.append("insufficient_positive_events")
    result["policy"] = {"status": "limited" if reasons else "available", "reason": reasons}
    result["prediction_policy"] = result["policy"]
    return result


def _training_scales(train, dates, m, time_unit):
    if dates is None:
        errors = [train[index] - train[index - m] for index in range(m, len(train))]
    else:
        starts = _dates(dates, len(train))
        errors = []
        for index in range(m, len(train)):
            block = starts[index - m:index + 1]
            same_year = all(left.year == right.year for left, right in zip(block, block[1:]))
            adjacent = same_year and (_continuous(block) if time_unit == "calendar_day" else True)
            if adjacent:
                errors.append(train[index] - train[index - m])
    if not errors:
        return None, None, 0
    return mean(abs(error) for error in errors), mean(error * error for error in errors), len(errors)


def score_forecast(train, actual, predicted, *, training_dates=None, m=1, time_unit="calendar_day"):
    """Daily/operational metrics; scale only within explicit training segments.

    Bias is mean(predicted - actual). WAPE is percent. MASE/RMSSE never scale
    on targets. A zero training scale is unavailable without an epsilon.
    """
    train, actual, predicted = _values(train), _values(actual), _values(predicted)
    if len(actual) != len(predicted) or not actual:
        raise ValueError("Nonempty equal-length targets and forecasts are required")
    if isinstance(m, bool) or not isinstance(m, int) or m < 1:
        raise ValueError("m must be a positive integer")
    if time_unit not in ("calendar_day", "operational_day"):
        raise ValueError("Unsupported scale time unit")
    errors = [forecast - observed for observed, forecast in zip(actual, predicted)]
    absolute, squared = mean(abs(error) for error in errors), mean(error * error for error in errors)
    absolute_total = fsum(abs(error) for error in errors)
    total = fsum(actual)
    abs_scale, sq_scale, count = _training_scales(train, training_dates, m, time_unit)
    # Segment scales are pooled over valid training differences only. With no
    # dates supplied, reuse the separately tested continuous-sequence service.
    reason = "insufficient_training_scale" if not count else "zero_training_scale"
    mase = absolute / abs_scale if abs_scale else None
    rmsse = sqrt(squared) / sqrt(sq_scale) if sq_scale else None
    result = {"n": len(actual), "mae": absolute, "rmse": sqrt(squared), "bias": mean(errors),
              "wape": absolute_total / total * 100 if total else None,
              "mase": mase, "rmsse": rmsse, "actual_total": total,
              "absolute_error_total": absolute_total, "squared_error_total": squared * len(actual),
              "signed_error_total": fsum(errors), "scale_source": "training_only",
              "scale_pair_count": count, "scale_m": m,
              "mase_denominator": abs_scale, "rmsse_denominator": sq_scale,
              "unavailable_reasons": {"wape": None if total else "zero_target_demand",
                                      "mase": None if mase is not None else reason,
                                      "rmsse": None if rmsse is not None else reason}}
    if training_dates is None:
        scaled = scaled_error_service.compute(train, actual, predicted, m=m)
        result["mase"], result["rmsse"] = scaled["mase"]["value"], scaled["rmsse"]["value"]
    if any(isinstance(value, float) and not isfinite(value) for value in result.values()):
        raise ValueError("Numeric range exceeded while scoring")
    return result
