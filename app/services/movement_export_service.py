"""Read-only export of stock movements honouring the live list filters.

Reference and status values use the shared derived backend presentation. Existing
export columns are preserved. UTC timestamps are shown in America/Caracas time.
"""
from datetime import datetime, timezone

from app.extensions import db
from app.models import User
from app.services import export_files, inventory_service
from app.services.exceptions import ValidationError

LABELS = {'entrada': 'Entrada', 'salida': 'Salida', 'ajuste': 'Ajuste'}
COLUMNS = [('created_at', 'Fecha', 18), ('movement_type', 'Tipo', 10), ('reference', 'Referencia', 16),
           ('product', 'Producto', 34), ('code', 'Código', 14), ('quantity', 'Cantidad', 10),
           ('previous_stock', 'Stock anterior', 12), ('new_stock', 'Stock actual', 12),
           ('user', 'Responsable', 22), ('reason', 'Motivo', 36)]


def local_time(value):
    return value.replace(tzinfo=timezone.utc).astimezone(inventory_service.COMMERCIAL_ZONE).replace(tzinfo=None)


def signed_quantity(movement):
    if movement.movement_type == 'entrada':
        return movement.quantity
    if movement.movement_type == 'salida':
        return -movement.quantity
    return movement.new_stock - movement.previous_stock


def movement_values(movement, presentation=None):
    if presentation is None:
        presentation = inventory_service.movement_presentations([movement])[movement.id]
    product = movement.product
    return dict(created_at=local_time(movement.created_at), movement_type=LABELS.get(movement.movement_type, 'Tipo no soportado'),
                reference=presentation['display_reference'] or '—', display_status=presentation['display_status'],
                product=product.name if product else 'Producto no disponible',
                code=product.code if product else '', quantity=signed_quantity(movement),
                previous_stock=movement.previous_stock, new_stock=movement.new_stock,
                user=movement.user.name if movement.user else 'No registrado', reason=movement.reason or '')


def filter_lines(filters, params):
    parts = []
    if filters.get('search'):
        parts.append(f"Búsqueda: {filters['search']}")
    if filters.get('movement_type'):
        parts.append(f"Tipo: {LABELS[filters['movement_type']]}")
    if filters.get('user_id'):
        user = db.session.get(User, filters['user_id'])
        parts.append(f"Responsable: {user.name if user else 'No registrado'}")
    for key, label in (('date_from', 'Desde'), ('date_to', 'Hasta')):
        if filters.get(key):
            parts.append(f"{label}: {filters[key]:%d/%m/%Y}")
    if params.get('sort'):
        parts.append(f"Orden: {params['sort']}")
    return 'Filtros: ' + ' · '.join(parts) if parts else 'Filtros: ninguno (todos los movimientos)'


def movement_rows(params):
    """(rows, filter summary, generation time) for ALL matching movements, never a page."""
    params = {key: value for key, value in params.items() if key not in ('page', 'per_page', 'limit') and value != ''}
    filters, query, order = inventory_service.sorted_movements_query(params)
    movements = query.order_by(*order).limit(export_files.MAX_EXPORT_ROWS + 1).all()
    if len(movements) > export_files.MAX_EXPORT_ROWS:
        raise ValidationError(f'La exportación supera {export_files.MAX_EXPORT_ROWS} movimientos. Aplique filtros para reducir el historial.')
    presentations = inventory_service.movement_presentations(movements)
    return [movement_values(m, presentations[m.id]) for m in movements], filter_lines(filters, params), datetime.now(inventory_service.COMMERCIAL_ZONE)


def export_movements(format_name, params):
    format_name = export_files.validate_format(format_name)
    rows, summary, now = movement_rows(params)
    lines = [f'Fecha de generación: {now:%d/%m/%Y %H:%M}', summary, f'Movimientos: {len(rows)}']
    return export_files.build(format_name, rows, COLUMNS, now, sheet='Movimientos',
                              title='Movimientos de Inventario', lines=lines, stem='movimientos_inventario')
