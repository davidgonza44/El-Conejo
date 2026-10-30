"""Registro único de modelos de pronóstico y contrato de salida.

Los baselines conservan ``(history, horizon, granularity) -> list[float]``;
``predict`` admite además ``starts`` opcional para el calendario de Random Forest.
Cada modelo solo ve el pasado que recibe. El baseline es la
media móvil; ningún modelo queda "seleccionado" de forma definitiva: ``backtest_service``
los compara con datos reales cuando existan.

``forecast()`` produce el pronóstico para un horizonte en días (7/30/90) y, si recibe la
desviación de los residuos del backtest, un intervalo ±z·σ etiquetado como tal. Sin
residuos no hay intervalo (la UI lo oculta), nunca un ± inventado.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Callable, Sequence

from app.services.predictive_data_service import GRANULARITY_DAILY, PERIOD_DAYS, TimeSeries
from app.services import forecast_advanced_service as advanced
from app.services.forecast_advanced_service import ModelUnavailable

ModelFn = Callable[..., list[float]]

HORIZONS_DAYS = (7, 30, 90)
DEFAULT_HORIZON_DAYS = 30
WINDOW = {GRANULARITY_DAILY: 7, "weekly": 4}
Z_80 = 1.2816  # intervalo ~80 % bajo residuos aproximadamente normales


def _clip(values: list[float]) -> list[float]:
    if any(not math.isfinite(v) for v in values):
        raise ModelUnavailable("Model produced nonfinite predictions")
    return [max(0.0, v) for v in values]


def naive(history: Sequence[float], horizon: int, _granularity: str) -> list[float]:
    last = float(history[-1]) if history else 0.0
    return [last] * horizon


def historical_mean(history: Sequence[float], horizon: int, _granularity: str) -> list[float]:
    mean = sum(history) / len(history) if history else 0.0
    return [mean] * horizon


def moving_average(history: Sequence[float], horizon: int, granularity: str) -> list[float]:
    window = WINDOW.get(granularity, 7)
    tail = list(history[-window:]) if history else []
    mean = sum(tail) / len(tail) if tail else 0.0
    return [mean] * horizon


def linear_trend(history: Sequence[float], horizon: int, _granularity: str) -> list[float]:
    """Mínimos cuadrados sobre el índice temporal; con < 3 puntos degrada a la media."""
    n = len(history)
    if n < 3:
        return historical_mean(history, horizon, _granularity)
    xs = range(n)
    x_mean = (n - 1) / 2.0
    y_mean = sum(history) / n
    sxx = sum((x - x_mean) ** 2 for x in xs)
    sxy = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, history))
    slope = sxy / sxx if sxx else 0.0
    intercept = y_mean - slope * x_mean
    return _clip([intercept + slope * (n + k) for k in range(horizon)])


MODELS: dict[str, ModelFn] = {
    "naive": naive,
    "mean": historical_mean,
    "moving_average": moving_average,
    "linear_trend": linear_trend,
    "arima": advanced.arima,
    "random_forest": advanced.random_forest,
}
MODEL_LABELS = {
    "naive": "Último valor (naive)",
    "mean": "Media histórica",
    "moving_average": "Media móvil (baseline)",
    "linear_trend": "Tendencia lineal",
    "arima": "ARIMA",
    "random_forest": "Random Forest",
}
BASELINE_MODEL = "moving_average"
AUTO_MODEL = "auto"  # el mejor WAPE del backtest por producto; sin backtest cae al baseline


def model_eligible(history: Sequence[float], model: str, granularity: str,
                   starts: Sequence[date] | None = None, *, operational: bool = False) -> bool:
    if model not in MODELS:
        raise ValueError(f"Modelo desconocido: {model}")
    return model not in advanced.MODEL_REQUIREMENTS or advanced.eligible(history, model, granularity, starts, operational=operational)


def minimum_train(model: str, granularity: str, baseline_min: int) -> int:
    if model in advanced.MODEL_REQUIREMENTS:
        return max(baseline_min, advanced.minimum_train(model, granularity))
    return baseline_min


def predict(history: Sequence[float], periods: int, model: str, granularity: str = GRANULARITY_DAILY,
            starts: Sequence[date] | None = None, *, operational: bool = False,
            future_starts: Sequence[date] | None = None, fit_cache=None) -> list[float]:
    if model not in MODELS:
        raise ValueError(f"Modelo desconocido: {model}")
    if periods <= 0:
        return []
    if model == "random_forest" and operational:
        return MODELS[model](list(history), periods, granularity, starts=starts,
                             operational=True, future_starts=future_starts)
    if model in advanced.MODEL_REQUIREMENTS:
        options = {"fit_cache": fit_cache} if fit_cache is not None else {}
        return MODELS[model](list(history), periods, granularity, starts=starts, **options)
    return _clip(MODELS[model](list(history), periods, granularity))


def periods_for(horizon_days: int, granularity: str) -> tuple[int, float]:
    """(períodos completos a pronosticar, fracción del último). 30 días semanales = 4 + 2/7."""
    days = PERIOD_DAYS[granularity]
    whole, remainder = divmod(horizon_days, days)
    return whole, remainder / days


@dataclass(frozen=True)
class Forecast:
    model: str
    horizon_days: int
    total: float                 # demanda estimada en el horizonte
    daily_rate: float            # demanda esperada por día
    path: list[float]            # pronóstico por período
    half_width: float | None     # ± del total (z·σ de residuos del backtest) o None
    kind: str                    # "model" o "descriptive"
    rate_unit: str = "calendar_day"

    @property
    def model_label(self) -> str:
        return MODEL_LABELS.get(self.model, self.model)


def forecast(series: TimeSeries, horizon_days: int, model: str = BASELINE_MODEL,
             residual_std: float | None = None, kind: str = "model", *,
             future_starts: Sequence[date] | None = None, predictor=None) -> Forecast:
    whole, fraction = periods_for(horizon_days, series.granularity)
    periods = whole + (1 if fraction else 0)
    kwargs = {}
    if series.time_unit == "operational_day":
        if not advanced.valid_starts(future_starts, periods, "daily", operational=True):
            raise ModelUnavailable("Explicit future operational dates are required.")
        if not series.starts or future_starts[0] <= series.starts[-1]:
            raise ModelUnavailable("Future operational dates must follow training.")
        kwargs = {"operational": True, "future_starts": future_starts}
    path = (predictor or predict)(series.values, periods, model, series.granularity, starts=series.starts, **kwargs)
    total = sum(path[:whole]) + (path[whole] * fraction if fraction and len(path) > whole else 0.0)
    half_width = Z_80 * residual_std if residual_std is not None and residual_std > 0 else None
    return Forecast(model=model, horizon_days=horizon_days, total=total, daily_rate=total / horizon_days,
                    path=path, half_width=half_width, kind=kind, rate_unit=series.time_unit)


def descriptive_rate(series: TimeSeries, horizon_days: int) -> Forecast:
    """Regla descriptiva para historial limitado/intermitente: tasa media observada, sin intervalo."""
    per_period = sum(series.values) / series.periods if series.periods else 0.0
    daily = per_period / series.period_days
    whole, fraction = periods_for(horizon_days, series.granularity)
    path = [per_period] * (whole + (1 if fraction else 0))
    return Forecast(model="mean", horizon_days=horizon_days, total=daily * horizon_days, daily_rate=daily,
                    path=path, half_width=None, kind="descriptive", rate_unit=series.time_unit)


def std(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))
