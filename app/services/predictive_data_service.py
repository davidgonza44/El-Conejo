"""Contrato de datos, normalización y series temporales del módulo predictivo.

Tres responsabilidades, sin pronóstico ni escrituras:

1. Contrato interno estable (``DEMAND_FIELDS`` / ``SUPPLY_FIELDS``) y capa de
   mapeo/normalización para Excel/CSV futuros (``normalize_rows``). No obliga a que
   el archivo traiga estos nombres: ``column_map`` traduce columnas de origen.
2. Agregación temporal con granularidad encapsulada (diaria/semanal), relleno de
   períodos sin venta con 0 (demanda observada = 0, no "registro faltante") y
   extensión de la serie hasta hoy.
3. Elegibilidad por producto (ELIGIBLE / INSUFFICIENT_HISTORY / INTERMITTENT /
   INACTIVE / NO_DEMAND) con criterios centralizados en ``EligibilityConfig``.

La extracción desde la BD sigue viviendo en ``demand_data_service`` (fuente canónica:
históricos confirmados + ventas completadas + notas emitidas).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Callable, Iterable, Mapping

from app.services import demand_data_service
from app.services.demand_analysis_service import SPARSE_ZERO_RATIO, institutional_today
from app.services.prediction_readiness_service import (
    LIMITED_MIN_PERIODS,
    LIMITED_MIN_POSITIVE,
    SIMPLE_MIN_PERIODS,
    SIMPLE_MIN_POSITIVE,
)

# ---------------------------------------------------------------------------
# Contrato de datos
# ---------------------------------------------------------------------------
DEMAND_REQUIRED = ("date", "quantity")
DEMAND_PRODUCT_KEYS = ("product_id", "product_code")  # al menos uno
DEMAND_OPTIONAL = (
    "product_name", "category", "operation_type", "unit_price", "total", "customer_id",
    "branch_id", "sale_id", "movement_id", "line_id", "status", "stock_before", "stock_after",
)
DEMAND_FIELDS = DEMAND_REQUIRED + DEMAND_PRODUCT_KEYS + DEMAND_OPTIONAL
SUPPLY_FIELDS = (
    "current_stock", "minimum_stock", "supplier_id", "supplier_name", "unit_cost", "lead_time_days",
    "ordered_quantity", "received_quantity", "pending_quantity", "expected_delivery_date",
)

# Signo de cada tipo de operación sobre la demanda observada. Lo que no está aquí se rechaza.
OPERATION_SIGN = {"sale": 1, "exit": 1, "salida": 1, "venta": 1, "return": -1, "devolucion": -1,
                  "correction": 1}
EXCLUDED_OPERATIONS = ("cancellation", "anulacion", "quote", "cotizacion", "order", "pedido", "entry", "entrada")
EXCLUDED_STATUSES = ("cancelled", "canceled", "anulada", "anulado", "cancelada", "void", "voided")
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M")

REJECT_MISSING_DATE = "missing_or_invalid_date"
REJECT_MISSING_QUANTITY = "missing_or_invalid_quantity"
REJECT_UNKNOWN_PRODUCT = "unknown_product"
REJECT_EXCLUDED_OPERATION = "excluded_operation"
REJECT_CANCELLED = "cancelled_record"
REJECT_DUPLICATE = "duplicate"


@dataclass(frozen=True)
class NormalizedRecord:
    date: date
    product_id: int
    quantity: Decimal          # siempre > 0
    signed_quantity: Decimal   # con signo según operation_type
    operation_type: str
    product_code: str | None = None
    product_name: str | None = None
    category: str | None = None
    extras: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RejectedRow:
    row_number: int
    reason: str
    detail: str


@dataclass
class NormalizedDataset:
    records: list[NormalizedRecord] = field(default_factory=list)
    rejected: list[RejectedRow] = field(default_factory=list)
    duplicates_dropped: int = 0
    source_rows: int = 0

    def summary(self) -> dict:
        reasons: dict[str, int] = {}
        for row in self.rejected:
            reasons[row.reason] = reasons.get(row.reason, 0) + 1
        return {"source_rows": self.source_rows, "accepted": len(self.records),
                "rejected": len(self.rejected), "duplicates_dropped": self.duplicates_dropped,
                "rejection_reasons": reasons}


def parse_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def parse_quantity(value) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip().replace(",", ".")
    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _apply_column_map(row: Mapping, column_map: Mapping[str, str] | None) -> dict:
    if not column_map:
        return dict(row)
    mapped = {}
    for source, canonical in column_map.items():
        if source in row:
            mapped[canonical] = row[source]
    return mapped


def _resolve_product(row: dict, product_lookup) -> int | None:
    raw_id = row.get("product_id")
    if raw_id not in (None, ""):
        try:
            return int(str(raw_id).strip())
        except ValueError:
            return None
    code = str(row.get("product_code") or "").strip().upper()
    if not code or product_lookup is None:
        return None
    if callable(product_lookup):
        return product_lookup(code)
    return product_lookup.get(code)


def _dedupe_key(row: dict, record_date: date, product_id: int, quantity: Decimal, operation: str) -> str:
    """Con id de línea/venta/movimiento la clave es exacta; si no, solo filas idénticas se colapsan."""
    ids = tuple(str(row.get(k) or "") for k in ("line_id", "movement_id", "sale_id"))
    if any(ids):
        base = f"{record_date}|{product_id}|{quantity}|{operation}|" + "|".join(ids)
    else:
        base = f"{record_date}|{product_id}|{quantity}|{operation}|" + repr(sorted(row.items(), key=lambda i: str(i[0])))
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def normalize_rows(rows: Iterable[Mapping], column_map: Mapping[str, str] | None = None,
                   product_lookup: Callable[[str], int | None] | Mapping[str, int] | None = None) -> NormalizedDataset:
    """Traduce filas de origen al contrato interno; rechaza en vez de corregir en silencio."""
    dataset = NormalizedDataset()
    seen: set[str] = set()
    for number, source in enumerate(rows, start=1):
        dataset.source_rows += 1
        row = _apply_column_map(source, column_map)
        status = str(row.get("status") or "").strip().lower()
        if status in EXCLUDED_STATUSES:
            dataset.rejected.append(RejectedRow(number, REJECT_CANCELLED, f"estado '{status}'"))
            continue
        operation = str(row.get("operation_type") or "sale").strip().lower()
        if operation in EXCLUDED_OPERATIONS:
            dataset.rejected.append(RejectedRow(number, REJECT_EXCLUDED_OPERATION, f"operación '{operation}'"))
            continue
        if operation not in OPERATION_SIGN:
            dataset.rejected.append(RejectedRow(number, REJECT_EXCLUDED_OPERATION, f"operación desconocida '{operation}'"))
            continue
        record_date = parse_date(row.get("date"))
        if record_date is None:
            dataset.rejected.append(RejectedRow(number, REJECT_MISSING_DATE, repr(row.get("date"))))
            continue
        quantity = parse_quantity(row.get("quantity"))
        if quantity is None or quantity <= 0:
            dataset.rejected.append(RejectedRow(number, REJECT_MISSING_QUANTITY, repr(row.get("quantity"))))
            continue
        product_id = _resolve_product(row, product_lookup)
        if product_id is None:
            dataset.rejected.append(RejectedRow(number, REJECT_UNKNOWN_PRODUCT, str(row.get("product_code") or row.get("product_id"))))
            continue
        key = _dedupe_key(row, record_date, product_id, quantity, operation)
        if key in seen:
            dataset.duplicates_dropped += 1
            dataset.rejected.append(RejectedRow(number, REJECT_DUPLICATE, "fila repetida"))
            continue
        seen.add(key)
        dataset.records.append(NormalizedRecord(
            date=record_date, product_id=product_id, quantity=quantity,
            signed_quantity=quantity * OPERATION_SIGN[operation], operation_type=operation,
            product_code=(str(row["product_code"]).strip() if row.get("product_code") else None),
            product_name=(str(row["product_name"]).strip() if row.get("product_name") else None),
            category=(str(row["category"]).strip() if row.get("category") else None),
            extras={k: row[k] for k in DEMAND_OPTIONAL if k in row},
        ))
    return dataset


# ---------------------------------------------------------------------------
# Granularidad y series
# ---------------------------------------------------------------------------
GRANULARITY_DAILY = "daily"
GRANULARITY_WEEKLY = "weekly"
GRANULARITIES = (GRANULARITY_DAILY, GRANULARITY_WEEKLY)
PERIOD_DAYS = {GRANULARITY_DAILY: 1, GRANULARITY_WEEKLY: 7}


@dataclass
class TimeSeries:
    product_id: int
    granularity: str
    starts: list[date] = field(default_factory=list)   # Ordered real dates; operational observations need not be civil-contiguous.
    values: list[float] = field(default_factory=list)  # Observed demand; zero does not establish latent demand or availability.
    event_count: int = 0
    data_source: str | None = None
    inconsistencies: list[dict] = field(default_factory=list)
    time_unit: str = "calendar_day"
    is_service: bool = False  # Explicit trusted input, not inferred from catalog text.

    @property
    def operational_index(self) -> list[int]:
        return list(range(1, self.periods + 1)) if self.time_unit == "operational_day" else []

    @property
    def periods(self) -> int:
        return len(self.values)

    @property
    def positive_periods(self) -> int:
        return sum(1 for v in self.values if v > 0)

    @property
    def zero_ratio(self) -> float | None:
        return (self.periods - self.positive_periods) / self.periods if self.periods else None

    @property
    def last_positive_index(self) -> int | None:
        for index in range(self.periods - 1, -1, -1):
            if self.values[index] > 0:
                return index
        return None

    @property
    def period_days(self) -> int:
        return PERIOD_DAYS[self.granularity]


def period_start(day: date, granularity: str) -> date:
    if granularity == GRANULARITY_WEEKLY:
        return day - timedelta(days=day.weekday())  # lunes ISO
    return day


def aggregate_daily(product_id: int, daily: Iterable[tuple[date, Decimal | float]], granularity: str,
                    end: date | None = None, *, operational_calendar=None,
                    is_service: bool = False) -> TimeSeries:
    """Agrega puntos diarios a la granularidad pedida y rellena con 0 los períodos sin venta.

    ``end`` extiende la serie hasta ese día (hoy comercial) para que la inactividad reciente
    sea visible como ceros y no desaparezca por falta de registros.
    """
    if granularity not in GRANULARITIES:
        raise ValueError(f"Granularidad no soportada: {granularity}")
    if operational_calendar is not None:
        if granularity != GRANULARITY_DAILY:
            raise ValueError("Operational calendar requires daily positional observations")
        points = demand_data_service.operational_points(daily, operational_calendar, end)
        return TimeSeries(product_id, granularity, [p.day for p in points],
                          [float(p.demand) for p in points], time_unit="operational_day",
                          is_service=is_service)
    buckets: dict[date, float] = {}
    for day, value in daily:
        key = period_start(day, granularity)
        buckets[key] = buckets.get(key, 0.0) + float(value)
    series = TimeSeries(product_id=product_id, granularity=granularity, is_service=is_service)
    if not buckets and end is None:
        return series
    first = min(buckets) if buckets else period_start(end, granularity)
    last = max(buckets) if buckets else first
    if end is not None:
        last = max(last, period_start(end, granularity))
    step = timedelta(days=PERIOD_DAYS[granularity])
    current = first
    while current <= last:
        series.starts.append(current)
        series.values.append(buckets.get(current, 0.0))
        current += step
    return series


def last_complete_period(today: date, granularity: str) -> date:
    """Inicio del último período COMPLETO: el día/semana en curso es parcial y nunca entra en la serie
    (un 'hoy' a medias sesgaría naive/medias móviles y las ventanas de tendencia)."""
    return period_start(today - timedelta(days=PERIOD_DAYS[granularity]), granularity)


def load_series(granularity: str = GRANULARITY_DAILY, today: date | None = None, *,
                product_id: int | None = None) -> dict[int, TimeSeries]:
    """Series por producto desde la fuente canónica (solo lectura), hasta el último período completo."""
    today = today or institutional_today()
    end = last_complete_period(today, granularity)
    limit = end + timedelta(days=PERIOD_DAYS[granularity])  # exclusivo
    cutoff = demand_data_service.get_temporal_cutoff()
    events = demand_data_service.load_demand_events(cutoff, product_id=product_id)
    daily_map = demand_data_service.build_daily_series_by_product(events, cutoff)
    result: dict[int, TimeSeries] = {}
    for product_id, daily in daily_map.items():
        series = aggregate_daily(product_id, ((p.day, p.demand) for p in daily.points if p.day < limit),
                                 granularity, end=end)
        series.event_count = daily.original_event_count
        series.data_source = daily.data_source
        series.inconsistencies = daily.inconsistencies
        result[product_id] = series
    return result


def window_sum(series: TimeSeries, days: int, end: date, offset_days: int = 0) -> float:
    """Suma de demanda en la ventana (end-offset-days, end-offset]; útil para tendencias."""
    upper = end - timedelta(days=offset_days)
    lower = upper - timedelta(days=days)
    return sum(v for s, v in zip(series.starts, series.values) if lower < s <= upper)


# ---------------------------------------------------------------------------
# Elegibilidad
# ---------------------------------------------------------------------------
ELIGIBLE = "ELIGIBLE"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
INTERMITTENT = "INTERMITTENT"
INACTIVE = "INACTIVE"
NO_DEMAND = "NO_DEMAND"

ELIGIBILITY_LABELS = {
    ELIGIBLE: "Elegible", INSUFFICIENT_HISTORY: "Historial insuficiente",
    INTERMITTENT: "Demanda intermitente", INACTIVE: "Inactivo", NO_DEMAND: "Sin demanda",
}


@dataclass(frozen=True)
class EligibilityConfig:
    """Criterios configurables; los umbrales diarios reutilizan los del diagnóstico de suficiencia."""

    min_periods: int
    min_positive_periods: int
    limited_periods: int          # a partir de aquí cabe una regla descriptiva (tasa media)
    limited_positive_periods: int
    inactivity_periods: int       # períodos consecutivos finales en 0 => inactivo
    intermittent_zero_ratio: float = SPARSE_ZERO_RATIO


ELIGIBILITY_CONFIG = {
    GRANULARITY_DAILY: EligibilityConfig(SIMPLE_MIN_PERIODS, SIMPLE_MIN_POSITIVE, LIMITED_MIN_PERIODS,
                                         LIMITED_MIN_POSITIVE, inactivity_periods=90),
    GRANULARITY_WEEKLY: EligibilityConfig(8, 4, 4, 2, inactivity_periods=13),
}


@dataclass(frozen=True)
class Eligibility:
    status: str
    reason: str
    descriptive_allowed: bool  # regla descriptiva (tasa media) permitida aunque no haya modelo

    @property
    def label(self) -> str:
        return ELIGIBILITY_LABELS[self.status]


def classify_eligibility(series: TimeSeries, product_is_active: bool = True,
                         config: EligibilityConfig | None = None) -> Eligibility:
    cfg = config or ELIGIBILITY_CONFIG[series.granularity]
    if not product_is_active:
        return Eligibility(INACTIVE, "El producto está inactivo en el catálogo.", False)
    positive = series.positive_periods
    if positive == 0:
        return Eligibility(NO_DEMAND, "No hay períodos con demanda observada.", False)
    last_index = series.last_positive_index
    trailing_zeros = series.periods - 1 - (last_index if last_index is not None else -1)
    if trailing_zeros >= cfg.inactivity_periods:
        return Eligibility(INACTIVE, f"Sin demanda en los últimos {trailing_zeros} períodos "
                                     f"(umbral {cfg.inactivity_periods}).", False)
    limited = series.periods >= cfg.limited_periods and positive >= cfg.limited_positive_periods
    if series.periods < cfg.min_periods or positive < cfg.min_positive_periods:
        return Eligibility(INSUFFICIENT_HISTORY,
                           f"{series.periods} períodos y {positive} con demanda; se requieren "
                           f"≥{cfg.min_periods} y ≥{cfg.min_positive_periods}.", limited)
    zero_ratio = series.zero_ratio or 0.0
    if zero_ratio >= cfg.intermittent_zero_ratio:
        return Eligibility(INTERMITTENT, f"{zero_ratio:.0%} de períodos en cero "
                                         f"(umbral {cfg.intermittent_zero_ratio:.0%}).", True)
    return Eligibility(ELIGIBLE, f"{series.periods} períodos, {positive} con demanda, "
                                 f"{zero_ratio:.0%} en cero.", True)
