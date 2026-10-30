"""Opt-in operational-day evaluation using the application's model registry.

No database, file loading, cohort selection, tuning, fallback or stock decisions.
Origins are zero-based positions of the FIRST TARGET in the shared operational
calendar, never product-local indexes. origin_date is the last observed calendar
date. Index 60 therefore uses at most the first 60 observations as TRAIN.
7/7 and 30/30, 60 observations/12 positives and 3 publication folds are predeclared
methodological/technical decisions, not observed lead times or sufficiency proofs.
"""
from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

from app.services import forecast_service as forecast, predictive_metrics_service as metrics
from app.services.demand_data_service import validate_operational_calendar
from app.services.predictive_data_service import TimeSeries

PRIMARY_HORIZON = 7
SECONDARY_HORIZON = 30
MIN_TRAIN = 60
MIN_POSITIVE = 12
MIN_COMMON_FOLDS = 3
SUCCESS = "SUCCESS"
INELIGIBLE = "INELIGIBLE"
MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
ERROR = "ERROR"


def train_only_eligibility(train: Sequence[float]) -> str | None:
    """Return a stable reason or None; this API cannot inspect test or product state."""
    if any(not math.isfinite(v) or v < 0 for v in train):
        return "invalid_train_values"
    if len(train) < MIN_TRAIN:
        return "insufficient_train_observations"
    if sum(v > 0 for v in train) < MIN_POSITIVE:
        return "insufficient_train_positive_observations"
    return None


def operational_origins(calendar, horizon=PRIMARY_HORIZON, step=None, initial_origin=MIN_TRAIN):
    """A shared complete-test grid, independent of observed product quantities."""
    days = validate_operational_calendar(calendar)
    step = horizon if step is None else step
    if (type(horizon) is not int or horizon <= 0 or type(step) is not int
            or step < horizon or type(initial_origin) is not int or initial_origin < 1):
        raise ValueError("Positive horizon and non-overlapping operational origins required")
    return list(range(initial_origin, len(days) - horizon + 1, step))


@dataclass
class EmpiricalFold:
    product_id: int
    product_code: str | None
    is_service: bool
    model: str
    origin: int
    origin_date: date
    train_size: int
    positives_train: int
    horizon: int
    target_dates: list[date]
    actual: list[float]
    predicted: list[float] = field(default_factory=list)
    status: str = INELIGIBLE
    reason: str | None = None
    metrics: metrics.MetricSet | None = None
    train_eligible: bool = False


@dataclass
class EmpiricalEvaluation:
    product_id: int
    horizon: int
    models: tuple[str, ...]
    origins: list[int]
    rows: list[EmpiricalFold]

    @property
    def common_origins(self) -> list[int]:
        return [origin for origin in self.origins if all(
            any(row.origin == origin and row.model == model and row.status == SUCCESS
                for row in self.rows) for model in self.models)]

    @property
    def valid_common_folds(self) -> int:
        return len(self.common_origins)

    @property
    def individual_comparison_publishable(self) -> bool:
        return self.valid_common_folds >= MIN_COMMON_FOLDS

    def common_metrics(self, model: str) -> metrics.MetricSet:
        if model not in self.models:
            raise ValueError("Model is outside this comparison")
        common = set(self.common_origins)
        rows = [row for row in self.rows if row.model == model and row.origin in common]
        return metrics.compute([v for row in rows for v in row.actual],
                               [v for row in rows for v in row.predicted])

    def coverage(self, model: str) -> dict:
        if model not in self.models:
            raise ValueError("Model is outside this comparison")
        rows = [row for row in self.rows if row.model == model]
        counts = {status: sum(row.status == status for row in rows)
                  for status in (SUCCESS, INELIGIBLE, MODEL_UNAVAILABLE, ERROR)}
        attempted = sum(row.train_eligible for row in rows)
        eligible_failures = sum(row.train_eligible and row.status in (MODEL_UNAVAILABLE, ERROR)
                                for row in rows)
        total = len(rows)
        return {"panel_folds": total, "counts": counts, "eligible_folds": attempted,
                "valid_common_folds": self.valid_common_folds,
                "eligibility_rate": attempted / total if total else None,
                "availability_rate": counts[SUCCESS] / total if total else None,
                "failure_rate": (counts[MODEL_UNAVAILABLE] + counts[ERROR]) / total if total else None,
                "failure_rate_given_eligible": eligible_failures / attempted
                if attempted else None}


def _validate_structure(series, calendar):
    if series.time_unit != "operational_day" or series.granularity != "daily":
        raise ValueError("Empirical evaluation requires an explicit operational-day series")
    if len(series.starts) != len(series.values):
        raise ValueError("Dates and observations must have equal lengths")
    starts = validate_operational_calendar(series.starts)
    if starts:
        offset = bisect_left(calendar, starts[0])
        if calendar[offset:offset + len(starts)] != starts:
            raise ValueError("Series must be a contiguous suffix segment of the shared calendar")


def _run_row(row, train, starts):
    reason = train_only_eligibility(train)
    if reason:
        row.reason = reason
        row.status = ERROR if reason == "invalid_train_values" else INELIGIBLE
        return
    if not forecast.model_eligible(train, row.model, "daily", starts, operational=True):
        row.reason = "model_train_requirements"
        return
    row.train_eligible = True
    if len(row.actual) != row.horizon:
        row.reason = "incomplete_observed_test"
        return
    if any(not math.isfinite(v) or v < 0 for v in row.actual):
        row.status, row.reason = ERROR, "invalid_test_values"
        return
    try:
        predicted = forecast.predict(train, row.horizon, row.model, "daily", starts=starts,
                                     operational=True, future_starts=row.target_dates)
        if len(predicted) != row.horizon or any(not math.isfinite(v) or v < 0 for v in predicted):
            row.status, row.reason = ERROR, "invalid_prediction_values"
            return
        measured = metrics.compute(row.actual, predicted)
        if any(v is not None and not math.isfinite(v)
               for v in (measured.mae, measured.rmse, measured.bias, measured.wape, measured.mape)):
            row.status, row.reason = ERROR, "nonfinite_metrics"
            return
        row.predicted, row.metrics, row.status = list(predicted), measured, SUCCESS
    except forecast.ModelUnavailable:
        row.status, row.reason = MODEL_UNAVAILABLE, "model_unavailable"
    except Exception:
        # Boundary ledger: preserve failures without leaking internal exception text.
        # KeyboardInterrupt/SystemExit are not swallowed; no zero or fallback is fabricated.
        row.status, row.reason = ERROR, "model_execution_error"


def evaluate_operational(series: TimeSeries, *, operational_calendar, horizon=PRIMARY_HORIZON,
                         step=None, initial_origin=MIN_TRAIN, explicit_origins=None,
                         candidates=None, product_code=None) -> EmpiricalEvaluation:
    """Pure-model expanding validation; defaults do not affect LIVE eligibility.

    The caller supplies the cohort and shared calendar. Finite-value checks are
    fold-local, so corruption or altered demand after a fold cannot gate that fold.
    All requested cells survive in rows; only all-model SUCCESS cells are compared.
    """
    calendar = validate_operational_calendar(operational_calendar)
    generated = operational_origins(calendar, horizon, step, initial_origin)
    selected = generated if explicit_origins is None else list(explicit_origins)
    if (any(type(o) is not int or o < 1 or o + horizon > len(calendar) for o in selected)
            or any(b - a < horizon for a, b in zip(selected, selected[1:]))):
        raise ValueError("Origins must be ordered, unique, complete and non-overlapping")
    models = tuple(forecast.MODELS) if candidates is None else tuple(candidates)
    if not models or len(set(models)) != len(models) or any(m not in forecast.MODELS for m in models):
        raise ValueError("Comparison requires distinct registered models")
    _validate_structure(series, calendar)
    rows = []
    for origin in selected:
        targets = calendar[origin:origin + horizon]
        local = bisect_left(series.starts, targets[0])
        train, starts = series.values[:local], series.starts[:local]
        actual = series.values[local:local + horizon]
        if series.starts[local:local + horizon] != targets:
            actual = []  # Left-censored or incomplete, never invented zeros.
        for model in models:
            row = EmpiricalFold(series.product_id, product_code, series.is_service, model,
                                origin, calendar[origin - 1], len(train), sum(v > 0 for v in train),
                                horizon, list(targets), list(actual))
            _run_row(row, list(train), list(starts))
            rows.append(row)
    return EmpiricalEvaluation(series.product_id, horizon, models, selected, rows)


def pooled_common_metrics(evaluations, model):
    """Pool pairs, not fold percentages; caller must separate units/cohorts/horizons.

    Products with fewer than three common folds are retained in this aggregate.
    """
    actual, predicted = [], []
    signature = None
    for evaluation in evaluations:
        current = (evaluation.horizon, evaluation.models)
        if signature is not None and current != signature:
            raise ValueError("Pooling requires the same horizon and declared model panel")
        signature = current
        if model not in evaluation.models:
            raise ValueError("Model is outside this comparison")
        common = set(evaluation.common_origins)
        for row in evaluation.rows:
            if row.model == model and row.origin in common:
                actual.extend(row.actual)
                predicted.extend(row.predicted)
    return metrics.compute(actual, predicted)
