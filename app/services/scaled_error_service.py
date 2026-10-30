"""Pure experimental MASE/RMSSE metrics; no application or database imports."""

import math
from numbers import Integral, Real


def _reason(code, description, **details):
    return {"code": code, "description": description, **details}


def _unavailable(reason, denominator=None):
    return {
        "available": False,
        "value": None,
        "denominator": denominator,
        "reason": reason,
    }


def _result(m, reason=None):
    return {
        "m": m,
        "benchmark": {
            "method": "naive",
            "lag": m,
            "scale_source": "training_only",
        },
        "mase": _unavailable(reason),
        "rmsse": _unavailable(reason),
    }


def _series(values, field):
    """Copy finite real observations; missing observations are never imputed."""
    if isinstance(values, (str, bytes, dict, set, frozenset)):
        return None, _reason(
            "invalid_series", "Se requiere una secuencia numérica.", field=field
        )
    try:
        observations = list(values)
    except TypeError:
        return None, _reason(
            "invalid_series", "Se requiere una secuencia numérica.", field=field
        )
    for index, value in enumerate(observations):
        if isinstance(value, bool) or not isinstance(value, Real):
            return None, _reason(
                "invalid_observation",
                "Cada observación debe ser un número real; no se imputan ausencias.",
                field=field,
                index=index,
            )
        try:
            number = float(value)
        except (OverflowError, ValueError):
            number = None
        if number is None or not math.isfinite(number):
            return None, _reason(
                "nonfinite_observation",
                "Cada observación debe ser finita y representable.",
                field=field,
                index=index,
            )
        observations[index] = number
    return observations, None


def _mean_loss(errors, *, squared):
    losses = []
    for error in errors:
        magnitude = abs(error)
        loss = magnitude * magnitude if squared else magnitude
        if not math.isfinite(loss):
            return None, _reason(
                "numeric_overflow", "El cálculo del error supera la escala numérica."
            )
        if magnitude != 0 and loss == 0:
            return None, _reason(
                "numeric_underflow", "El cálculo del error pierde la escala numérica."
            )
        losses.append(loss)
    largest = max(losses)
    if largest == 0:
        return 0.0, None
    # Normalize before adding to avoid overflowing a finite arithmetic mean.
    mean = (math.fsum(value / largest for value in losses) / len(losses)) * largest
    if mean == 0:
        return None, _reason(
            "numeric_underflow", "La media del error pierde la escala numérica."
        )
    return mean, None


def _metric(training_errors, evaluation_errors, *, squared):
    denominator, reason = _mean_loss(training_errors, squared=squared)
    if reason:
        return _unavailable({**reason, "stage": "training_scale"})
    if denominator == 0:
        return _unavailable(
            _reason(
                "zero_training_scale",
                "El benchmark naïve tiene error cero en entrenamiento.",
                stage="training_scale",
            ),
            denominator=0.0,
        )
    numerator, reason = _mean_loss(evaluation_errors, squared=squared)
    if reason:
        return _unavailable({**reason, "stage": "evaluation_error"}, denominator)
    if squared:
        # Equivalent to sqrt(MSE / scale), without overflowing the ratio first.
        value = math.sqrt(numerator) / math.sqrt(denominator)
    else:
        value = numerator / denominator
    if not math.isfinite(value):
        return _unavailable(
            _reason("numeric_overflow", "La métrica escalada no es representable."),
            denominator,
        )
    if numerator != 0 and value == 0:
        return _unavailable(
            _reason("numeric_underflow", "La métrica escalada pierde su escala."),
            denominator,
        )
    return {
        "available": True,
        "value": value,
        "denominator": denominator,
        "reason": None,
    }


def compute(train, actual, predicted, *, m=1):
    """Compute additional scaled errors using only training for both scales.

    ``m`` is an explicit positive integer lag (default 1, nonseasonal naïve).
    Input observations must be finite real numbers at a common cadence. The
    caller must provide observed history only: this function does not turn an
    unobserved period into zeros, infer seasonality or choose a lag. Results
    are dictionaries serializable with ``json.dumps(..., allow_nan=False)``.
    A metric below 1 has lower error than the training naïve in its own scale.
    These metrics supplement WAPE; they do not select a model or change LIVE.
    """
    if isinstance(m, bool) or not isinstance(m, Integral) or m < 1:
        return _result(
            None, _reason("invalid_m", "m debe ser un entero positivo explícito.")
        )
    m = int(m)
    result = _result(m)
    inputs = {}
    for field, values in (("train", train), ("actual", actual), ("predicted", predicted)):
        observations, reason = _series(values, field)
        if reason:
            return _result(m, reason)
        inputs[field] = observations
    if len(inputs["train"]) <= m:
        return _result(
            m,
            _reason(
                "insufficient_training",
                "El entrenamiento requiere más observaciones que el rezago m.",
            ),
        )
    if len(inputs["actual"]) != len(inputs["predicted"]):
        return _result(
            m, _reason("length_mismatch", "actual y predicted deben tener igual longitud.")
        )
    if not inputs["actual"]:
        return _result(
            m, _reason("empty_evaluation", "Se requieren observaciones de evaluación.")
        )
    training_errors = [
        inputs["train"][index] - inputs["train"][index - m]
        for index in range(m, len(inputs["train"]))
    ]
    evaluation_errors = [
        observed - forecast
        for observed, forecast in zip(inputs["actual"], inputs["predicted"])
    ]
    result["mase"] = _metric(training_errors, evaluation_errors, squared=False)
    result["rmsse"] = _metric(training_errors, evaluation_errors, squared=True)
    return result
