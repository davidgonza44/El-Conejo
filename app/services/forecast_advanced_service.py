"""Candidatos avanzados del pipeline existente; ajustes efímeros, solo sobre TRAIN."""
from __future__ import annotations

import math
import logging
import warnings
from datetime import date, timedelta
from typing import Sequence

from sklearn.ensemble import RandomForestRegressor
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tools.sm_exceptions import ConvergenceWarning

from app.services.prediction_readiness_service import ADVANCED_MIN_PERIODS, ADVANCED_MIN_POSITIVE
from app.services.predictive_data_service import PERIOD_DAYS

ARIMA_ORDERS = ((1, 0, 1), (1, 1, 0), (1, 1, 1), (2, 1, 0), (0, 1, 1), (2, 1, 1))
# (mínimo de períodos, mínimo de positivos), separados de elegibilidad del producto.
MODEL_REQUIREMENTS = {
    "arima": {"daily": (ADVANCED_MIN_PERIODS, ADVANCED_MIN_POSITIVE), "weekly": (16, 6)},
    "random_forest": {"daily": (ADVANCED_MIN_PERIODS, ADVANCED_MIN_POSITIVE), "weekly": (20, 6)},
}
RF_LAGS = {"daily": (1, 7, 14, 28), "weekly": (1, 4, 8)}
RF_WINDOW = {"daily": 7, "weekly": 4}
# numpy.linalg.LinAlgError hereda ValueError; no requiere importar NumPy directamente.
NUMERICAL_ERRORS = (ValueError, ArithmeticError)
_LOG = logging.getLogger(__name__)


class ModelUnavailable(Exception):
    """No hay un pronóstico válido; nunca equivale a demanda cero."""


def minimum_train(model: str, granularity: str) -> int:
    return MODEL_REQUIREMENTS[model][granularity][0]


def valid_starts(starts: Sequence[date] | None, size: int, granularity: str,
                 *, operational: bool = False) -> bool:
    if starts is None or len(starts) != size or not size:
        return False
    if not all(type(day) is date for day in starts):
        return False
    if operational:
        return granularity == "daily" and all(a < b for a, b in zip(starts, starts[1:]))
    step = timedelta(days=PERIOD_DAYS[granularity])
    return all(b - a == step for a, b in zip(starts, starts[1:])) and (
        granularity != "weekly" or starts[0].weekday() == 0)


def eligible(history: Sequence[float], model: str, granularity: str,
             starts: Sequence[date] | None = None, *, operational: bool = False) -> bool:
    periods, positive = MODEL_REQUIREMENTS[model][granularity]
    if len(history) < periods or not all(math.isfinite(v) and v >= 0 for v in history):
        return False
    if sum(v > 0 for v in history) < positive:
        return False
    if model == "arima":
        return max(history) > min(history)  # serie constante: baselines suficientes
    return valid_starts(starts, len(history), granularity, operational=operational)


def _validated_path(values, horizon: int) -> list[float]:
    path = [float(v) for v in values]
    if len(path) != horizon or not all(math.isfinite(v) for v in path):
        raise ModelUnavailable("El modelo no produjo un pronóstico finito completo.")
    return [max(0.0, v) for v in path]


def arima(history: Sequence[float], horizon: int, granularity: str,
          starts: Sequence[date] | None = None, *, fit_cache=None) -> list[float]:
    """Modelo estadístico: AIC decide el orden dentro de TRAIN, nunca contra holdout."""
    if not eligible(history, "arima", granularity, starts):
        raise ModelUnavailable("Historial no elegible para ARIMA.")
    best_fit = fit_cache(lambda: _fit_arima(history)) if fit_cache else _fit_arima(history)
    try:
        return _validated_path(best_fit.forecast(steps=horizon), horizon)
    except NUMERICAL_ERRORS as exc:
        raise ModelUnavailable("ARIMA no pudo generar un pronóstico válido.") from exc


def _fit_arima(history):
    """The fitted order depends on TRAIN, not the requested forecast horizon."""
    best_fit, best_aic = None, math.inf
    for order in ARIMA_ORDERS:
        try:
            with warnings.catch_warnings():
                # Solo convergencia se convierte en fallo del orden; otros warnings se conservan.
                warnings.simplefilter("error", ConvergenceWarning)
                fitted = ARIMA(list(history), order=order).fit()
            if not fitted.mle_retvals.get("converged", False):
                _LOG.info("ARIMA candidate excluded: order=%s reason=not_converged", order)
                continue
            if math.isfinite(fitted.aic) and fitted.aic < best_aic:
                best_fit, best_aic = fitted, fitted.aic
        except (*NUMERICAL_ERRORS, ConvergenceWarning) as error:
            _LOG.info("ARIMA candidate excluded: order=%s reason=%s", order, type(error).__name__)
            continue
    if best_fit is None:
        raise ModelUnavailable("Ningún orden ARIMA convergió con AIC finito.")
    return best_fit


def rf_features(history: Sequence[float], target_date: date, granularity: str) -> list[float]:
    """Features para y(t): history termina en t-1, incluso durante recursión."""
    lags, window = RF_LAGS[granularity], RF_WINDOW[granularity]
    features = [float(history[-lag]) for lag in lags]
    features.append(sum(history[-window:]) / window)
    if granularity == "daily":
        features.append(target_date.weekday())
    features.append(target_date.month)
    return features


def supervised_rows(history: Sequence[float], starts: Sequence[date], granularity: str):
    first = max(RF_LAGS[granularity])
    # Only the longest lag/window is needed; avoid copying every growing prefix.
    width = max(first, RF_WINDOW[granularity])
    return ([rf_features(history[max(0, i - width):i], starts[i], granularity) for i in range(first, len(history))],
            list(history[first:]))


def random_forest(history: Sequence[float], horizon: int, granularity: str,
                  starts: Sequence[date] | None = None, *, operational: bool = False,
                  future_starts: Sequence[date] | None = None, fit_cache=None) -> list[float]:
    """Aprendizaje supervisado con lags causales y pronóstico multistep recursivo."""
    if not eligible(history, "random_forest", granularity, starts, operational=operational):
        raise ModelUnavailable("Historial o calendario no elegible para Random Forest.")
    if operational:
        if (not valid_starts(future_starts, horizon, granularity, operational=True)
                or future_starts[0] <= starts[-1]):
            raise ModelUnavailable("Explicit future operational dates are required.")
        future = list(future_starts)
    else:
        future = [starts[-1] + timedelta(days=PERIOD_DAYS[granularity] * i)
                  for i in range(1, horizon + 1)]
    simulated = list(history)
    try:
        fit = lambda: _fit_random_forest(history, starts, granularity)
        estimator = fit_cache(fit) if fit_cache else fit()
        for day in future:
            row = rf_features(simulated, day, granularity)
            prediction = _validated_path(estimator.predict([row]), 1)[0]
            simulated.append(prediction)
    except NUMERICAL_ERRORS as exc:
        raise ModelUnavailable("Random Forest no pudo generar un pronóstico válido.") from exc
    return simulated[len(history):]


def _fit_random_forest(history, starts, granularity):
    features, targets = supervised_rows(history, starts, granularity)
    estimator = RandomForestRegressor(n_estimators=50, max_depth=6, random_state=42, n_jobs=1)
    estimator.fit(features, targets)
    return estimator
