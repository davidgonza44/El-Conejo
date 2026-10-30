"""Extracción y combinación de demanda histórica y operativa.

Fase de diagnóstico: no pronostica, no reabastece y no modifica inventario.

Fuentes aprobadas (fuente canónica de demanda observada):
- Histórica: ``historical_demand_records`` de lotes CONFIRMADOS, vigentes,
  con ``include_in_demand`` y ``product_id`` válido.
- Operativa canónica: despacho físico (nota de entrega ``issued``).
  Una venta ``completed`` con el mismo producto, fecha comercial y cantidad
  se omite cuando ya existe una nota que la cubre. La venta sin despacho
  equivalente sí cuenta. Así no se suma dos veces la misma salida.

No se usa ``stock_movements``. El dataset experimental y su sello no pasan
por esta función. Cotizaciones, pedidos, notas canceladas y ventas no
``completed`` quedan fuera. Las fechas se convierten de UTC a la fecha
comercial de America/Caracas. Con lote histórico confirmado, ``period_end``
es solo histórico; la operativa empieza el día siguiente.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy.orm import load_only

from app.extensions import db
from app.models import DeliveryNote, DeliveryNoteItem, HistoricalDemandRecord, HistoricalImport, Sale, SaleLine
from app.models.delivery_note import STATUS_ISSUED
from app.models.sale import STATUS_COMPLETED
from app.models.historical_demand_record import (
    RECORD_STATUS_ACTIVE,
    RECORD_STATUS_ISSUED,
    RECORD_TYPE_CANCELLATION,
    RECORD_TYPE_CORRECTION,
    RECORD_TYPE_RETURN,
    RECORD_TYPE_SALE,
)
from app.models.historical_import import IMPORT_STATUS_CONFIRMED

SOURCE_HISTORICAL = "historical"
SOURCE_OPERATIONAL = "operational"
SOURCE_COMBINED = "combined"

ACTIVE_EFFECTIVE_STATUSES = (RECORD_STATUS_ISSUED, RECORD_STATUS_ACTIVE)
DEMAND_ADD_TYPES = (RECORD_TYPE_SALE, RECORD_TYPE_CORRECTION)
DEMAND_SUBTRACT_TYPES = (RECORD_TYPE_RETURN, RECORD_TYPE_CANCELLATION)

INCONSISTENCY_NEGATIVE_NET = "negative_net_demand"
INCONSISTENCY_UNLINKED_RETURN = "unlinked_return"
INCONSISTENCY_UNLINKED_CANCELLATION = "unlinked_cancellation"
INCONSISTENCY_RELATED_PRODUCT_MISMATCH = "related_product_mismatch"


def _as_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _iso_date(value: date | None) -> str | None:
    return value.isoformat() if value else None


@dataclass(frozen=True)
class TemporalCutoff:
    """Corte derivado de lotes históricos confirmados; nunca de fechas inventadas."""

    has_confirmed_historical_import: bool
    confirmed_import_count: int
    historical_period_start: date | None
    historical_period_end: date | None
    operational_starts_on: date | None

    def to_dict(self) -> dict:
        return {
            "has_confirmed_historical_import": self.has_confirmed_historical_import,
            "confirmed_import_count": self.confirmed_import_count,
            "historical_period_start": _iso_date(self.historical_period_start),
            "historical_period_end": _iso_date(self.historical_period_end),
            "operational_starts_on": _iso_date(self.operational_starts_on),
        }


@dataclass(frozen=True)
class DemandEvent:
    product_id: int
    event_date: date
    quantity: Decimal
    signed_quantity: Decimal
    source: str
    record_type: str
    applied: bool
    inconsistency_code: str | None = None


@dataclass
class DailyPoint:
    day: date
    demand: Decimal


@dataclass
class ProductDemandSeries:
    product_id: int
    points: list[DailyPoint] = field(default_factory=list)
    original_event_count: int = 0
    data_source: str | None = None
    inconsistencies: list[dict] = field(default_factory=list)
    historical_event_count: int = 0
    operational_event_count: int = 0

    @property
    def start_date(self) -> date | None:
        return self.points[0].day if self.points else None

    @property
    def end_date(self) -> date | None:
        return self.points[-1].day if self.points else None


def validate_operational_calendar(calendar: Iterable[date]) -> list[date]:
    """Explicit chronological business calendar; never infer closures from weekdays."""
    days = list(calendar)
    if any(type(day) is not date for day in days) or any(a >= b for a, b in zip(days, days[1:])):
        raise ValueError("Operational calendar must contain unique increasing dates")
    return days


def operational_points(daily, operational_calendar, end=None) -> list[DailyPoint]:
    """Aggregate already-canonical observations, without deduplication or prehistory zeros.

    Before the first positive observation the product is left-censored, not zero.
    Missing operational dates after that observation mean zero observed demand only.
    """
    days = validate_operational_calendar(operational_calendar)
    allowed = set(days)
    buckets = {}
    for day, value in daily:
        amount = _as_decimal(value)
        if day not in allowed or not amount.is_finite() or amount < 0:
            raise ValueError("Invalid operational observation or date outside supplied calendar")
        if end is not None and day > end:
            continue
        buckets[day] = buckets.get(day, Decimal(0)) + amount
    positive = [day for day, value in buckets.items() if value > 0]
    if not positive:
        return []
    first = min(positive)
    return [DailyPoint(day, buckets.get(day, Decimal(0))) for day in days
            if day >= first and (end is None or day <= end)]


def get_temporal_cutoff() -> TemporalCutoff:
    """Calcula el corte con ``period_start``/``period_end`` de lotes confirmados."""
    rows = (
        db.session.query(
            HistoricalImport.period_start,
            HistoricalImport.period_end,
        )
        .filter(HistoricalImport.status == IMPORT_STATUS_CONFIRMED)
        .all()
    )
    if not rows:
        return TemporalCutoff(
            has_confirmed_historical_import=False,
            confirmed_import_count=0,
            historical_period_start=None,
            historical_period_end=None,
            operational_starts_on=None,
        )

    starts = [row.period_start for row in rows if row.period_start is not None]
    ends = [row.period_end for row in rows if row.period_end is not None]
    period_start = min(starts) if starts else None
    period_end = max(ends) if ends else None
    operational_starts_on = (
        period_end + timedelta(days=1) if period_end is not None else None
    )
    return TemporalCutoff(
        has_confirmed_historical_import=True,
        confirmed_import_count=len(rows),
        historical_period_start=period_start,
        historical_period_end=period_end,
        operational_starts_on=operational_starts_on,
    )


def load_demand_events(cutoff: TemporalCutoff | None = None, *, product_id: int | None = None) -> list[DemandEvent]:
    """Carga eventos válidos de ambas fuentes sin solapar períodos."""
    cutoff = cutoff or get_temporal_cutoff()
    events: list[DemandEvent] = []
    events.extend(_load_historical_events(cutoff, product_id=product_id))
    events.extend(_load_operational_events(cutoff, product_id=product_id))
    return events


def build_daily_series_by_product(
    events: Iterable[DemandEvent] | None = None,
    cutoff: TemporalCutoff | None = None,
    *, operational_calendar: Iterable[date] | None = None,
) -> dict[int, ProductDemandSeries]:
    """Agrega por producto y día, rellena ceros y detecta netos negativos."""
    if events is None:
        cutoff = cutoff or get_temporal_cutoff()
        events = load_demand_events(cutoff)
    calendar = None if operational_calendar is None else validate_operational_calendar(operational_calendar)

    grouped: dict[int, list[DemandEvent]] = {}
    for event in events:
        grouped.setdefault(event.product_id, []).append(event)
    return {
        product_id: _build_product_series(product_id, product_events, operational_calendar=calendar)
        for product_id, product_events in grouped.items()
    }


def get_product_daily_series(
    product_id: int,
    events: Iterable[DemandEvent] | None = None,
    cutoff: TemporalCutoff | None = None,
) -> ProductDemandSeries:
    if events is None:
        cutoff = cutoff or get_temporal_cutoff()
        events = load_demand_events(cutoff, product_id=product_id)
    series_map = build_daily_series_by_product(events, cutoff)
    return series_map.get(product_id, ProductDemandSeries(product_id=product_id))


def _load_historical_events(cutoff: TemporalCutoff | None = None, *, product_id=None) -> list[DemandEvent]:
    records = (
        HistoricalDemandRecord.query.join(
            HistoricalImport,
            HistoricalDemandRecord.historical_import_id == HistoricalImport.id,
        )
        .options(
            load_only(
                HistoricalDemandRecord.id,
                HistoricalDemandRecord.product_id,
                HistoricalDemandRecord.event_date,
                HistoricalDemandRecord.quantity,
                HistoricalDemandRecord.record_type,
                HistoricalDemandRecord.related_record_id,
                HistoricalDemandRecord.include_in_demand,
                HistoricalDemandRecord.effective_status,
                HistoricalDemandRecord.historical_import_id,
            )
        )
        .filter(
            HistoricalImport.status == IMPORT_STATUS_CONFIRMED,
            HistoricalDemandRecord.include_in_demand.is_(True),
            HistoricalDemandRecord.effective_status.in_(ACTIVE_EFFECTIVE_STATUSES),
            HistoricalDemandRecord.product_id.isnot(None),
        )
    )
    if product_id is not None:
        records = records.filter(HistoricalDemandRecord.product_id == product_id)
    if cutoff and cutoff.historical_period_start is not None:
        records = records.filter(HistoricalDemandRecord.event_date >= cutoff.historical_period_start)
    if cutoff and cutoff.historical_period_end is not None:
        records = records.filter(HistoricalDemandRecord.event_date <= cutoff.historical_period_end)
    records = records.all()
    related_map = _load_related_records(records)
    events: list[DemandEvent] = []
    for record in records:
        events.append(_historical_event(record, related_map.get(record.related_record_id)))
    return events


def _load_related_records(records: list[HistoricalDemandRecord]) -> dict[int, HistoricalDemandRecord]:
    related_ids = {
        record.related_record_id
        for record in records
        if record.related_record_id is not None
    }
    if not related_ids:
        return {}
    related = (
        HistoricalDemandRecord.query.options(
            load_only(
                HistoricalDemandRecord.id,
                HistoricalDemandRecord.product_id,
                HistoricalDemandRecord.record_type,
            )
        )
        .filter(HistoricalDemandRecord.id.in_(related_ids))
        .all()
    )
    return {row.id: row for row in related}


def _historical_event(
    record: HistoricalDemandRecord,
    related: HistoricalDemandRecord | None,
) -> DemandEvent:
    quantity = _as_decimal(record.quantity)
    record_type = record.record_type
    product_id = int(record.product_id)

    if record_type in DEMAND_ADD_TYPES:
        return DemandEvent(
            product_id=product_id,
            event_date=record.event_date,
            quantity=quantity,
            signed_quantity=quantity,
            source=SOURCE_HISTORICAL,
            record_type=record_type,
            applied=True,
        )

    if record_type in DEMAND_SUBTRACT_TYPES:
        inconsistency = _return_inconsistency(record, related)
        applied = inconsistency is None
        signed = -quantity if applied else Decimal("0")
        return DemandEvent(
            product_id=product_id,
            event_date=record.event_date,
            quantity=quantity,
            signed_quantity=signed,
            source=SOURCE_HISTORICAL,
            record_type=record_type,
            applied=applied,
            inconsistency_code=inconsistency,
        )

    return DemandEvent(
        product_id=product_id,
        event_date=record.event_date,
        quantity=quantity,
        signed_quantity=Decimal("0"),
        source=SOURCE_HISTORICAL,
        record_type=record_type,
        applied=False,
        inconsistency_code=None,
    )


def _return_inconsistency(
    record: HistoricalDemandRecord,
    related: HistoricalDemandRecord | None,
) -> str | None:
    default_code = (
        INCONSISTENCY_UNLINKED_CANCELLATION
        if record.record_type == RECORD_TYPE_CANCELLATION
        else INCONSISTENCY_UNLINKED_RETURN
    )
    if record.related_record_id is None or related is None:
        return default_code
    if related.product_id != record.product_id:
        return INCONSISTENCY_RELATED_PRODUCT_MISMATCH
    if related.record_type not in DEMAND_ADD_TYPES:
        return default_code
    return None


def commercial_date(value: datetime) -> date:
    """Fecha comercial (America/Caracas) de un timestamp UTC naive guardado en BD."""
    from app.services.inventory_service import COMMERCIAL_ZONE

    return value.replace(tzinfo=timezone.utc).astimezone(COMMERCIAL_ZONE).date()


def utc_naive_start_of_commercial_day(day: date) -> datetime:
    """00:00 America/Caracas de ``day``, guardado como UTC naive (mismo contrato que sold_at)."""
    from app.services.inventory_service import COMMERCIAL_ZONE

    local = datetime.combine(day, time.min, tzinfo=COMMERCIAL_ZONE)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def commercial_datetime_as_utc_naive(day: date, hour: int = 0, minute: int = 0) -> datetime:
    """Instante comercial (Caracas) expresado como UTC naive para sembrar/comparar timestamps."""
    from app.services.inventory_service import COMMERCIAL_ZONE

    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=COMMERCIAL_ZONE)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def _operational_event(product_id: int, when: datetime, quantity) -> DemandEvent:
    quantity = _as_decimal(quantity)
    return DemandEvent(
        product_id=int(product_id),
        event_date=commercial_date(when),
        quantity=quantity,
        signed_quantity=quantity,
        source=SOURCE_OPERATIONAL,
        record_type=RECORD_TYPE_SALE,
        applied=True,
    )


def _quantity_key(quantity):
    number = _as_decimal(quantity)
    return int(number) if number == number.to_integral_value() else number


def _load_operational_events(cutoff: TemporalCutoff, *, product_id=None) -> list[DemandEvent]:
    """Despacho primero. Una venta equivalente no se vuelve a contar."""
    notes = (
        db.session.query(DeliveryNoteItem.product_id, DeliveryNote.created_at, DeliveryNoteItem.quantity)
        .join(DeliveryNote, DeliveryNoteItem.delivery_note_id == DeliveryNote.id)
        .filter(DeliveryNote.status == STATUS_ISSUED)
    )
    sales = (
        db.session.query(SaleLine.product_id, Sale.sold_at, SaleLine.quantity)
        .join(Sale, SaleLine.sale_id == Sale.id)
        .filter(Sale.status == STATUS_COMPLETED)
    )
    if product_id is not None:
        notes = notes.filter(DeliveryNoteItem.product_id == product_id)
        sales = sales.filter(SaleLine.product_id == product_id)
    if cutoff.operational_starts_on is not None:
        starts = utc_naive_start_of_commercial_day(cutoff.operational_starts_on)
        notes = notes.filter(DeliveryNote.created_at >= starts)
        sales = sales.filter(Sale.sold_at >= starts)

    events: list[DemandEvent] = []
    covered: dict[tuple, int] = {}
    for pid, created_at, quantity in notes.all():
        if created_at is None:
            continue
        event = _operational_event(pid, created_at, quantity)
        events.append(event)
        key = (event.product_id, event.event_date, _quantity_key(event.quantity))
        covered[key] = covered.get(key, 0) + 1
    for pid, sold_at, quantity in sales.all():
        if sold_at is None:
            continue
        event = _operational_event(pid, sold_at, quantity)
        key = (event.product_id, event.event_date, _quantity_key(event.quantity))
        if covered.get(key, 0) > 0:
            covered[key] -= 1
            continue
        events.append(event)
    if cutoff.operational_starts_on is not None:
        events = [event for event in events if event.event_date >= cutoff.operational_starts_on]
    return events


def _build_product_series(
    product_id: int, events: list[DemandEvent], *, operational_calendar=None
) -> ProductDemandSeries:
    daily_net: dict[date, Decimal] = {}
    inconsistencies: list[dict] = []
    applied_sources: set[str] = set()
    historical_count = 0
    operational_count = 0
    original_event_count = 0

    for event in events:
        if event.inconsistency_code:
            inconsistencies.append(
                {
                    "code": event.inconsistency_code,
                    "date": event.event_date.isoformat(),
                    "message": _inconsistency_message(event),
                }
            )
        if not event.applied:
            continue

        original_event_count += 1
        daily_net[event.event_date] = (
            daily_net.get(event.event_date, Decimal("0")) + event.signed_quantity
        )
        applied_sources.add(event.source)
        if event.source == SOURCE_HISTORICAL:
            historical_count += 1
        elif event.source == SOURCE_OPERATIONAL:
            operational_count += 1

    if not daily_net:
        return ProductDemandSeries(
            product_id=product_id,
            inconsistencies=inconsistencies,
        )

    start = min(daily_net)
    end = max(daily_net)
    points: list[DailyPoint] = []
    if operational_calendar is None:
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    else:
        days = []  # The operational branch never materializes civil-day zeros.
    for current in days:
        demand = daily_net.get(current, Decimal("0"))
        if demand < 0:
            inconsistencies.append(
                {
                    "code": INCONSISTENCY_NEGATIVE_NET,
                    "date": current.isoformat(),
                    "message": (
                        "La demanda neta del "
                        f"{current.isoformat()} es negativa ({demand}); "
                        "no se reemplaza silenciosamente por cero."
                    ),
                }
            )
        points.append(DailyPoint(day=current, demand=demand))

    if operational_calendar is not None:
        points = operational_points(daily_net.items(), operational_calendar)

    data_source: str | None
    if SOURCE_HISTORICAL in applied_sources and SOURCE_OPERATIONAL in applied_sources:
        data_source = SOURCE_COMBINED
    elif SOURCE_HISTORICAL in applied_sources:
        data_source = SOURCE_HISTORICAL
    elif SOURCE_OPERATIONAL in applied_sources:
        data_source = SOURCE_OPERATIONAL
    else:
        data_source = None

    return ProductDemandSeries(
        product_id=product_id,
        points=points,
        original_event_count=original_event_count,
        data_source=data_source,
        inconsistencies=inconsistencies,
        historical_event_count=historical_count,
        operational_event_count=operational_count,
    )


def _inconsistency_message(event: DemandEvent) -> str:
    day = event.event_date.isoformat()
    if event.inconsistency_code == INCONSISTENCY_RELATED_PRODUCT_MISMATCH:
        return (
            f"Devolución o anulación del {day} apunta a un producto distinto; "
            "no se restó de la demanda."
        )
    if event.inconsistency_code == INCONSISTENCY_UNLINKED_CANCELLATION:
        return (
            f"Anulación del {day} sin vínculo válido a una venta; "
            "no se restó de la demanda."
        )
    return (
        f"Devolución del {day} sin vínculo válido a una venta; "
        "no se restó de la demanda."
    )
