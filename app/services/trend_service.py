"""Tendencias sobre demanda OBSERVADA (no pronóstico).

Compara el período actual con el período anterior de igual duración, calcula la
variación porcentual y la pendiente por mínimos cuadrados, y clasifica UP / DOWN /
STABLE con umbrales centralizados en ``TrendConfig``. Las "observaciones" son patrones
verificables en la propia serie (crecimiento sostenido, caída reciente, alta variabilidad,
pico reciente, intermitencia); nunca causas externas que los datos no contienen.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.services.predictive_data_service import TimeSeries, window_sum

TREND_UP = "up"
TREND_DOWN = "down"
TREND_STABLE = "stable"
TREND_INSUFFICIENT = "insufficient"
TREND_LABELS = {TREND_UP: "Al alza", TREND_DOWN: "A la baja", TREND_STABLE: "Estable",
                TREND_INSUFFICIENT: "Sin historial suficiente"}
TREND_TONES = {TREND_UP: "green", TREND_DOWN: "red", TREND_STABLE: "gray", TREND_INSUFFICIENT: "gray"}


@dataclass(frozen=True)
class TrendConfig:
    period_days: int = 30
    stable_tolerance_pct: float = 5.0   # |variación| ≤ tolerancia => estable
    min_periods_with_demand: int = 2    # menos que esto en ambos períodos => insuficiente
    high_cv: float = 1.0                # coeficiente de variación alto en la ventana actual
    spike_ratio: float = 2.0            # máximo reciente ≥ ratio × media => pico
    intermittent_zero_ratio: float = 0.8


DEFAULT_CONFIG = TrendConfig()


@dataclass
class TrendResult:
    status: str
    current: float
    previous: float
    variation_pct: float | None       # None cuando el período anterior fue 0
    slope: float                      # unidades por período dentro de la ventana actual
    observations: list[str] = field(default_factory=list)
    spark: list[float] = field(default_factory=list)  # valores de la ventana actual

    @property
    def label(self) -> str:
        return TREND_LABELS[self.status]

    @property
    def tone(self) -> str:
        return TREND_TONES[self.status]

    @property
    def primary_observation(self) -> str:
        return self.observations[0] if self.observations else "Sin observaciones"


def _slope(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    sxx = sum((i - x_mean) ** 2 for i in range(n))
    return sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values)) / sxx if sxx else 0.0


def _window_values(series: TimeSeries, days: int, end: date) -> list[float]:
    lower = end - timedelta(days=days)
    return [v for s, v in zip(series.starts, series.values) if lower < s <= end]


def classify(current: float, previous: float, config: TrendConfig = DEFAULT_CONFIG) -> tuple[str, float | None]:
    """Determinista: (estado, variación %). Sin base anterior, cualquier demanda actual es alza."""
    if previous == 0 and current == 0:
        return TREND_STABLE, 0.0
    if previous == 0:
        return TREND_UP, None
    variation = (current - previous) / previous * 100.0
    if abs(variation) <= config.stable_tolerance_pct:
        return TREND_STABLE, variation
    return (TREND_UP if variation > 0 else TREND_DOWN), variation


def analyze(series: TimeSeries, today: date, config: TrendConfig = DEFAULT_CONFIG) -> TrendResult:
    current = window_sum(series, config.period_days, today)
    previous = window_sum(series, config.period_days, today, offset_days=config.period_days)
    window = _window_values(series, config.period_days, today)
    positive_now = sum(1 for v in window if v > 0)
    previous_window = _window_values(series, config.period_days, today - timedelta(days=config.period_days))
    positive_prev = sum(1 for v in previous_window if v > 0)
    slope = _slope(window)
    if positive_now + positive_prev < config.min_periods_with_demand:
        return TrendResult(TREND_INSUFFICIENT, current, previous, None, slope,
                           ["Historial insuficiente en el período analizado"], window)
    status, variation = classify(current, previous, config)
    return TrendResult(status, current, previous, variation, slope,
                       observations(status, window, slope, config), window)


def observations(status: str, window: list[float], slope: float, config: TrendConfig = DEFAULT_CONFIG) -> list[str]:
    """Patrones detectables en la serie; el primero es el principal."""
    notes: list[str] = []
    total = sum(window)
    if status == TREND_UP:
        notes.append("Crecimiento sostenido" if slope > 0 else "Mayor demanda que el período anterior")
    elif status == TREND_DOWN:
        notes.append("Caída reciente" if slope < 0 else "Menor demanda que el período anterior")
    else:
        notes.append("Comportamiento estable" if total > 0 else "Sin demanda en el período")
    if window and total > 0:
        mean = total / len(window)
        cv = math.sqrt(sum((v - mean) ** 2 for v in window) / len(window)) / mean
        zeros = sum(1 for v in window if v == 0) / len(window)
        recent = window[-7:]
        if zeros >= config.intermittent_zero_ratio:
            notes.append("Demanda intermitente")
        elif cv >= config.high_cv:
            notes.append("Alta variabilidad")
        if max(recent) >= config.spike_ratio * mean and max(recent) > 0 and mean > 0:
            notes.append("Pico reciente")
    return notes


def category_evolution(series_by_product: dict[int, TimeSeries], category_of: dict[int, str], today: date,
                       config: TrendConfig = DEFAULT_CONFIG, top: int = 5) -> dict:
    """Demanda diaria por categoría en la ventana actual (top categorías por volumen)."""
    days = [today - timedelta(days=config.period_days - 1 - i) for i in range(config.period_days)]
    index = {day: i for i, day in enumerate(days)}
    totals: dict[str, list[float]] = {}
    for product_id, series in series_by_product.items():
        category = category_of.get(product_id) or "Sin categoría"
        bucket = totals.setdefault(category, [0.0] * len(days))
        for start, value in zip(series.starts, series.values):
            if start in index:
                bucket[index[start]] += value
    ranked = sorted(totals.items(), key=lambda item: -sum(item[1]))[:top]
    return {"labels": [d.strftime("%d/%m") for d in days],
            "series": [{"label": name, "values": values} for name, values in ranked if sum(values) > 0]}
