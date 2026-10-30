"""Clasificación de suficiencia histórica para un futuro módulo predictivo.

Esta fase solo diagnostica. No entrena modelos, no genera pronósticos y no
calcula cantidades de reabastecimiento.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import joinedload

from app.models import Product
from app.services.demand_analysis_service import (
    DescriptiveStats,
    PATTERN_LABELS,
    compute_descriptive_stats,
)
from app.services.demand_data_service import (
    ProductDemandSeries,
    TemporalCutoff,
    build_daily_series_by_product,
    get_temporal_cutoff,
    load_demand_events,
)
from app.services.exceptions import NotFoundError

CLASS_NO_HISTORY = "NO_HISTORY"
CLASS_INSUFFICIENT = "INSUFFICIENT"
CLASS_LIMITED = "LIMITED"
CLASS_SIMPLE_READY = "SIMPLE_READY"
CLASS_ADVANCED_READY = "ADVANCED_READY"

# Umbrales internos (no configurables desde el frontend en esta fase).
ADVANCED_MIN_PERIODS = 60
ADVANCED_MIN_POSITIVE = 12
SIMPLE_MIN_PERIODS = 30
SIMPLE_MIN_POSITIVE = 8
LIMITED_MIN_PERIODS = 8
LIMITED_MIN_POSITIVE = 4

CLASS_LABELS = {
    CLASS_NO_HISTORY: "Sin historial",
    CLASS_INSUFFICIENT: "Insuficiente",
    CLASS_LIMITED: "Limitado",
    CLASS_SIMPLE_READY: "Apto simple",
    CLASS_ADVANCED_READY: "Apto avanzado",
}

INSUFFICIENT_MESSAGE = (
    "No existen datos históricos suficientes para generar una predicción confiable."
)
NO_FORECAST_MESSAGE = (
    "Esta vista solo diagnostica la suficiencia del historial. "
    "Aún no se generan pronósticos ni recomendaciones de reabastecimiento."
)

READY_FOR_SIMPLE_OR_ADVANCED = {CLASS_SIMPLE_READY, CLASS_ADVANCED_READY}


def _as_float(value: Decimal | float | int | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def classify_sufficiency(stats: DescriptiveStats) -> tuple[str, str]:
    """Aplica umbrales de mayor a menor. Períodos = días calendario de la serie."""
    periods = stats.periods
    positive = stats.positive_periods
    if positive <= 0:
        return (
            CLASS_NO_HISTORY,
            "No hay períodos con demanda positiva en el historial válido.",
        )
    if periods >= ADVANCED_MIN_PERIODS and positive >= ADVANCED_MIN_POSITIVE:
        return (
            CLASS_ADVANCED_READY,
            (
                f"Hay {periods} períodos y {positive} períodos positivos; "
                f"cumple el umbral avanzado (≥{ADVANCED_MIN_PERIODS} períodos y "
                f"≥{ADVANCED_MIN_POSITIVE} positivos)."
            ),
        )
    if periods >= SIMPLE_MIN_PERIODS and positive >= SIMPLE_MIN_POSITIVE:
        return (
            CLASS_SIMPLE_READY,
            (
                f"Hay {periods} períodos y {positive} períodos positivos; "
                f"cumple el umbral de modelo simple (≥{SIMPLE_MIN_PERIODS} períodos y "
                f"≥{SIMPLE_MIN_POSITIVE} positivos)."
            ),
        )
    if periods >= LIMITED_MIN_PERIODS and positive >= LIMITED_MIN_POSITIVE:
        return (
            CLASS_LIMITED,
            (
                f"Hay {periods} períodos y {positive} períodos positivos; "
                f"alcanza historial limitado (≥{LIMITED_MIN_PERIODS} períodos y "
                f"≥{LIMITED_MIN_POSITIVE} positivos), insuficiente para un modelo estable."
            ),
        )
    return (
        CLASS_INSUFFICIENT,
        (
            f"Hay {positive} período(s) positivo(s) y {periods} día(s) de cobertura; "
            f"no llega a historial limitado (≥{LIMITED_MIN_POSITIVE} positivos y "
            f"≥{LIMITED_MIN_PERIODS} períodos)."
        ),
    )


def get_readiness_summary() -> dict:
    cutoff, diagnoses = _collect_diagnoses()
    kpis = _kpis(diagnoses)
    has_model_ready = (kpis["simple_ready"] + kpis["advanced_ready"]) > 0
    show_insufficient = (not cutoff.has_confirmed_historical_import) or not has_model_ready
    return {
        "module_phase": "readiness",
        "forecast_available": False,
        "replenishment_available": False,
        "message": INSUFFICIENT_MESSAGE if show_insufficient else NO_FORECAST_MESSAGE,
        "no_forecast_message": NO_FORECAST_MESSAGE,
        "insufficient_history_message": INSUFFICIENT_MESSAGE,
        "show_insufficient_banner": show_insufficient,
        "kpis": kpis,
        "cutoff": cutoff.to_dict(),
        "demand_sources": {
            "historical": (
                "historical_demand_records de lotes confirmados, vigentes, "
                "con include_in_demand y product_id"
            ),
            "operational": (
                "sale_lines de ventas completadas y delivery_note_items de notas "
                "emitidas con fecha posterior al período histórico confirmado"
            ),
            "excluded": (
                "stock_movements, notas canceladas, cotizaciones, pedidos, lotes "
                "revertidos, registros sin include_in_demand y salidas manuales"
            ),
        },
    }


def list_product_readiness() -> dict:
    _cutoff, diagnoses = _collect_diagnoses()
    items = [item["summary"] for item in diagnoses]
    return {
        "count": len(items),
        "forecast_available": False,
        "replenishment_available": False,
        "items": items,
    }


def get_product_readiness(product_id: int) -> dict:
    product = (
        Product.query.options(joinedload(Product.category))
        .filter(Product.id == product_id)
        .first()
    )
    if product is None:
        raise NotFoundError(f"El producto con id {product_id} no existe.")

    cutoff = get_temporal_cutoff()
    events = load_demand_events(cutoff, product_id=product_id)
    series_map = build_daily_series_by_product(events, cutoff)
    series = series_map.get(product.id, ProductDemandSeries(product_id=product.id))
    return _serialize_detail(product, series)


def _collect_diagnoses() -> tuple[TemporalCutoff, list[dict]]:
    cutoff = get_temporal_cutoff()
    events = load_demand_events(cutoff)
    series_map = build_daily_series_by_product(events, cutoff)
    products = (
        Product.query.options(joinedload(Product.category))
        .order_by(Product.code, Product.id)
        .all()
    )
    diagnoses = []
    for product in products:
        series = series_map.get(product.id, ProductDemandSeries(product_id=product.id))
        diagnoses.append(_serialize_product(product, series))
    return cutoff, diagnoses


def _serialize_product(product: Product, series: ProductDemandSeries) -> dict:
    stats = compute_descriptive_stats(series)
    sufficiency, reason = classify_sufficiency(stats)
    readiness = bool(product.is_active) and sufficiency in READY_FOR_SIMPLE_OR_ADVANCED
    summary = {
        "product_id": product.id,
        "code": product.code,
        "name": product.name,
        "category": product.category.name if product.category else None,
        "is_active": bool(product.is_active),
        "data_source": stats.data_source,
        "start_date": _iso(stats.start_date),
        "end_date": _iso(stats.end_date),
        "periods": stats.periods,
        "positive_periods": stats.positive_periods,
        "zero_ratio": stats.zero_ratio,
        "total_demand": _as_float(stats.total_demand),
        "average_daily_demand": stats.average_daily_demand,
        "original_event_count": stats.original_event_count,
        "sufficiency_class": sufficiency,
        "sufficiency_label": CLASS_LABELS[sufficiency],
        "classification_reason": reason,
        "demand_pattern": stats.demand_pattern,
        "demand_pattern_label": PATTERN_LABELS[stats.demand_pattern],
        "readiness_for_replenishment": readiness,
        "forecast_available": False,
    }
    return {"summary": summary, "stats": stats, "series": series, "product": product}


def _serialize_detail(product: Product, series: ProductDemandSeries) -> dict:
    payload = _serialize_product(product, series)
    stats: DescriptiveStats = payload["stats"]
    sufficiency = payload["summary"]["sufficiency_class"]
    has_series = bool(series.points)
    cannot_forecast = sufficiency in {CLASS_NO_HISTORY, CLASS_INSUFFICIENT} or not has_series
    return {
        **payload["summary"],
        "median": stats.median,
        "standard_deviation": stats.standard_deviation,
        "min_demand": _as_float(stats.min_demand),
        "max_demand": _as_float(stats.max_demand),
        "zero_periods": stats.zero_periods,
        "days_covered": stats.days_covered,
        "last_date_with_demand": _iso(stats.last_date_with_demand),
        "days_since_last_demand": stats.days_since_last_demand,
        "average_interval_between_positive_demand": (
            stats.average_interval_between_positive_demand
        ),
        "coefficient_of_variation": stats.coefficient_of_variation,
        "demand_pattern_reason": stats.demand_pattern_reason,
        "inconsistencies": series.inconsistencies,
        "daily_series": [
            {"date": point.day.isoformat(), "demand": _as_float(point.demand)}
            for point in series.points
        ],
        "has_historical_series": has_series,
        "replenishment_available": False,
        "message": INSUFFICIENT_MESSAGE if cannot_forecast else NO_FORECAST_MESSAGE,
    }


def _kpis(diagnoses: list[dict]) -> dict:
    summaries = [item["summary"] for item in diagnoses]
    def _count(class_name: str) -> int:
        return sum(1 for item in summaries if item["sufficiency_class"] == class_name)

    return {
        "active_products": sum(1 for item in summaries if item["is_active"]),
        "products_with_history": sum(
            1 for item in summaries if item["positive_periods"] > 0
        ),
        "no_history": _count(CLASS_NO_HISTORY),
        "insufficient": _count(CLASS_INSUFFICIENT),
        "limited": _count(CLASS_LIMITED),
        "simple_ready": _count(CLASS_SIMPLE_READY),
        "advanced_ready": _count(CLASS_ADVANCED_READY),
        "total_products": len(summaries),
    }
