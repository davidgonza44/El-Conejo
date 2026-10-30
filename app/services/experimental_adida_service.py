"""ADIDA experimental puro: agregación explícita, naïve agregado y desagregación."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from math import fsum, isfinite
from numbers import Real


def _positive_integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _reason(code, message):
    return {"code": code, "message": message}


def _empty_result(aggregation_size, horizon):
    return {
        "model": "adida",
        "experimental": True,
        "available": False,
        "aggregation_size": aggregation_size if _positive_integer(aggregation_size) else None,
        "aggregated_observations": 0,
        "horizon": horizon if _positive_integer(horizon) else None,
        "horizon_unit": "calendar_day",
        "forecast": [],
        "forecast_dates": [],
        "forecast_total": None,
        "base_method": "naive_last_aggregate",
        "disaggregation_method": "uniform",
        "partial_block_policy": "origin_aligned_drop_oldest",
        "unavailable_reason": None,
        "metadata": {
            "training_observations": 0,
            "discarded_initial_observations": 0,
            "training_block_totals": [],
            "forecast_block_periods": [],
            "forecast_block_totals": [],
        },
    }


def _history_values(history):
    if not isinstance(history, Sequence) or isinstance(history, (str, bytes)):
        return None, _reason("invalid_history", "La historia debe ser una secuencia de cantidades observadas.")
    values = []
    for value in history:
        if not isinstance(value, Real) or isinstance(value, bool):
            return None, _reason("invalid_observation", "Cada cantidad observada debe ser numérica; un período no observado no es cero.")
        try:
            number = float(value)
        except (OverflowError, ValueError):
            return None, _reason("non_finite_observation", "Las cantidades deben ser finitas.")
        if not isfinite(number) or number < 0:
            return None, _reason("invalid_observation", "Las cantidades deben ser finitas y no negativas.")
        values.append(number)
    return values, None


def _parse_day(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and len(value) == 10 and value[4] == "-" and value[7] == "-":
        return date.fromisoformat(value)
    raise ValueError("Se requiere fecha ISO YYYY-MM-DD o datetime.date.")


def _history_days(starts, forecast_origin, count):
    if not isinstance(starts, Sequence) or isinstance(starts, (str, bytes)) or len(starts) != count:
        return None, None, _reason("invalid_dates", "Se requiere una fecha por observación.")
    try:
        origin = _parse_day(forecast_origin)
        days = [_parse_day(value) for value in starts]
    except (ValueError, TypeError):
        return None, None, _reason("invalid_dates", "Las fechas deben ser días ISO YYYY-MM-DD o datetime.date.")
    if any(day >= origin for day in days):
        return None, None, _reason("future_history", "Toda observación de entrenamiento debe ser anterior al origen del forecast.")
    differences = [(right - left).days for left, right in zip(days, days[1:])]
    if any(delta <= 0 for delta in differences):
        return None, None, _reason("unordered_history", "Las fechas deben estar ordenadas y no duplicadas.")
    if any(delta != 1 for delta in differences):
        return None, None, _reason("unobserved_gap", "La historia contiene un período no observado; no se concatena ni se rellena con ceros.")
    if days and (origin - days[-1]).days != 1:
        return None, None, _reason("unobserved_trailing_period", "La última observación debe corresponder al día anterior al origen.")
    return days, origin, None


def _aggregate(values, aggregation_size):
    discarded = len(values) % aggregation_size
    totals = [fsum(values[start:start + aggregation_size])
              for start in range(discarded, len(values), aggregation_size)]
    return discarded, totals


def _disaggregate(block_total, aggregation_size, horizon):
    rate = block_total / aggregation_size
    forecast, block_periods, block_totals = [], [], []
    for start in range(0, horizon, aggregation_size):
        periods = min(aggregation_size, horizon - start)
        total = block_total if periods == aggregation_size else rate * periods
        values = [rate] * (periods - 1)
        values.append(total - fsum(values))
        forecast.extend(values)
        block_periods.append(periods)
        block_totals.append(total)
    return forecast, block_periods, block_totals


def adida(history, *, aggregation_size, horizon, starts, forecast_origin):
    """Produce un DTO experimental; nunca infiere bucket, ordena ni rellena datos.

    ``starts`` contiene la fecha de cada cantidad diaria observada. El origen es
    el primer día pronosticado. Todos los bloques se anclan hacia atrás desde él;
    el único bloque histórico parcial posible (el más antiguo) se descarta.
    ``horizon`` se expresa en días calendario, incluidos los ceros observados.
    """
    result = _empty_result(aggregation_size, horizon)
    if not _positive_integer(aggregation_size) or not _positive_integer(horizon):
        result["unavailable_reason"] = _reason("invalid_configuration", "aggregation_size y horizon deben ser enteros positivos explícitos.")
        return result
    values, issue = _history_values(history)
    if issue:
        result["unavailable_reason"] = issue
        return result
    result["metadata"]["training_observations"] = len(values)
    days, origin, issue = _history_days(starts, forecast_origin, len(values))
    if issue:
        result["unavailable_reason"] = issue
        return result
    if len(values) < aggregation_size:
        result["unavailable_reason"] = _reason("insufficient_history", "No existe un bloque histórico completo para el tamaño explícito.")
        return result
    return _forecast_result(result, values, days, origin, aggregation_size, horizon)


def _forecast_result(result, values, days, origin, aggregation_size, horizon):
    try:
        discarded, totals = _aggregate(values, aggregation_size)
        forecast, block_periods, block_totals = _disaggregate(totals[-1], aggregation_size, horizon)
        forecast_total = fsum(forecast)
        forecast_dates = [(origin + timedelta(days=index)).isoformat() for index in range(horizon)]
    except (OverflowError, ValueError):
        result["unavailable_reason"] = _reason("numeric_or_date_range", "El escenario supera el rango numérico o de fechas soportado.")
        return result
    if not all(isfinite(number) for number in totals + forecast + block_totals + [forecast_total]):
        result["unavailable_reason"] = _reason("numeric_range", "El forecast debe contener exclusivamente cantidades finitas.")
        return result
    result.update({
        "available": True,
        "aggregated_observations": len(totals),
        "forecast": forecast,
        "forecast_dates": forecast_dates,
        "forecast_total": forecast_total,
    })
    result["metadata"].update({
        "training_start": days[0].isoformat(),
        "training_end": days[-1].isoformat(),
        "forecast_origin": origin.isoformat(),
        "discarded_initial_observations": discarded,
        "training_block_totals": totals,
        "forecast_block_periods": block_periods,
        "forecast_block_totals": block_totals,
    })
    return result
