"""Read-only analysis of a confirmed real-2026 batch, without operational data."""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from decimal import Decimal

from flask_login import current_user
from sqlalchemy import or_

from app.models.real_2026 import Real2026Daily, Real2026Import, Real2026Product
from app.services import empirical_backtest_service as empirical, forecast_service
from app.services import real_2026_source_service as source
from app.services.exceptions import NotFoundError, ValidationError
from app.utils.permissions import role_has_permission

SOURCE_LABEL = "Histórico real 2026"
HORIZONS = (7, 30)
FUTURE_RULE = ("Lunes a sábado; domingos no operativos. "
               "No se registran feriados ni cierres futuros documentados.")
PAGE_SIZE = 25
_REASONS = {
    "invalid_train_values": "El historial contiene cantidades inválidas.",
    "insufficient_train_observations": "Se requieren al menos 60 observaciones operativas.",
    "insufficient_train_positive_observations": "Se requieren al menos 12 observaciones con demanda positiva.",
    "service_excluded": "Los servicios se muestran para auditoría y no se pronostican como mercancía.",
    "product_excluded": "El producto está excluido del análisis según la revisión del lote.",
    "superseded_batch": "Esta versión se conserva solo para auditoría; consulte una versión vigente para pronosticar.",
    "model_train_requirements": "El historial no cumple los requisitos del modelo elegido.",
    "future_calendar_required": "Random Forest requiere un calendario operativo futuro confirmado; no se inventan fechas.",
    "model_unavailable": "El modelo no pudo generar un pronóstico válido con este historial.",
    "invalid_prediction_values": "El modelo no produjo una trayectoria completa y válida.",
    "model_execution_error": "No se pudo calcular el modelo. No se sustituye el resultado por ceros.",
}


def _authorize(*permissions):
    if not current_user.is_authenticated:
        raise ValidationError("Autenticación requerida.", status_code=401)
    if not all(role_has_permission(current_user.role, code) for code in permissions):
        raise ValidationError("No tiene permisos para consultar esta información.", status_code=403)


def _batch(public_id):
    row = Real2026Import.query.filter_by(public_id=str(public_id)).first()
    if row is None or row.status not in ("confirmed", "superseded", "reverted"):
        raise NotFoundError("No se encontró un lote histórico confirmado.")
    return row


def _product(batch, product_id):
    if isinstance(product_id, bool):
        raise NotFoundError("Producto histórico no encontrado en este lote.")
    row = Real2026Product.query.filter_by(batch_id=batch.id, id=product_id).first()
    if row is None:
        raise NotFoundError("Producto histórico no encontrado en este lote.")
    return row


def _product_data(row):
    return {"id": row.id, "code": row.code, "name": row.name, "unit": row.unit,
            "cohort": row.cohort, "included": bool(row.included), "is_service": bool(row.is_service),
            "reason": row.reason, "identity_status": row.identity_status, "unit_status": row.unit_status}


def _iso(value):
    return value.isoformat() if value is not None else None


def _batch_data(row):
    return {"public_id": row.public_id, "status": row.status,
            "archived": row.status != "confirmed", "source_label": SOURCE_LABEL,
            "coverage_start": _iso(row.coverage_start), "coverage_end": _iso(row.coverage_end),
            "confirmed_at": _iso(row.confirmed_at), "source_hashes": row.source_hashes,
            "provenance": row.provenance}


def _calendar(batch):
    try:
        days = [date.fromisoformat(value) for value in batch.calendar]
    except (TypeError, ValueError):
        raise ValidationError("El lote no contiene un calendario operativo válido.", status_code=409)
    if not days or any(a >= b for a, b in zip(days, days[1:])):
        raise ValidationError("El calendario del lote debe ser único y cronológico.", status_code=409)
    if days[0] != batch.coverage_start or days[-1] != batch.coverage_end:
        raise ValidationError("La cobertura no coincide con el calendario del lote.", status_code=409)
    return days


def _observations(batch, product):
    calendar = _calendar(batch)
    rows = Real2026Daily.query.filter_by(product_id=product.id).order_by(Real2026Daily.event_date).all()
    values = [(row.event_date, Decimal(row.quantity)) for row in rows]
    if any(not quantity.is_finite() or quantity < 0 for _, quantity in values):
        raise ValidationError("El historial contiene cantidades inválidas.", status_code=409)
    observed = [day for day, _ in values]
    if len(set(observed)) != len(observed) or any(day not in calendar for day in observed):
        raise ValidationError("Las observaciones no coinciden con el calendario del lote.", status_code=409)
    if values and (values[0][1] <= 0 or observed != [day for day in calendar if day >= observed[0]]):
        raise ValidationError("La serie debe conservar el calendario completo desde la primera demanda positiva.", status_code=409)
    return calendar, values


def _sensitivity(product):
    return str(product.cohort or "").upper() == "SENSITIVITY"


def _sufficiency(product, values):
    if product.is_service:
        code = "service_excluded"
    elif not product.included:
        code = "product_excluded"
    else:
        code = empirical.train_only_eligibility([float(quantity) for _, quantity in values])
    return {"eligible": code is None, "reason": _REASONS.get(code) if code else None,
            "reason_code": code, "min_observations": empirical.MIN_TRAIN,
            "min_positive": empirical.MIN_POSITIVE}


def products(public_id, *, search="", page=1):
    """List only the independent catalog of the requested auditable batch."""
    _authorize("historical_imports:read")
    batch = _batch(public_id)
    if type(page) is not int or page < 1:
        raise ValidationError("La página debe ser un entero positivo.")
    if not isinstance(search, str) or len(search) > 150:
        raise ValidationError("La búsqueda no puede superar 150 caracteres.")
    query = Real2026Product.query.filter_by(batch_id=batch.id)
    if search.strip():
        term = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(or_(Real2026Product.code.ilike(f"%{term}%", escape="\\"),
                                 Real2026Product.name.ilike(f"%{term}%", escape="\\")))
    total = query.count()
    rows = query.order_by(Real2026Product.code, Real2026Product.id).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    return {"batch": _batch_data(batch), "items": [_product_data(row) for row in rows],
            "page": page, "pages": max(1, math.ceil(total / PAGE_SIZE)), "total": total}


def product_history(public_id, product_id):
    """Return exact observed quantities and their own calendar, never today's stock."""
    _authorize("historical_imports:read")
    batch = _batch(public_id)
    product = _product(batch, product_id)
    calendar, values = _observations(batch, product)
    positive = sum(quantity > 0 for _, quantity in values)
    count = len(values)
    first = _iso(values[0][0]) if values else None
    last = _iso(values[-1][0]) if values else None
    return {"batch": _batch_data(batch), "product": _product_data(product),
            "points": [{"date": day.isoformat(), "quantity": str(quantity)} for day, quantity in values],
            "summary": {"count": count, "positive_count": positive, "zero_count": count - positive,
                        "zero_ratio": (count - positive) / count if count else None,
                        "total": str(sum((quantity for _, quantity in values), Decimal(0))),
                        "first_date": first, "last_date": last},
            "calendar": [day.isoformat() for day in calendar],
            "coverage_start": _iso(batch.coverage_start), "coverage_end": _iso(batch.coverage_end),
            "sufficiency": _sufficiency(product, values), "source_label": SOURCE_LABEL,
            "sensitivity_only": _sensitivity(product),
            "unit_notice": ("Solo sensibilidad: se conserva la unidad de origen sin conversiones."
                            if _sensitivity(product) else "Se conserva la unidad de origen sin conversiones.")}


def _forecast_result(history, model, horizon):
    return {"status": "ineligible", "reason": history["sufficiency"]["reason"],
            "reason_code": history["sufficiency"]["reason_code"], "model": model,
            "method_label": forecast_service.MODEL_LABELS[model], "horizon": horizon,
            "time_unit": "operational_day", "cutoff": history["summary"]["last_date"],
            "points": [], "total": None, "future_calendar": None,
            "sensitivity_only": history["sensitivity_only"]}


def _future_starts(history, horizon):
    """Períodos operativos posteriores al corte del lote, o None si el corte no es el fin del calendario."""
    calendar = history["calendar"]
    if not calendar or history["summary"]["last_date"] != calendar[-1]:
        return None
    return source.operational_days_after(date.fromisoformat(calendar[-1]), horizon)


def _predict(history, result):
    values = [float(point["quantity"]) for point in history["points"]]
    starts = [date.fromisoformat(point["date"]) for point in history["points"]]
    model, horizon = result["model"], result["horizon"]
    if not forecast_service.model_eligible(values, model, "daily", starts, operational=True):
        result.update(reason_code="model_train_requirements", reason=_REASONS["model_train_requirements"])
        return
    future = _future_starts(history, horizon)
    if model == "random_forest" and future is None:
        result.update(status="model_unavailable", reason_code="future_calendar_required",
                      reason=_REASONS["future_calendar_required"])
        return
    # Only Random Forest builds calendar features; the other models keep their exact call.
    options = {"future_starts": future} if model == "random_forest" else {}
    try:
        path = forecast_service.predict(values, horizon, model, "daily", starts=starts, operational=True, **options)
        if len(path) != horizon or any(not math.isfinite(value) or value < 0 for value in path):
            result.update(status="model_unavailable", reason_code="invalid_prediction_values",
                          reason=_REASONS["invalid_prediction_values"])
            return
    except forecast_service.ModelUnavailable:
        result.update(status="model_unavailable", reason_code="model_unavailable", reason=_REASONS["model_unavailable"])
        return
    except Exception:
        # Preserve the failure state without exposing internals or substituting a model.
        result.update(status="error", reason_code="model_execution_error", reason=_REASONS["model_execution_error"])
        return
    days = future or [None] * horizon
    result.update(status="available", reason=None, reason_code=None,
                  points=[{"period": number, "date": _iso(day), "quantity": float(value)}
                          for number, (day, value) in enumerate(zip(days, path), 1)],
                  total=float(sum(path)))
    if future:
        result["future_calendar"] = {"rule": FUTURE_RULE, "start": future[0].isoformat(),
                                     "end": future[-1].isoformat(),
                                     "calendar_days": (future[-1] - date.fromisoformat(result["cutoff"])).days}


def analyze(public_id, product_id, *, model="moving_average", horizon=7):
    """Forecast operational periods after the imported cutoff (Monday to Saturday)."""
    _authorize("historical_imports:read", "predictions:read")
    if model not in forecast_service.MODELS:
        raise ValidationError("Seleccione un modelo disponible en el registro de pronósticos.")
    if type(horizon) is not int or horizon not in HORIZONS:
        raise ValidationError("El horizonte debe ser de 7 o 30 períodos operativos.")
    history = product_history(public_id, product_id)
    result = _forecast_result(history, model, horizon)
    if history["batch"]["archived"]:
        result.update(reason_code="superseded_batch", reason=_REASONS["superseded_batch"])
    elif history["sufficiency"]["eligible"]:
        _predict(history, result)
    return {**history, "analysis_generated_at": datetime.now(timezone.utc).isoformat(),
            "namespace": f"real_2026:{public_id}:{product_id}", "forecast": result,
            "models": [{"value": value, "label": forecast_service.MODEL_LABELS[value]}
                       for value in forecast_service.MODELS], "horizons": list(HORIZONS)}
