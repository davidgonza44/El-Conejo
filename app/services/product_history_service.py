"""Indicadores históricos de Gestión de Productos por período (America/Caracas).

Regla única para las cuatro tarjetas: el valor es el estado al CIERRE del último
día del período. Si ese día es hoy se usa el estado vivo; si es un día pasado se
usa inventory_snapshots. Sin cierre guardado no hay valor: nunca se reconstruye,
se rellena con ceros ni se copia el estado actual a fechas pasadas.
"""
import calendar
from datetime import datetime, timedelta

from sqlalchemy import and_, case, distinct, func

from app.extensions import db
from app.models import InventorySnapshot
from app.services import product_service
from app.services.exceptions import ValidationError
from app.services.inventory_snapshot_service import commercial_today

COMPARISONS = ("previous", "year", "none")
KPI_KEYS = ("total_products", "low_stock", "out_of_stock", "active_categories")
# Subir bajo/sin stock es malo; bajar total/categorías es neutro, no rojo.
_UP = {"total_products": "positive", "low_stock": "negative", "out_of_stock": "negative", "active_categories": "positive"}
_DOWN = {"total_products": "neutral", "low_stock": "positive", "out_of_stock": "positive", "active_categories": "neutral"}


def _parse_date(value, field):
    try:
        return datetime.strptime(value or "", "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError(f"'{field}' debe ser una fecha válida YYYY-MM-DD.")


def previous_range(start, end):
    """Rango inmediatamente anterior con la misma cantidad de días calendario."""
    prev_end = start - timedelta(days=1)
    return prev_end - (end - start), prev_end


def shift_year(day):
    """Misma fecha un año antes; 29 feb se recorta al último día válido."""
    last = calendar.monthrange(day.year - 1, day.month)[1]
    return day.replace(year=day.year - 1, day=min(day.day, last))


def year_range(start, end):
    return shift_year(start), shift_year(end)


def snapshot_kpis(day):
    """KPIs desde el cierre guardado de ``day``; None si ese día no fue capturado."""
    snap = InventorySnapshot
    active = snap.product_is_active.is_(True)
    row = (
        db.session.query(
            func.count(snap.id),
            func.sum(case((active, 1), else_=0)),
            func.sum(case((and_(active, snap.stock > 0, snap.stock <= snap.minimum_stock), 1), else_=0)),
            func.sum(case((and_(active, snap.stock == 0), 1), else_=0)),
            func.count(distinct(case((active, snap.category_id)))),
        )
        .filter(snap.snapshot_date == day)
        .one()
    )
    if not row[0]:
        return None
    return dict(zip(KPI_KEYS, (int(value or 0) for value in row[1:])))


def kpis_at(day, today):
    return product_service.kpi_counts() if day == today else snapshot_kpis(day)


def _kpi(key, current, previous):
    item = {"value": current, "comparison_value": previous, "delta": None, "change_percent": None, "sentiment": None}
    if current is None or previous is None:
        return item
    delta = current - previous
    item.update(
        delta=delta,
        change_percent=round(delta * 100 / previous, 1) if previous else None,
        sentiment="neutral" if delta == 0 else (_UP if delta > 0 else _DOWN)[key],
    )
    return item


def _reason(day, available_from):
    if available_from is None:
        return "history_not_started"
    return "before_history" if day < available_from else "snapshot_missing"


def history_summary(params: dict, now=None) -> dict:
    today = commercial_today(now)
    start = _parse_date(params.get("start"), "start")
    requested_end = _parse_date(params.get("end"), "end")
    if start > requested_end:
        raise ValidationError("La fecha de inicio no puede ser posterior a la fecha de fin.")
    if start > today:
        raise ValidationError("El período no puede comenzar en el futuro.")
    compare = (params.get("compare") or "previous").strip().lower()
    if compare not in COMPARISONS:
        raise ValidationError(f"Comparación inválida. Valores permitidos: {', '.join(COMPARISONS)}.")

    end = min(requested_end, today)
    available_from = db.session.query(func.min(InventorySnapshot.snapshot_date)).scalar()
    current = kpis_at(end, today)
    previous = None
    comparison = {"type": compare, "start": None, "end": None, "available": False}
    if compare != "none":
        c_start, c_end = (previous_range if compare == "previous" else year_range)(start, end)
        previous = kpis_at(c_end, today)
        comparison.update(start=c_start.isoformat(), end=c_end.isoformat(), available=previous is not None)

    if current is None:
        reason = "period_" + _reason(end, available_from)
    elif compare == "none":
        reason = "no_comparison"
    elif previous is None:
        reason = "comparison_" + _reason(c_end, available_from)
    else:
        reason = "ok"

    return {
        "timezone": "America/Caracas",
        "today": today.isoformat(),
        "history": {
            "available_from": available_from.isoformat() if available_from else None,
            "current_period_available": current is not None,
            "comparison_available": previous is not None,
            "reason": reason,
        },
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "requested_end": requested_end.isoformat(),
            "clamped": end != requested_end,
            "available": current is not None,
        },
        "comparison": comparison,
        "kpis": {
            key: _kpi(key, current[key] if current else None, previous[key] if previous else None)
            for key in KPI_KEYS
        },
    }
