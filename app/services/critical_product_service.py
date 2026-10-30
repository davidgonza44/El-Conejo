"""Riesgo de quiebre por producto con reglas transparentes.

Distingue RIESGO OPERATIVO (stock actual vs. stock mínimo, sin pronóstico) de RIESGO
PREDICTIVO (días de cobertura = stock disponible / demanda diaria esperada). Cada
evaluación lleva la lista de motivos que la justifican. Umbrales en ``RiskConfig``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.services.replenishment_service import DEFAULT_LEAD_TIME_DAYS, LEAD_TIME_ASSUMED, LEAD_TIME_PROVIDED

RISK_CRITICAL = "critical"
RISK_HIGH = "high"
RISK_MEDIUM = "medium"
RISK_LOW = "low"
RISK_ORDER = (RISK_CRITICAL, RISK_HIGH, RISK_MEDIUM, RISK_LOW)
RISK_LABELS = {RISK_CRITICAL: "Crítico", RISK_HIGH: "Alto", RISK_MEDIUM: "Medio", RISK_LOW: "Bajo"}
RISK_TONES = {RISK_CRITICAL: "red", RISK_HIGH: "orange", RISK_MEDIUM: "yellow", RISK_LOW: "green"}
RISK_RANK = {level: index for index, level in enumerate(RISK_ORDER)}

KIND_OPERATIONAL = "operational"
KIND_PREDICTIVE = "predictive"

NO_DEMAND_LABEL = "Sin demanda prevista"
NO_FORECAST_LABEL = "Sin pronóstico disponible"
UNCALCULABLE_LABEL = "No calculable"


@dataclass(frozen=True)
class RiskConfig:
    critical_cover_days: float = 3.0
    high_cover_days: float = 7.0
    medium_cover_days: float = 14.0
    default_lead_time_days: int = DEFAULT_LEAD_TIME_DAYS
    horizon_days: int = 30  # ventana para "rotación alta": demanda prevista > stock


DEFAULT_CONFIG = RiskConfig()


@dataclass
class RiskAssessment:
    level: str
    kind: str
    reasons: list[str] = field(default_factory=list)
    days_of_cover: float | None = None   # None => sin pronóstico o sin demanda prevista
    out_of_stock: bool = False
    low_stock: bool = False
    high_rotation: bool = False
    lead_time_source: str = LEAD_TIME_ASSUMED
    reorder_point: float | None = None

    @property
    def label(self) -> str:
        return RISK_LABELS[self.level]

    @property
    def tone(self) -> str:
        return RISK_TONES[self.level]

    @property
    def motive(self) -> str:
        return self.reasons[0] if self.reasons else "Cobertura suficiente"

    @property
    def rank(self) -> int:
        return RISK_RANK[self.level]


def days_of_cover(available_stock: float, expected_daily_demand: float | None) -> float | None:
    """Stock / demanda diaria; sin dato válido o con demanda cero no se divide."""
    stock, daily = nonnegative_number(available_stock), nonnegative_number(expected_daily_demand)
    if stock is None or daily is None or daily == 0:
        return None
    cover = stock / daily
    return cover if math.isfinite(cover) else None


def nonnegative_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def cover_label(cover: float | None, has_forecast: bool, *, zero_demand: bool = True) -> str:
    if not has_forecast:
        return UNCALCULABLE_LABEL
    if cover is None:
        return NO_DEMAND_LABEL if zero_demand else UNCALCULABLE_LABEL
    if nonnegative_number(cover) is None:
        return UNCALCULABLE_LABEL
    days = f"{cover:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    if 0 < cover < 0.01:
        days = "< 0,01"
    return f"{days} día" if cover == 1 else f"{days} días"


def _raise(assessment: RiskAssessment, level: str, reason: str) -> None:
    if RISK_RANK[level] < RISK_RANK[assessment.level]:
        assessment.level = level
        assessment.reasons.insert(0, reason)
    else:
        assessment.reasons.append(reason)


def _coverage_risk(result, current_stock, lead_time, config):
    cover = result.days_of_cover
    if cover is None or current_stock <= 0:
        return
    if cover <= config.critical_cover_days:
        _raise(result, RISK_CRITICAL, f"Cobertura de {cover_label(cover, True)}")
    elif cover <= lead_time:
        _raise(result, RISK_HIGH, f"Lead time ({lead_time} días) superior a la cobertura")
    elif cover <= config.high_cover_days:
        _raise(result, RISK_HIGH, "Cobertura baja")
    elif cover <= config.medium_cover_days:
        _raise(result, RISK_MEDIUM, "Cobertura baja para el margen de reposición")
    if current_stock <= result.reorder_point:
        _raise(result, RISK_MEDIUM, "Stock en o por debajo del punto de reorden")


def _predictive_risk(result, current_stock, minimum_stock, daily, lead_time, trend_up, config):
    result.reorder_point = max(0, minimum_stock) + daily * lead_time
    result.days_of_cover = days_of_cover(current_stock, daily)
    if daily == 0:
        result.reasons.append(NO_DEMAND_LABEL)
    else:
        _coverage_risk(result, current_stock, lead_time, config)
    if daily * config.horizon_days > current_stock > 0:
        result.high_rotation = True
        result.reasons.append("Demanda prevista elevada")
    if trend_up and result.low_stock:
        result.reasons.append("Stock bajo + tendencia creciente")


def _operational_risk(result, current_stock, minimum_stock, daily, has_forecast):
    result.out_of_stock = current_stock <= 0
    result.low_stock = 0 < current_stock <= minimum_stock
    if current_stock < 0:
        _raise(result, RISK_CRITICAL, "Stock negativo: revisar inventario")
    elif result.out_of_stock and (minimum_stock > 0 or (daily is not None and daily > 0)):
        _raise(result, RISK_CRITICAL, "Sin stock")
    elif result.low_stock:
        reason = "En el stock mínimo" if current_stock == minimum_stock else "Por debajo del stock mínimo"
        _raise(result, RISK_MEDIUM, reason if has_forecast else f"{reason} (sin pronóstico)")


def assess(current_stock: float, minimum_stock: float, expected_daily_demand: float | None,
           has_forecast: bool, lead_time_days: int | None = None, trend_up: bool = False,
           config: RiskConfig = DEFAULT_CONFIG) -> RiskAssessment:
    """``has_forecast=False`` => solo reglas operativas; con pronóstico se añade la cobertura."""
    daily = nonnegative_number(expected_daily_demand) if has_forecast else None
    has_forecast = has_forecast and daily is not None
    lead_time = lead_time_days or config.default_lead_time_days
    result = RiskAssessment(level=RISK_LOW, kind=KIND_PREDICTIVE if has_forecast else KIND_OPERATIONAL,
                            lead_time_source=LEAD_TIME_ASSUMED if lead_time_days is None else LEAD_TIME_PROVIDED)
    _operational_risk(result, current_stock, minimum_stock, daily, has_forecast)
    if has_forecast:
        _predictive_risk(result, current_stock, minimum_stock, daily, lead_time, trend_up, config)
    if not result.reasons:
        result.reasons.append("Cobertura suficiente" if has_forecast else
                              "Sin pronóstico; sin mínimo positivo que justifique criticidad" if minimum_stock <= 0 else
                              "Stock sobre el mínimo (sin pronóstico)")
    return result
