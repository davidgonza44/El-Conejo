"""Read-only category export: same filters and aggregates as the live listing."""
from datetime import datetime
from decimal import Decimal

from app.services import category_service, export_files, inventory_service
from app.services.exceptions import ValidationError

OPTIONS = {'include_description': ('description', 'Descripción', 50),
           'include_status': ('status', 'Estado', 12),
           'include_products': ('products_count', 'Productos', 12),
           'include_value': ('inventory_value', 'Valor en inventario', 20)}
STATUS_LABELS = {'all': 'Todas', 'active': 'Activas', 'inactive': 'Inactivas',
                 'pending': 'Pendientes', 'approved': 'Aprobadas', 'rejected': 'Rechazadas'}


def columns_for(params):
    """Category name is always present; each checkbox toggles one column ('1'/'0').

    include_value=1 se ignora sin products:view_cost: la columna no sale.
    """
    from app.utils.permissions import response_includes_cost

    show_cost = response_includes_cost()
    columns = [('name', 'Categoría', 32)]
    for key, column in OPTIONS.items():
        value = params.get(key, '1')
        if value not in ('0', '1'):
            raise ValidationError(f"La opción '{key}' debe ser 1 o 0.")
        if key == 'include_value' and not show_cost:
            continue
        if value == '1':
            if key == 'include_value':
                columns.extend([('inventory_value_usd', 'Valor USD', 20), ('inventory_value_ves', 'Valor VES', 20),
                                ('unknown_currency_products', 'Productos sin moneda', 20)])
            else:
                columns.append(column)
    return columns


def category_values(item):
    row = dict(name=item['name'], description=item.get('description') or '',
               status=(('Activa' if item['is_active'] else 'Inactiva') if item['approval_status'] == 'approved'
                       else STATUS_LABELS[item['approval_status']]), products_count=int(item['products_count']))
    if 'inventory_value' in item:
        amounts = {entry['currency']: Decimal(entry['amount']) for entry in item['totals_by_currency']}
        row.update(inventory_value_usd=amounts.get('USD', Decimal('0.00')),
                   inventory_value_ves=amounts.get('VES', Decimal('0.00')),
                   unknown_currency_products=item['unknown_currency_products'])
    return row


def export_categories(format_name, params):
    format_name = export_files.validate_format(format_name)
    columns = columns_for(params)
    status = (params.get('status') or '').strip().lower() or None
    approval = (params.get('approval_status') or '').strip().lower() or None
    result = category_service.list_categories(search=params.get('search'), status=status, sort=params.get('sort'),
                                              approval_status=approval)
    if result['total'] > export_files.MAX_EXPORT_ROWS:
        raise ValidationError(f'La exportación supera {export_files.MAX_EXPORT_ROWS} categorías. Aplique filtros.')
    rows = [category_values(item) for item in result['items']]
    now = datetime.now(inventory_service.COMMERCIAL_ZONE)
    applied = category_service.effective_sort(params.get('sort'))
    parts = [f"Estado: {STATUS_LABELS[status or 'all']}", f"Orden: {applied}"]
    if approval:
        parts.append(f"Aprobación: {STATUS_LABELS[approval]}")
    if (params.get('search') or '').strip():
        parts.insert(0, f"Búsqueda: {params['search'].strip()}")
    lines = [f'Fecha de generación: {now:%d/%m/%Y %H:%M}', 'Filtros: ' + ' · '.join(parts),
             'Campos: ' + ', '.join(label for _, label, _ in columns), f'Categorías: {len(rows)}']
    return export_files.build(format_name, rows, columns, now, sheet='Categorías',
                              title='Listado de categorías', lines=lines, stem='categorias')
