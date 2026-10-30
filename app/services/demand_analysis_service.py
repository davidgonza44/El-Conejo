"""Estadísticos descriptivos y patrón de demanda (sin pronóstico).

No calcula MAE, RMSE, WAPE, MASE ni sMAPE: no existe un modelo ni una
serie prevista en esta fase.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.services.demand_data_service import ProductDemandSeries

INSTITUTIONAL_TZ = "America/Caracas"

PATTERN_CONTINUOUS = "continuous"
PATTERN_INTERMITTENT = "intermittent"
PATTERN_SPARSE = "sparse"
PATTERN_NO_HISTORY = "no_history"

# Umbrales internos del patrón descriptivo (no seleccionan modelo).
SPARSE_ZERO_RATIO = 0.80
INTERMITTENT_ZERO_RATIO = 0.50
INTERMITTENT_AVERAGE_INTERVAL = 1.32

PATTERN_LABELS = {
    PATTERN_CONTINUOUS: "Continua",
    PATTERN_INTERMITTENT: "Intermitente",
    PATTERN_SPARSE: "Dispersa",
    PATTERN_NO_HISTORY: "Sin historial",
}


def institutional_today() -> date:
    try:
        return datetime.now(ZoneInfo(INSTITUTIONAL_TZ)).date()
    except Exception:
        return datetime.utcnow().date()


@dataclass(frozen=True)
class DescriptiveStats:
    start_date: date | None
    end_date: date | None
    days_covered: int
    periods: int
    positive_periods: int
    zero_periods: int
    zero_ratio: float | None
    total_demand: Decimal
    average_daily_demand: float | None
    median: float | None
    standard_deviation: float | None
    min_demand: Decimal | None
    max_demand: Decimal | None
    last_date_with_demand: date | None
    days_since_last_demand: int | None
    original_event_count: int
    data_source: str | None
    average_interval_between_positive_demand: float | None
    coefficient_of_variation: float | None
    demand_pattern: str
    demand_pattern_reason: str


def compute_descriptive_stats(
    series: ProductDemandSeries,
    *,
    today: date | None = None,
) -> DescriptiveStats:
    today = today or institutional_today()
    points = series.points
    periods = len(points)
    values = [float(point.demand) for point in points]
    positive_dates = [point.day for point in points if point.demand > 0]
    zero_periods = sum(1 for point in points if point.demand == 0)
    positive_periods = len(positive_dates)
    total = sum((point.demand for point in points), Decimal("0"))

    zero_ratio = round(zero_periods / periods, 6) if periods else None
    average = (float(total) / periods) if periods else None
    median = statistics.median(values) if values else None
    stddev = statistics.pstdev(values) if values else None
    min_demand = min((point.demand for point in points), default=None)
    max_demand = max((point.demand for point in points), default=None)
    last_positive = positive_dates[-1] if positive_dates else None
    days_since = (today - last_positive).days if last_positive else None
    avg_interval = _average_interval(positive_dates)
    cv = None
    if stddev is not None and average is not None and average > 0:
        cv = stddev / average

    pattern, pattern_reason = classify_demand_pattern(
        positive_periods=positive_periods,
        zero_ratio=zero_ratio,
        average_interval=avg_interval,
    )

    return DescriptiveStats(
        start_date=series.start_date,
        end_date=series.end_date,
        days_covered=periods,
        periods=periods,
        positive_periods=positive_periods,
        zero_periods=zero_periods,
        zero_ratio=zero_ratio,
        total_demand=total,
        average_daily_demand=average,
        median=median,
        standard_deviation=stddev,
        min_demand=min_demand,
        max_demand=max_demand,
        last_date_with_demand=last_positive,
        days_since_last_demand=days_since,
        original_event_count=series.original_event_count,
        data_source=series.data_source,
        average_interval_between_positive_demand=avg_interval,
        coefficient_of_variation=cv,
        demand_pattern=pattern,
        demand_pattern_reason=pattern_reason,
    )


def classify_demand_pattern(
    *,
    positive_periods: int,
    zero_ratio: float | None,
    average_interval: float | None,
) -> tuple[str, str]:
    """Clasifica el patrón observado. No elige Croston ni ningún modelo."""
    if positive_periods <= 0:
        return (
            PATTERN_NO_HISTORY,
            "No hay días con demanda positiva en la serie diaria.",
        )
    if zero_ratio is not None and zero_ratio >= SPARSE_ZERO_RATIO:
        return (
            PATTERN_SPARSE,
            (
                f"La proporción de ceros es {zero_ratio:.2%} "
                f"(umbral de dispersión ≥ {SPARSE_ZERO_RATIO:.0%})."
            ),
        )
    intermittent_by_zeros = (
        zero_ratio is not None and zero_ratio >= INTERMITTENT_ZERO_RATIO
    )
    intermittent_by_interval = (
        average_interval is not None
        and average_interval > INTERMITTENT_AVERAGE_INTERVAL
    )
    if intermittent_by_zeros or intermittent_by_interval:
        return (
            PATTERN_INTERMITTENT,
            (
                "La serie tiene huecos frecuentes "
                f"(ceros ≥ {INTERMITTENT_ZERO_RATIO:.0%} o intervalo medio "
                f"> {INTERMITTENT_AVERAGE_INTERVAL})."
            ),
        )
    return (
        PATTERN_CONTINUOUS,
        "La demanda positiva cubre la mayoría de los días de la serie.",
    )


def _average_interval(positive_dates: list[date]) -> float | None:
    if len(positive_dates) < 2:
        return None
    gaps = [
        (positive_dates[index] - positive_dates[index - 1]).days
        for index in range(1, len(positive_dates))
    ]
    return sum(gaps) / len(gaps)
