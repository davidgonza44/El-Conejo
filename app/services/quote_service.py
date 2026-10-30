"""Cotizaciones (Quotes Core v1).

Una cotización NUNCA descuenta, reserva ni mueve stock: no llama a inventory_service ni
crea StockMovement. Secuencia de ``create_quote``: rechazar campos autoritativos ->
validar cliente/validez/estado/descuento/notas/renglones -> insertar con ``quote_number``
NULL, ``flush`` y fijar ``COT-{id:06d}`` -> renglones con precio copiado -> totales Decimal
ROUND_HALF_UP -> un único commit (rollback total ante cualquier error).

"Vencida" es un estado computado (pending/sent con ``valid_until`` < hoy Caracas); los GET
nunca lo escriben en la BD. Transiciones: pending -> sent|accepted|rejected;
sent -> accepted|rejected; accepted y rejected son terminales.
"""
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import and_

from app.extensions import db
from app.models import Customer, Quote, QuoteLine
from app.models.quote import (NUMBER_PREFIX, OPEN_STATUSES, QUOTE_STATUSES, STATUS_ACCEPTED, STATUS_EXPIRED,
                              STATUS_LABELS, STATUS_PENDING, TRANSITIONS, VALIDITY_DAYS)
from app.services import sales_common as common
from app.services.exceptions import ConflictError, NotFoundError, ValidationError

_FORBIDDEN_FIELDS = {"seller_id", "quote_number", "quoted_at", "valid_until", "subtotal", "discount_total", "total"}
_FILTER_STATUSES = QUOTE_STATUSES + (STATUS_EXPIRED,)
_SORT_COLUMNS = {"date": Quote.quoted_at, "number": Quote.id, "total": Quote.total, "valid_until": Quote.valid_until,
                 "customer": Customer.name, "status": Quote.status, "validity": Quote.validity_days}


def _validity(data: dict) -> int:
    value = data.get("validity_days")
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value)
    if isinstance(value, bool) or value not in VALIDITY_DAYS:
        raise ValidationError(f"El campo 'validity_days' debe ser uno de: {', '.join(map(str, VALIDITY_DAYS))}.")
    return value


def create_quote(data: dict) -> Quote:
    common.reject(data, _FORBIDDEN_FIELDS, "la cotización")
    customer = common.resolve_customer(data)
    validity = _validity(data)
    status = common.choice(data, "status", QUOTE_STATUSES, default=STATUS_PENDING)
    discount = common.parse_discount(data.get("discount_percent"))
    notes = common.optional_text(data, "notes", 500)
    entries = common.parse_lines(data)
    today = common.commercial_today()
    try:
        quote = Quote(customer_id=customer.id, seller_id=common.seller_id(), quoted_at=common.utc_now(),
                      validity_days=validity, valid_until=today + timedelta(days=validity),
                      discount_percent=discount, status=status, notes=notes)
        common.assign_number(quote, "quote_number", NUMBER_PREFIX)
        lines = [common.snapshot_line(QuoteLine, product, quantity, quote_id=quote.id) for product, quantity in entries]
        db.session.add_all(lines)
        quote.subtotal = common.quantize(sum((line.line_subtotal for line in lines), Decimal(0)))
        quote.discount_total = common.quantize(quote.subtotal * discount / Decimal(100))
        quote.total = common.quantize(quote.subtotal - quote.discount_total)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return quote


def get_quote(quote_number: str) -> Quote:
    quote = Quote.query.filter(Quote.quote_number == quote_number).first()
    if quote is None:
        raise NotFoundError(f"La cotización {quote_number} no existe.")
    return quote


def change_status(quote_number: str, data: dict) -> Quote:
    quote = get_quote(quote_number)
    new_status = common.choice(data, "status", QUOTE_STATUSES)
    if new_status not in TRANSITIONS[quote.status]:
        raise ConflictError(
            f"La cotización {quote_number} está '{STATUS_LABELS[quote.status]}' y no puede pasar a "
            f"'{STATUS_LABELS[new_status]}'."
        )
    if new_status == STATUS_ACCEPTED and quote.effective_status(common.commercial_today()) == STATUS_EXPIRED:
        raise ConflictError(f"La cotización {quote_number} está vencida; no puede aceptarse.")
    quote.status = new_status
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return quote


def serialize(quote: Quote, detail: bool = False) -> dict:
    today = common.commercial_today()
    return quote.to_dict(today) if detail else quote.to_summary_dict(today)


def _status_filter(query, status: str, today):
    """``expired`` es computado; pending/sent excluyen las vencidas para que los grupos no se solapen."""
    if status not in _FILTER_STATUSES:
        raise ValidationError(f"Estado inválido. Valores permitidos: {', '.join(_FILTER_STATUSES)}.")
    if status == STATUS_EXPIRED:
        return query.filter(and_(Quote.status.in_(OPEN_STATUSES), Quote.valid_until < today))
    if status in OPEN_STATUSES:
        return query.filter(and_(Quote.status == status, Quote.valid_until >= today))
    return query.filter(Quote.status == status)


def list_quotes(params: dict) -> dict:
    today = common.commercial_today()
    query = Quote.query.join(Customer, Quote.customer_id == Customer.id)
    search = (params.get("search") or "").strip()
    if search:
        query = common.apply_search(query, Quote, QuoteLine, QuoteLine.quote_id, Quote.quote_number, search)
    status = (params.get("status") or "").strip().lower()
    if status:
        query = _status_filter(query, status, today)
    query = common.apply_datetime_range(query, Quote.quoted_at, params, "date_from", "date_to")
    query = common.apply_id_filters(query, Quote, QuoteLine, QuoteLine.quote_id, params)
    query = common.apply_money_range(query, Quote.total, params, "total_min", "total_max")
    validity = common.int_param(params, "validity_days")
    if validity is not None:
        if validity not in VALIDITY_DAYS:
            raise ValidationError(f"'validity_days' debe ser uno de: {', '.join(map(str, VALIDITY_DAYS))}.")
        query = query.filter(Quote.validity_days == validity)
    query = query.order_by(common.order_clause(params, _SORT_COLUMNS, "-date"), Quote.id.desc())
    return common.paginate(query, params, Quote, QuoteLine, lambda quote: quote.to_summary_dict(today))


def quote_options() -> dict:
    """Valores canónicos con etiqueta en español para la UI live (filtros y drawer)."""
    return {
        "statuses": [{"value": k, "label": STATUS_LABELS[k]} for k in QUOTE_STATUSES],
        "filter_statuses": [{"value": k, "label": STATUS_LABELS[k]} for k in _FILTER_STATUSES],
        "validity_days": [{"value": d, "label": f"{d} días"} for d in VALIDITY_DAYS],
        "sellers": common.sellers_of(Quote),
        "today": common.commercial_today().isoformat(),
    }
