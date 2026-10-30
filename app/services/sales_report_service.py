"""Reportes de ventas (SOLO LECTURA): cinco pestañas sobre ventas ``completed`` reales.

Reutiliza el período comercial (America/Caracas), la comparación con el período anterior
de igual duración y las agrupaciones del Dashboard de Ventas. Nunca usa notas de entrega,
importaciones históricas ni fixtures. Dinero en ``Decimal`` cuantizado; el controlador y
los exportadores dan formato. ``rows`` trae TODAS las filas del período (la paginación es
de presentación) para que la exportación nunca dependa de la página visible.

Limitación histórica compartida con el dashboard: SaleLine no guarda la categoría al vender;
"por categoría" agrupa por la categoría ACTUAL del producto.
"""
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, literal, or_
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Category, Customer, Product, Sale, SaleLine
from app.models.sale import PAYMENT_METHODS, STATUS_COMPLETED
from app.services.exceptions import NotFoundError, ValidationError
from app.services.product_history_service import previous_range
from app.services.sales_common import commercial_today, like_term, quantize, utc_boundary
from app.services.sales_dashboard_service import (MONTHS, _by_category, _comparison, _completed, _day,
                                                  _month_start, _monthly, _top_customers, _totals, period)
from app.utils.permissions import response_includes_sale_price

TABS = [('summary', 'Resumen'), ('products', 'Ventas por producto'), ('customers', 'Ventas por cliente'),
        ('categories', 'Ventas por categoría'), ('payments', 'Métodos de pago')]
LEGACY_TABS = {'trends': 'summary'}
# Las ventas no registran moneda de origen: los importes no se presentan como USD confirmados.
CURRENCY_NOTE = 'Importes sin moneda de origen registrada: se presentan como unidades monetarias, sin convertir ni asumir USD.'
TOP = 5
SLICES = 6
MAX_QUERY = 100
_ZERO = Decimal('0.00')
_ESC = '\\'


def _q(params: dict) -> str:
    value = params.get('q') or ''
    if not isinstance(value, str):
        raise ValidationError("El parámetro 'q' debe ser texto.")
    return value.strip()[:MAX_QUERY]


def _category(params: dict) -> int | None:
    value = params.get('category')
    if value in (None, ''):
        return None
    if not str(value).isdigit() or not 1 <= int(value) <= 2_147_483_647:
        raise ValidationError("El parámetro 'category' debe ser un id de categoría.")
    if db.session.get(Category, int(value)) is None:
        raise NotFoundError('Categoría no encontrada.')
    return int(value)


def _range(start: date, end: date):
    return (Sale.status == STATUS_COMPLETED, Sale.sold_at >= utc_boundary(start),
            Sale.sold_at < utc_boundary(end + timedelta(days=1)))


def _completed_lines(*columns):
    return (db.session.query(*columns).select_from(SaleLine)
            .join(Sale, Sale.id == SaleLine.sale_id).filter(Sale.status == STATUS_COMPLETED))


def _lines(start: date, end: date, *columns):
    return _completed_lines(*columns).filter(*_range(start, end))


def _pct(current, previous):
    """Variación porcentual real; ``None`` sin denominador (nunca infinito ni 0 inventado)."""
    previous = Decimal(previous or 0)
    return round(float((Decimal(current or 0) - previous) * 100 / previous), 1) if previous > 0 else None


def _compare(current, previous) -> dict:
    return _comparison(current, previous, lambda value: value)


def _mean(values):
    values = [Decimal(str(v)) for v in values if v is not None]
    return quantize(sum(values, Decimal(0)) / len(values)) if values else None


def _slices(items: list[dict]) -> dict:
    """Máximo ``SLICES`` porciones; el resto se agrupa como "Otros" (id None). Porcentajes sobre el total."""
    if len(items) > SLICES:
        rest = items[SLICES - 1:]
        items = items[:SLICES - 1] + [{'id': None, 'name': 'Otros', 'value': sum((i['value'] for i in rest), 0)}]
    total = sum((i['value'] for i in items), 0)
    for item in items:
        item['percent'] = round(float(Decimal(item['value']) * 100 / Decimal(total)), 1) if total else 0
    return {'total': total, 'items': items}


def _share(rows: list[dict], total) -> None:
    total = Decimal(total or 0)
    for row in rows:
        row['percent'] = round(float(row['revenue'] * 100 / total), 1) if total else 0


def _evolution(today: date, query, keys: list, names: dict) -> dict:
    """Serie mensual (12 meses comerciales) por clave; ``query`` devuelve (clave, sold_at, valor).
    Meses anteriores a la primera venta real van como ``None`` (misma regla que el dashboard)."""
    months = [_month_start(today, MONTHS - 1 - i) for i in range(MONTHS)]
    index = {(m.year, m.month): i for i, m in enumerate(months)}
    series = {key: [Decimal(0)] * MONTHS for key in keys}
    earliest = db.session.query(func.min(Sale.sold_at)).filter(Sale.status == STATUS_COMPLETED).scalar()
    coverage = _day(earliest).replace(day=1) if earliest else None
    if keys and coverage is not None:
        for key, sold_at, value in query.filter(Sale.sold_at >= utc_boundary(months[0])).all():
            slot = _day(sold_at)
            position = index.get((slot.year, slot.month))
            if position is not None and key in series:
                series[key][position] += Decimal(value or 0)
    return {'months': months, 'coverage_from': coverage,
            'series': [{'key': key, 'name': names[key],
                        'values': [v if coverage and m >= coverage else None for m, v in zip(months, series[key])]}
                       for key in keys]}


# --- Ventas por producto -------------------------------------------------------------

def _product_rows(start: date, end: date, q: str = '', category_id: int | None = None) -> list[dict]:
    units, revenue = func.sum(SaleLine.quantity), func.sum(SaleLine.line_total)
    columns = (Product.id, Product.code, Product.name, Product.image_url, Category.name, Product.current_stock,
               Product.minimum_stock, Product.is_active)
    query = (_lines(start, end, *columns, units, revenue)
             .join(Product, Product.id == SaleLine.product_id).join(Category, Category.id == Product.category_id))
    if q:
        term = like_term(q)
        query = query.filter(or_(Product.name.ilike(term, escape=_ESC), Product.code.ilike(term, escape=_ESC),
                                 Category.name.ilike(term, escape=_ESC)))
    if category_id is not None:
        query = query.filter(Product.category_id == category_id)
    rows = query.group_by(*columns).order_by(revenue.desc(), Product.name.asc()).all()
    return [{'id': pid, 'code': code, 'name': name, 'image_url': image, 'category': category, 'stock': stock,
             'minimum_stock': minimum, 'is_active': bool(active), 'units': int(qty or 0),
             'revenue': quantize(Decimal(amount or 0))}
            for pid, code, name, image, category, stock, minimum, active, qty, amount in rows]


def _product_totals(start: date, end: date) -> dict:
    products, units, revenue = _lines(start, end, func.count(func.distinct(SaleLine.product_id)),
                                      func.coalesce(func.sum(SaleLine.quantity), 0),
                                      func.coalesce(func.sum(SaleLine.line_total), 0)).one()
    revenue = quantize(Decimal(revenue or 0))
    return {'products': int(products or 0), 'units': int(units or 0), 'revenue': revenue,
            'average': quantize(revenue / products) if products else _ZERO}


def _products_by_category(start: date, end: date) -> dict:
    count = func.count(func.distinct(SaleLine.product_id))
    rows = (_lines(start, end, Category.id, Category.name, count)
            .join(Product, Product.id == SaleLine.product_id).join(Category, Category.id == Product.category_id)
            .group_by(Category.id, Category.name).order_by(count.desc(), Category.name.asc()).all())
    return _slices([{'id': cid, 'name': name, 'value': int(n)} for cid, name, n in rows])


def _products(start, end, previous, today, q, category_id=None) -> dict:
    current, before = _product_totals(start, end), _product_totals(*previous)
    ranking = _product_rows(start, end)
    rows = ranking if not (q or category_id) else _product_rows(start, end, q, category_id)
    _share(rows, current['revenue'])
    _share(ranking, current['revenue'])
    top = ranking[:TOP]
    ids = [r['id'] for r in top]
    evolution = _evolution(today, _completed_lines(SaleLine.product_id, Sale.sold_at, SaleLine.line_total)
                           .filter(SaleLine.product_id.in_(ids)), ids, {r['id']: r['name'] for r in top})
    return {
        'kpis': {'products': (current['products'], _compare(current['products'], before['products'])),
                 'units': (current['units'], _compare(current['units'], before['units'])),
                 'revenue': (current['revenue'], _compare(current['revenue'], before['revenue'])),
                 'average': (current['average'], _compare(current['average'], before['average']))},
        'rows': rows, 'ranking': top, 'distribution': _products_by_category(start, end),
        'featured': top[0] if top else None, 'evolution': evolution,
        'category': db.session.get(Category, category_id).name if category_id else None,
    }


# --- Ventas por cliente --------------------------------------------------------------

def _customer_rows(start: date, end: date, q: str = '') -> list[dict]:
    count, revenue = func.count(Sale.id), func.sum(Sale.total)
    columns = (Customer.id, Customer.name, Customer.tax_id, Customer.is_active, Customer.is_walk_in)
    query = _completed(start, end).join(Customer, Customer.id == Sale.customer_id).with_entities(*columns, count, revenue)
    if q:
        term = like_term(q)
        query = query.filter(or_(Customer.name.ilike(term, escape=_ESC), Customer.tax_id.ilike(term, escape=_ESC)))
    rows = query.group_by(*columns).order_by(revenue.desc(), Customer.name.asc()).all()
    units = dict(_lines(start, end, Sale.customer_id, func.sum(SaleLine.quantity)).group_by(Sale.customer_id).all())
    result = []
    for cid, name, tax_id, active, walk_in, n, amount in rows:
        amount = quantize(Decimal(amount or 0))
        result.append({'id': cid, 'name': name, 'tax_id': tax_id, 'is_active': bool(active), 'is_walk_in': bool(walk_in),
                       'sales': int(n), 'units': int(units.get(cid) or 0), 'revenue': amount,
                       'average': quantize(amount / n) if n else _ZERO})
    return result


def _customer_totals(start: date, end: date) -> dict:
    # "Público en general" agrupa ventas anónimas: no cuenta como cliente identificable ni recurrente.
    per_customer = [int(n) for _, n in _completed(start, end).join(Customer, Customer.id == Sale.customer_id)
                    .filter(Customer.is_walk_in.is_(False))
                    .with_entities(Sale.customer_id, func.count(Sale.id)).group_by(Sale.customer_id).all()]
    active = len(per_customer)
    recurring = sum(1 for n in per_customer if n >= 2)
    totals = _totals(start, end)
    frequency = [('1 compra', sum(1 for n in per_customer if n == 1)),
                 ('2 a 3 compras', sum(1 for n in per_customer if 2 <= n <= 3)),
                 ('4 o más compras', sum(1 for n in per_customer if n >= 4))]
    return {'active': active, 'recurring_percent': round(recurring * 100 / active, 1) if active else 0,
            'revenue': totals['revenue'], 'average': totals['average'], 'count': totals['count'],
            'frequency': [{'id': None, 'name': label, 'value': value} for label, value in frequency if value]}


def _activity(start: date, end: date) -> list[dict]:
    sales = (_completed(start, end).options(joinedload(Sale.customer))
             .order_by(Sale.sold_at.desc(), Sale.id.desc()).limit(TOP).all())
    return [{'sale_number': s.sale_number, 'customer': s.customer.name if s.customer else '—',
             'sold_at': s.sold_at, 'total': quantize(Decimal(s.total))} for s in sales]


def _customers(start, end, previous, today, q) -> dict:
    current, before = _customer_totals(start, end), _customer_totals(*previous)
    ranking = _customer_rows(start, end)
    rows = ranking if not q else _customer_rows(start, end, q)
    _share(rows, current['revenue'])
    _share(ranking, current['revenue'])
    top = ranking[:TOP]
    ids = [r['id'] for r in top]
    evolution = _evolution(today, Sale.query.filter(Sale.status == STATUS_COMPLETED, Sale.customer_id.in_(ids))
                           .with_entities(Sale.customer_id, Sale.sold_at, Sale.total),
                           ids, {r['id']: r['name'] for r in top})
    return {
        'kpis': {'active': (current['active'], _compare(current['active'], before['active'])),
                 'recurring': (current['recurring_percent'], _compare(current['recurring_percent'], before['recurring_percent'])),
                 'revenue': (current['revenue'], _compare(current['revenue'], before['revenue'])),
                 'average': (current['average'], _compare(current['average'], before['average']))},
        'rows': rows, 'ranking': top, 'distribution': _slices(current['frequency']),
        'activity': _activity(start, end), 'evolution': evolution,
    }


# --- Ventas por categoría ------------------------------------------------------------

def _category_rows(start: date, end: date, q: str = '') -> list[dict]:
    products, units, revenue = func.count(func.distinct(SaleLine.product_id)), func.sum(SaleLine.quantity), func.sum(SaleLine.line_total)
    columns = (Category.id, Category.name, Category.color, Category.icon)
    query = (_lines(start, end, *columns, products, units, revenue)
             .join(Product, Product.id == SaleLine.product_id).join(Category, Category.id == Product.category_id))
    if q:
        query = query.filter(Category.name.ilike(like_term(q), escape=_ESC))
    rows = query.group_by(*columns).order_by(revenue.desc(), Category.name.asc()).all()
    result = []
    for cid, name, color, icon, n, qty, amount in rows:
        amount = quantize(Decimal(amount or 0))
        result.append({'id': cid, 'name': name, 'color': color, 'icon': icon, 'products': int(n), 'units': int(qty or 0),
                       'revenue': amount, 'average_unit': quantize(amount / qty) if qty else _ZERO})
    return result


def _categories(start, end, previous, today, q) -> dict:
    ranking, before = _category_rows(start, end), _category_rows(*previous)
    previous_by_id = {r['id']: r for r in before}
    rows = ranking if not q else _category_rows(start, end, q)
    revenue = sum((r['revenue'] for r in ranking), _ZERO)
    previous_revenue = sum((r['revenue'] for r in before), _ZERO)
    for row in ranking + ([] if rows is ranking else rows):
        earlier = previous_by_id.get(row['id'])
        row['previous_revenue'] = earlier['revenue'] if earlier else _ZERO
        row['change_percent'] = _pct(row['revenue'], row['previous_revenue'])
    _share(rows, revenue)
    _share(ranking, revenue)
    changes = [r['change_percent'] for r in ranking if r['change_percent'] is not None]
    leader = ranking[0] if ranking else None
    top = ranking[:SLICES]
    ids = [r['id'] for r in top]
    evolution = _evolution(today, _completed_lines(Product.category_id, Sale.sold_at, SaleLine.line_total)
                           .join(Product, Product.id == SaleLine.product_id).filter(Product.category_id.in_(ids)),
                           ids, {r['id']: r['name'] for r in top})
    return {
        'kpis': {'active': (len(ranking), _compare(len(ranking), len(before))),
                 'revenue': (revenue, _compare(revenue, previous_revenue)),
                 'leader': (leader, None),
                 'growth': (_mean(changes), None), 'growth_basis': len(changes)},
        'rows': rows, 'ranking': top,
        'distribution': _slices([{'id': r['id'], 'name': r['name'], 'value': r['revenue']} for r in ranking]),
        'evolution': evolution,
        'summary': {'growth': max((r for r in ranking if r['change_percent'] is not None), key=lambda r: r['change_percent'], default=None),
                    'ticket': max(ranking, key=lambda r: r['average_unit'], default=None),
                    'share': leader},
    }


# --- Métodos de pago -----------------------------------------------------------------

def _payment_rows(start: date, end: date) -> list[dict]:
    count, revenue = func.count(Sale.id), func.sum(Sale.total)
    rows = (_completed(start, end).with_entities(Sale.payment_method, count, revenue)
            .group_by(Sale.payment_method).order_by(revenue.desc(), Sale.payment_method.asc()).all())
    result = []
    for method, n, amount in rows:
        amount = quantize(Decimal(amount or 0))
        result.append({'id': method, 'method': method, 'name': PAYMENT_METHODS.get(method, method), 'count': int(n),
                       'revenue': amount, 'average': quantize(amount / n) if n else _ZERO})
    return result


def _weighted_ticket(rows):
    count = sum(r['count'] for r in rows)
    return quantize(sum((r['revenue'] for r in rows), _ZERO) / count) if count else None


def _payments(start, end, previous, today, q) -> dict:
    ranking, before = _payment_rows(start, end), _payment_rows(*previous)
    previous_by_id = {r['id']: r for r in before}
    revenue = sum((r['revenue'] for r in ranking), _ZERO)
    for row in ranking:
        earlier = previous_by_id.get(row['id'])
        row['previous_count'] = earlier['count'] if earlier else 0
        row['previous_revenue'] = earlier['revenue'] if earlier else _ZERO
        row['change_percent'] = _pct(row['revenue'], row['previous_revenue'])
    _share(ranking, revenue)
    rows = [r for r in ranking if q.lower() in r['name'].lower()] if q else ranking
    # Ticket promedio por venta ponderado por transacciones (no media simple de promedios por método).
    average, previous_average = _weighted_ticket(ranking), _weighted_ticket(before)
    with_previous = [r for r in ranking if r['change_percent'] is not None]
    ids = [r['id'] for r in ranking]
    evolution = _evolution(today, Sale.query.filter(Sale.status == STATUS_COMPLETED, Sale.payment_method.in_(ids))
                           .with_entities(Sale.payment_method, Sale.sold_at, literal(1)),
                           ids, {r['id']: r['name'] for r in ranking})
    return {
        'kpis': {'active': (len(ranking), _compare(len(ranking), len(before))), 'configured': len(PAYMENT_METHODS),
                 'revenue': (revenue, _compare(revenue, sum((r['revenue'] for r in before), _ZERO))),
                 'main': (ranking[0] if ranking else None, None),
                 'average': (average, _compare(average or 0, previous_average or 0))},
        'rows': rows, 'ranking': ranking,
        'distribution': _slices([{'id': r['id'], 'name': r['name'], 'value': r['count']} for r in ranking]),
        'evolution': evolution,
        'summary': {'share': ranking[0] if ranking else None,
                    'ticket': max(ranking, key=lambda r: r['average'], default=None),
                    'growth': max(with_previous, key=lambda r: r['change_percent'], default=None),
                    'stable': min(with_previous, key=lambda r: abs(r['change_percent']), default=None)},
    }


# --- Resumen -------------------------------------------------------------------------

def _active_customers(start: date, end: date) -> int:
    """Clientes identificados con ventas; las ventas a "Público en general" no suman clientes."""
    return int(_completed(start, end).join(Customer, Customer.id == Sale.customer_id)
               .filter(Customer.is_walk_in.is_(False))
               .with_entities(func.count(func.distinct(Sale.customer_id))).scalar() or 0)


def _summary(start, end, previous, today, q) -> dict:
    current, before = _totals(start, end), _totals(*previous)
    customers, previous_customers = _active_customers(start, end), _active_customers(*previous)
    kpis = {'revenue': (current['revenue'], _compare(current['revenue'], before['revenue'])),
            'count': (current['count'], _compare(current['count'], before['count'])),
            'average': (current['average'], _compare(current['average'], before['average'])),
            'customers': (customers, _compare(customers, previous_customers))}
    products, customer_rows = _product_rows(start, end), _customer_rows(start, end)
    _share(products, current['revenue'])
    payments = _payment_rows(start, end)
    by_category = _by_category(start, end)
    growth = max(((key, value[1]['change_percent']) for key, value in kpis.items() if value[1]['change_percent'] is not None),
                 key=lambda item: item[1], default=None)
    return {
        'kpis': kpis, 'monthly': _monthly(today), 'by_category': by_category,
        'top_products': products[:TOP], 'top_customers': _top_customers(start, end)['items'],
        'rows': [], 'products': products, 'customers': customer_rows,
        'executive': {'category': by_category['items'][0] if by_category['items'] else None,
                      'growth': growth,
                      'ticket': max(customer_rows, key=lambda r: r['average'], default=None),
                      'product': products[0] if products else None,
                      'payment': payments[0] if payments else None},
    }


_BUILDERS = {'summary': _summary, 'products': _products, 'customers': _customers,
             'categories': _categories, 'payments': _payments}


def normalize_tab(tab) -> str:
    tab = tab if isinstance(tab, str) else ''
    tab = LEGACY_TABS.get(tab, tab)
    if tab not in _BUILDERS:
        raise ValidationError('Pestaña de reporte inválida.')
    return tab


PHYSICAL_COLUMNS = {
    'summary': [('name', 'Categoría', 95), ('products', 'Productos', 35), ('units', 'Unidades', 30), ('sales', 'Ventas', 30)],
    'products': [('name', 'Producto', 65), ('code', 'Código', 25), ('category', 'Categoría', 45), ('units', 'Unidades', 25), ('sales', 'Ventas', 30)],
    'customers': [('name', 'Cliente', 90), ('tax_id', 'RIF', 40), ('units', 'Unidades', 30), ('sales', 'Ventas', 30)],
    'categories': [('name', 'Categoría', 95), ('products', 'Productos', 35), ('units', 'Unidades', 30), ('sales', 'Ventas', 30)],
    'payments': [('name', 'Método', 130), ('units', 'Unidades', 30), ('sales', 'Transacciones', 30)],
}


def _physical_totals(start, end):
    sales, customers = _completed(start, end).with_entities(
        func.count(Sale.id), func.count(func.distinct(Sale.customer_id))).one()
    units, products = _completed(start, end).join(SaleLine).with_entities(
        func.sum(SaleLine.quantity), func.count(func.distinct(SaleLine.product_id))).one()
    return [('Ventas realizadas', int(sales or 0)), ('Unidades vendidas', int(units or 0)),
            ('Productos vendidos', int(products or 0)), ('Clientes atendidos', int(customers or 0))]


def _physical_rows(tab, start, end, q):
    """Consulta únicamente cantidades; ni las filas ni el orden dependen de dinero."""
    query = _completed(start, end).join(SaleLine).join(Product)
    units, sales = func.sum(SaleLine.quantity), func.count(func.distinct(Sale.id))
    if tab == 'products':
        query = query.outerjoin(Category, Category.id == Product.category_id)
        dimensions = (Product.id, Product.name, Product.code, Category.name)
        fields = (Product.name.label('name'), Product.code.label('code'),
                  func.coalesce(Category.name, 'Sin categoría').label('category'))
        search = or_(Product.name.ilike(like_term(q), escape='\\'), Product.code.ilike(like_term(q), escape='\\'),
                     Category.name.ilike(like_term(q), escape='\\'))
    elif tab == 'customers':
        query = query.join(Customer, Customer.id == Sale.customer_id)
        dimensions = (Customer.id, Customer.name, Customer.tax_id)
        fields = (Customer.name.label('name'), Customer.tax_id.label('tax_id'))
        search = or_(Customer.name.ilike(like_term(q), escape='\\'), Customer.tax_id.ilike(like_term(q), escape='\\'))
    elif tab == 'payments':
        dimensions = (Sale.payment_method,)
        fields = (Sale.payment_method.label('name'),)
        search = None
    else:
        query = query.outerjoin(Category, Category.id == Product.category_id)
        dimensions = (Category.id, Category.name)
        fields = (func.coalesce(Category.name, 'Sin categoría').label('name'),
                  func.count(func.distinct(Product.id)).label('products'))
        search = Category.name.ilike(like_term(q), escape='\\')
    if q and search is not None:
        query = query.filter(search)
    result = query.with_entities(*fields, units.label('units'), sales.label('sales')).group_by(*dimensions)
    rows = [dict(row._mapping) for row in result.order_by(units.desc(), *dimensions).all()]
    if tab == 'payments':
        for row in rows:
            row['name'] = PAYMENT_METHODS.get(row['name'], row['name'])
        if q:
            rows = [row for row in rows if q.lower() in row['name'].lower()]
    return rows


def report(tab: str, params: dict, now=None) -> dict:
    """Agregado completo de una pestaña (solo SELECT). ``rows`` son todas las filas, sin paginar."""
    tab = normalize_tab(tab)
    today = commercial_today(now)
    start, end = period(params, today)
    previous = previous_range(start, end)
    q = _q(params)
    if not response_includes_sale_price():
        return {'tab': tab, 'timezone': 'America/Caracas', 'today': today, 'period': (start, end),
                'comparison_period': previous, 'q': q, 'physical': True,
                'kpis': _physical_totals(start, end), 'rows': _physical_rows(tab, start, end, q)}
    # "Ver detalle" de una categoría filtra por su id exacto, no por coincidencia de texto.
    extra = {'category_id': _category(params)} if tab == 'products' else {}
    data = _BUILDERS[tab](start, end, previous, today, q, **extra)
    return {'tab': tab, 'timezone': 'America/Caracas', 'today': today, 'period': (start, end),
            'comparison_period': previous, 'q': q, **data}
