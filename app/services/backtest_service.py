"""Validación temporal (rolling-origin) de los modelos de pronóstico.

Nunca usa train/test aleatorio: en cada pliegue el modelo solo recibe ``values[:origin]``
y se evalúa contra ``values[origin:origin + horizon]``. Los orígenes avanzan en el tiempo
(Ene-Jun→Jul, Ene-Jul→Ago, ...). Todo parámetro dependiente de datos (medias, pendiente,
ventana) se ajusta dentro de cada pliegue con el pasado únicamente.

Salida por producto: pares (real, predicho) fuera de muestra por período, totales por
pliegue (para el intervalo del horizonte) y métricas. ``evaluate_models`` compara los
candidatos y elige el de menor WAPE; sin pliegues suficientes no hay métricas (None).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services import forecast_service, predictive_metrics_service as metrics
from app.services.predictive_data_service import TimeSeries

MAX_FOLDS = 12
MIN_TRAIN_RATIO = 0.5  # el primer origen deja al menos la mitad de la serie (y >= min_train) para entrenar


@dataclass
class Fold:
    origin: int                 # índice del primer período pronosticado
    actual: list[float]
    predicted: list[float]

    @property
    def actual_total(self) -> float:
        return sum(self.actual)

    @property
    def predicted_total(self) -> float:
        return sum(self.predicted)


@dataclass
class BacktestResult:
    model: str
    horizon: int                # períodos
    folds: list[Fold] = field(default_factory=list)

    @property
    def actual(self) -> list[float]:
        return [v for fold in self.folds for v in fold.actual]

    @property
    def predicted(self) -> list[float]:
        return [v for fold in self.folds for v in fold.predicted]

    @property
    def metrics(self) -> metrics.MetricSet:
        return metrics.compute(self.actual, self.predicted)

    @property
    def total_residuals(self) -> list[float]:
        return [fold.predicted_total - fold.actual_total for fold in self.folds]

    @property
    def residual_std(self) -> float | None:
        return forecast_service.std(self.total_residuals)

    @property
    def last_fold(self) -> Fold | None:
        return self.folds[-1] if self.folds else None


def origins(n: int, horizon: int, min_train: int, max_folds: int = MAX_FOLDS) -> list[int]:
    """Orígenes crecientes; el último pliegue termina exactamente en el final de la serie."""
    first = max(min_train, int(n * MIN_TRAIN_RATIO))
    last = n - horizon
    if horizon <= 0 or last < first:
        return []
    count = last - first + 1
    step = max(1, -(-count // max_folds))  # ceil
    result = list(range(last, first - 1, -step))
    return sorted(result)


def rolling_backtest(series: TimeSeries, model: str, horizon: int, min_train: int,
                     fold_origins: list[int] | None = None, *, predictor=None) -> BacktestResult:
    values = series.values
    result = BacktestResult(model=model, horizon=horizon)
    minimum = forecast_service.minimum_train(model, series.granularity, min_train)
    selected = origins(len(values), horizon, minimum) if fold_origins is None else fold_origins
    for origin in selected:
        train = values[:origin]                    # solo pasado
        actual = values[origin:origin + horizon]   # solo futuro relativo al origen
        try:
            predicted = (predictor or forecast_service.predict)(train, horizon, model, series.granularity,
                                                                starts=series.starts[:origin])
        except forecast_service.ModelUnavailable:
            # Un candidato incompleto no compite con métricas sobre un subconjunto favorable.
            return BacktestResult(model=model, horizon=horizon)
        result.folds.append(Fold(origin=origin, actual=list(actual), predicted=predicted[:len(actual)]))
    return result


@dataclass
class ModelEvaluation:
    best: str
    results: dict[str, BacktestResult]

    @property
    def best_result(self) -> BacktestResult:
        return self.results[self.best]

    def ranking(self) -> list[tuple[str, float | None]]:
        rows = [(name, r.metrics.wape) for name, r in self.results.items()]
        return sorted(rows, key=lambda item: (item[1] is None, item[1] if item[1] is not None else 0.0))


def evaluate_models(series: TimeSeries, horizon: int, min_train: int,
                    candidates: tuple[str, ...] | None = None, *, predictor=None) -> ModelEvaluation | None:
    """Compara candidatos con el mismo esquema de pliegues; None si no cabe ni un pliegue."""
    names = tuple(forecast_service.MODELS) if candidates is None else candidates
    minimums = {}
    for name in names:
        first = _first_eligible_origin(series, name, horizon, min_train)
        if first is not None:
            minimums[name] = first
    if not minimums:
        return None
    common_origins = origins(series.periods, horizon, max(minimums.values()))
    results = {}
    for name in minimums:
        result = rolling_backtest(series, name, horizon, min_train, fold_origins=common_origins,
                                  predictor=predictor)
        if result.folds:
            results[name] = result
    if not results:
        return None
    scored = [(name, r.metrics.wape) for name, r in results.items() if r.metrics.wape is not None]
    if scored:
        best = min(scored, key=lambda item: item[1])[0]
    else:
        # WAPE None (denominador 0) no es 0 ni "mejor": cae al baseline, no se rankea.
        best = forecast_service.BASELINE_MODEL if forecast_service.BASELINE_MODEL in results else next(iter(results))
    return ModelEvaluation(best=best, results=results)


def _first_eligible_origin(series: TimeSeries, model: str, horizon: int, min_train: int) -> int | None:
    """El candidato necesita historial y al menos un holdout completo, sin ajustar modelos."""
    if not forecast_service.model_eligible(series.values, model, series.granularity, series.starts):
        return None
    first = forecast_service.minimum_train(model, series.granularity, min_train)
    for origin in range(first, series.periods - horizon + 1):
        if forecast_service.model_eligible(series.values[:origin], model, series.granularity,
                                         series.starts[:origin]):
            return origin
    return None
