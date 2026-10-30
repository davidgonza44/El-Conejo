"""Additive offline DTOs; no registry, winner, scientific freeze or purchasing side effect."""
from datetime import date, timedelta
from bisect import bisect_left
import math

from . import experimental_adida_service as adida
from . import predictive_metrics_service as legacy_metrics
from . import scaled_error_service as scaled


def _dates(values, count):
    if len(values) != count or any(type(day) is not date for day in values):
        raise ValueError("Explicit dates must match observations")
    if any(b - a != timedelta(days=1) for a, b in zip(values, values[1:])):
        raise ValueError("Calendar-day DTO requires a contiguous observed segment")


def evaluate_forecast(train, actual, predicted, *, training_dates, target_dates, m=1):
    """Keep legacy errors/WAPE and add fold-local scaled errors from explicit TRAIN."""
    _dates(training_dates, len(train))
    _dates(target_dates, len(actual))
    if not training_dates or not target_dates or target_dates[0] - training_dates[-1] != timedelta(days=1):
        raise ValueError("Training must end immediately before targets, with no unobserved gap")
    scaled_metrics = scaled.compute(train, actual, predicted, m=m)
    legacy = legacy_metrics.compute(actual, predicted).to_dict()
    if any(isinstance(value, float) and not math.isfinite(value) for value in legacy.values()):
        raise ValueError("Nonfinite legacy metrics cannot be serialized")
    return {"experimental": True, "scientific_status": "infrastructure_only", "time_unit": "calendar_day",
            "train_end": training_dates[-1].isoformat(), "target_start": target_dates[0].isoformat(),
            "horizon": len(actual), "legacy_metrics": legacy, "scaled_metrics": scaled_metrics}


def evaluate_adida(train, actual, *, training_dates, target_dates, aggregation_size, m=1):
    """Explicit technical candidate call. ACTUAL is used only after forecasting, for errors."""
    _dates(target_dates, len(actual))
    if not target_dates:
        raise ValueError("An explicit observed evaluation horizon is required")
    candidate = adida.adida(train, aggregation_size=aggregation_size, horizon=len(actual),
                            starts=training_dates, forecast_origin=target_dates[0])
    result = {"experimental": True, "scientific_status": "infrastructure_only", "adida": candidate,
              "metrics": None, "selection_performed": False}
    if candidate["available"]:
        result["metrics"] = evaluate_forecast(train, actual, candidate["forecast"],
                                              training_dates=training_dates, target_dates=target_dates, m=m)
    return result


def supplement_empirical_fold(series, row, *, operational_calendar, m=1):
    """Read a successful empirical fold without modifying it or rerunning models.

    An operational calendar is explicit evidence of cadence, not a calendar of
    sales dates. The caller must supply only confirmed observed segments.
    """
    if series.product_id != row.product_id or len(series.starts) != len(series.values):
        raise ValueError("Fold product identity and observation lengths must match the supplied series")
    result = {"experimental": True, "scientific_status": "infrastructure_only",
              "product_id": row.product_id, "model": row.model, "origin": row.origin, "source_status": row.status,
              "legacy_metrics": row.metrics.to_dict() if row.metrics is not None else None,
              "scaled_metrics": None, "unavailable_reason": None}
    if row.status != "SUCCESS":
        result["unavailable_reason"] = {"code": "source_fold_unavailable"}
        return result
    calendar = list(operational_calendar)
    if ((series.time_unit, series.granularity) != ("operational_day", "daily")
            or any(type(day) is not date for day in calendar)
            or any(b <= a for a, b in zip(calendar, calendar[1:]))):
        raise ValueError("An explicit ordered operational calendar is required")
    targets = list(row.target_dates)
    if not targets or len(targets) != row.horizon:
        raise ValueError("A complete fold is required")
    local = bisect_left(series.starts, targets[0])
    starts = list(series.starts[:local])
    scoped = starts + targets
    if (len(starts) != row.train_size or not starts or row.origin_date != starts[-1]
            or list(series.starts[local:local + row.horizon]) != targets):
        raise ValueError("Fold training/target identity does not match supplied series")
    if any(a.year <= 2025 <= b.year for a, b in zip(scoped, scoped[1:])):
        raise ValueError("2025 is unobserved; separate segments cannot share scaling differences")
    offset = bisect_left(calendar, scoped[0])
    if calendar[offset:offset + len(scoped)] != scoped:
        raise ValueError("Fold must cover a contiguous observed operational segment")
    result["time_unit"] = "operational_day"
    result["scaled_metrics"] = scaled.compute(series.values[:local], row.actual, row.predicted, m=m)
    return result
