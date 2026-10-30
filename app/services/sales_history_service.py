"""Consultas históricas del reporte. No invoca operaciones comerciales ni hace escrituras."""
from decimal import Decimal

from flask_login import current_user
from sqlalchemy import or_

from app.models import Category, Customer, Product, Sale, SaleLine
from app.models.sale import PAYMENT_METHODS
from app.services.exceptions import NotFoundError, ValidationError
from app.services.sales_common import commercial_today, like_term
from app.services.sales_dashboard_service import _completed, _day, period
from app.utils.permissions import REPORTS_READ, response_includes_sale_price, role_has_permission
from werkzeug.exceptions import Forbidden

KINDS = {'customers', 'products', 'payments', 'all'}
PAGE_SIZE = 20
ZERO = Decimal('0.00')


def _parameters(kind, key, params):
    if kind not in KINDS or (kind == 'all' and key is not None):
        raise NotFoundError('Detalle histórico no encontrado.')
    if kind in ('customers', 'products'):
        if not str(key).isascii() or not str(key).isdigit() or len(str(key)) > 10 or not 1 <= int(key) <= 2147483647:
            raise NotFoundError('Detalle histórico no encontrado.')
        key = int(key)
    if kind == 'payments' and (not key or len(key) > 20):
        raise NotFoundError('Método histórico no encontrado.')
    tab = params.get('tab', kind if kind != 'all' else 'summary')
    if tab not in ('summary', 'customers', 'products', 'payments', 'categories'):
        raise ValidationError('Pestaña inválida.')
    if kind != 'all' and tab != kind:
        raise ValidationError('El detalle no corresponde a la pestaña.')
    start, end = period(params, commercial_today())
    page = params.get('page', '1')
    if not str(page).isascii() or not str(page).isdigit() or len(str(page)) > 7 or int(page) < 1:
        raise ValidationError('Página inválida.')
    q = (params.get('q') or '').strip()
    if len(q) > 100:
        raise ValidationError('El filtro no puede superar 100 caracteres.')
    return key, tab, start, end, int(page), q


def _query(kind, key, tab, start, end, q, money):
    query = (_completed(start, end).join(Customer, Customer.id == Sale.customer_id)
             .outerjoin(SaleLine, SaleLine.sale_id == Sale.id)
             .outerjoin(Product, Product.id == SaleLine.product_id)
             .outerjoin(Category, Category.id == Product.category_id))
    if kind == 'customers':
        query = query.filter(Sale.customer_id == key)
    elif kind == 'products':
        query = query.filter(SaleLine.product_id == key)
    elif kind == 'payments':
        query = query.filter(Sale.payment_method == key)
    if q and tab in ('customers', 'products', 'categories'):
        fields = {'customers': (Customer.name, Customer.tax_id),
                  'products': (Product.name, Product.code, Category.name),
                  'categories': (Category.name,)}[tab]
        query = query.filter(or_(*(field.ilike(like_term(q), escape='\\') for field in fields)))
    if q and tab == 'payments':
        # Include legacy codes actually stored in the selected authorized period.
        methods = _completed(start, end).with_entities(Sale.payment_method).distinct().all()
        matching = [method for (method,) in methods if q.lower() in PAYMENT_METHODS.get(method, method).lower()]
        query = query.filter(Sale.payment_method.in_(matching))
    columns = [Sale.id.label('sale_id'), Sale.sale_number.label('number'), Sale.sold_at,
               Sale.payment_method.label('method'), Customer.name.label('customer'),
               Customer.tax_id, SaleLine.id.label('line_id'), SaleLine.product_id,
               SaleLine.product_code.label('code'), SaleLine.product_name.label('product'),
               SaleLine.quantity, Product.name.label('current_product'), Category.name.label('category')]
    if money:
        columns.extend([Sale.total.label('sale_total'), SaleLine.line_total.label('line_total')])
    return query.with_entities(*columns).order_by(Sale.sold_at.desc(), Sale.id.desc(), SaleLine.id)


def _add_product(products, row, money):
    if row['line_id'] is None:
        return
    key = (row['product_id'], row['code'], row['product'])
    product = products.setdefault(key, {'code': row['code'], 'name': row['product'], 'units': 0, 'amount': ZERO})
    product['units'] += row['quantity'] or 0
    if money:
        product['amount'] += row['line_total'] or ZERO


def _add_operation(operations, monthly, row, day, line_basis, money):
    first = row['sale_id'] not in operations
    operation = operations.setdefault(row['sale_id'], {
        'number': row['number'], 'date': day, 'customer': row['customer'],
        'method': PAYMENT_METHODS.get(row['method'], row['method']), 'units': 0, 'amount': ZERO})
    monthly['operations'].add(row['sale_id'])
    units = row['quantity'] or 0
    operation['units'] += units
    monthly['units'] += units
    if money:
        value = (row['line_total'] or ZERO) if line_basis else (row['sale_total'] if first else ZERO)
        operation['amount'] += value
        monthly['amount'] += value


def _summarize(rows, line_basis, money):
    operations, months, products = {}, {}, {}
    for row in rows:
        day = _day(row['sold_at']).isoformat()
        month = day[:7]
        entry = months.setdefault(month, {'period': month, 'operations': set(), 'units': 0, 'amount': ZERO})
        _add_operation(operations, entry, row, day, line_basis, money)
        _add_product(products, row, money)
    monthly = [{**value, 'operations': len(value['operations'])} for _, value in sorted(months.items())]
    return list(operations.values()), monthly, list(products.values())


def detail(kind, key, params):
    """Autorización y ámbito antes de consultar. Sin importaciones ni datos bancarios."""
    if not current_user.is_authenticated or not role_has_permission(current_user.role, REPORTS_READ):
        raise Forbidden()
    key, tab, start, end, page, q = _parameters(kind, key, params)
    money = response_includes_sale_price()
    rows = [dict(row._mapping) for row in _query(kind, key, tab, start, end, q, money).all()]
    line_basis = kind == 'products' or (kind == 'all' and tab in ('products', 'categories'))
    operations, monthly, products = _summarize(rows, line_basis, money)
    title = {'customers': 'Histórico del cliente', 'products': 'Histórico del producto',
             'payments': 'Histórico del método de pago', 'all': 'Operaciones históricas'}[kind]
    identity, subtitle = 'Sin operaciones en el período y ámbito seleccionados', ''
    if rows:
        first = rows[0]
        if kind == 'customers':
            identity, subtitle = first['customer'], first['tax_id'] or 'Sin identificación fiscal registrada'
        elif kind == 'products':
            identity = first['current_product'] or first['product']
            subtitle = 'Categoría actual: ' + (first['category'] or 'Sin categoría')
        elif kind == 'payments':
            identity = PAYMENT_METHODS.get(first['method'], first['method'])
        else:
            identity = 'Histórico de ventas del reporte'
    count = len(operations)
    pages = max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages)
    return {'kind': kind, 'key': key, 'tab': tab, 'start': start.isoformat(), 'end': end.isoformat(), 'q': q,
            'title': title, 'identity': identity, 'subtitle': subtitle, 'money': money, 'line_basis': line_basis,
            'count': count, 'units': sum(item['units'] for item in operations),
            'amount': sum((item['amount'] for item in operations), ZERO) if money else None,
            'line_amount': sum((item['amount'] for item in products), ZERO) if money else None,
            'monthly': monthly, 'products': products, 'page': page, 'pages': pages,
            'operations': operations[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]}
